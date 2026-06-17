"""The stage DAG, extracted from the CLI (FIX F03/F91). advance() runs the deterministic
chain as far as it can and PAUSES at each agent gate (moments, captions). EVERY per-unit stage
call is wrapped so one bad source/moment/clip goes to `error` and is skipped — it never wedges
the whole pass (FIX F03). Returns counts + awaiting{moments,captions}."""
from __future__ import annotations
from datetime import datetime, timezone
from fanops.config import Config
from fanops.errors import AuthError
from fanops.ledger import Ledger
from fanops.models import (SourceState, MomentState, ClipState, PostState, Fmt, PLATFORM_ASPECT)
from fanops.accounts import Accounts
from fanops.ingest import ingest_drops
from fanops.transcribe import transcribe_source
from fanops.signals import detect_signals
from fanops.moments import request_moments, ingest_moments
from fanops.hookedit import request_hook_edit, ingest_hook_edit, hook_edit_pending
from fanops.hookjudge import request_hook_judge, ingest_hook_judge, hook_judge_pending
from fanops.clip import render_aspects_for
from fanops.caption import request_captions, ingest_captions
from fanops.crosspost import crosspost_clips
from fanops.post.run import publish_due
from fanops.reconcile import reconcile_posts
from fanops.digest import write_digest
from fanops.log import get_logger
from fanops.agentstep import pending
from fanops.timeutil import parse_iso

def _aspects_for(accts: Accounts) -> set[Fmt]:
    return {PLATFORM_ASPECT.get(s.platform, Fmt.r9x16) for s in accts.surfaces()} or {Fmt.r9x16}

def _parse(ts):
    # Parse an ISO-8601 scheduled_time (may carry a 'Z') into an aware datetime, or None if
    # absent/unparseable — never raises, so the heartbeat age computation can't crash a pass.
    # Defensive None/except wrapper around the shared strict parse_iso (audit (i)).
    try:
        return parse_iso(ts) if ts else None
    except Exception:
        return None

def _prewarm(cfg: Config, aspects: set[Fmt], log) -> None:
    """Phase D: run ONLY the slow subprocess stages (whisper / ffmpeg signals / ffmpeg render) with NO
    ledger lock held, against a THROWAWAY ledger, so they populate their deterministic on-disk artifacts
    (transcript JSON, signals sidecar, clip mp4 + render fingerprint). The authoritative transaction in
    advance() then re-runs the same stages, which SKIP the now-warm subprocess and only flip ledger
    state under a short lock — keeping the multi-minute transcodes OUT of the lock (the LockBusyError
    starvation hit live). Writes NO gate requests and saves NO ledger state; only the on-disk artifacts
    persist. Fail-open per unit: a warm miss/error just means that stage runs inside the lock (today's
    behavior), never a crash."""
    try:
        led = Ledger.load(cfg)
    except Exception as e:
        log("prewarm", "-", "warn", err=str(e)[:120]); return
    for s in list(led.sources.values()):
        try:
            if s.state is SourceState.catalogued:
                led = transcribe_source(led, cfg, s.id)
            if led.sources[s.id].state is SourceState.transcribed:
                led = detect_signals(led, cfg, s.id)
        except Exception as e:                            # fail-open: the commit pass retries in-lock
            log("prewarm", s.id, "warn", err=str(e)[:120])
    # Finalize hooks on the throwaway ledger (ingest_hook_edit only READS the response + mutates the
    # ledger — no disk side effects) so a warmed render's fingerprint matches the in-lock render and the
    # commit skips ffmpeg. Mirror advance()'s render gate so we don't warm a hook the editor will rewrite.
    hold_edit = hold_judge = False
    if cfg.hook_editor:
        try:
            led = ingest_hook_edit(led, cfg)
            if cfg.responder_mode == "llm":
                hold_edit = hook_edit_pending(led, cfg)
        except Exception:
            hold_edit = False
    if cfg.hook_judge:                                    # Phase 3 critic: don't warm a render of a hook
        try:                                             # the judge may reject (fingerprint would mismatch)
            led = ingest_hook_judge(led, cfg)
            if cfg.responder_mode == "llm":
                hold_judge = hook_judge_pending(led, cfg)
        except Exception:
            hold_judge = False
    for m in list(led.moments.values()):
        if m.state is MomentState.decided and not (hold_edit and not m.hook_edited) \
                and not (hold_judge and not m.hook_judged):
            try:
                led, _ = render_aspects_for(led, cfg, m.id, aspects=aspects)
            except Exception as e:
                log("prewarm", m.id, "warn", err=str(e)[:120])

def advance(cfg: Config, *, base_time: str) -> dict:
    accts = Accounts.load(cfg)
    log = get_logger(cfg)
    aspects = _aspects_for(accts)

    # Phase D: ingest in a SHORT transaction FIRST so a brand-new drop is catalogued and VISIBLE to the
    # lock-free pre-warm below — otherwise its transcribe would run inside the main lock. ingest_drops is
    # idempotent (content-addressed dedup), so this never double-catalogues.
    with Ledger.transaction(cfg) as led:
        led = ingest_drops(led, cfg)
    # Phase D: warm the slow subprocess stages with NO lock held (see _prewarm). The main transaction
    # then re-runs them and they skip on the warm artifacts — so a render no longer starves a concurrent
    # Studio write / second pass. Lock-free; saves nothing; fail-open.
    _prewarm(cfg, aspects, log)

    # AUDIT B4: the load-mutate-save COMMIT runs inside ONE ledger transaction — the lock is acquired
    # BEFORE load and the single save happens on clean exit. This closes the lost-update window the
    # save()-only lock left open (two overlapping cron passes both loaded a stale snapshot; last save()
    # won; the other's updates — a published post, a submitting flip — vanished silently). A second live
    # pass is excluded (typed LockBusyError, bounded by timeout), not silently overwritten. (Phase D: the
    # SLOW subprocesses already ran lock-free above; this transaction only flips state + does the cheap
    # gate/crosspost/publish work, so the lock-held window is short.)
    with Ledger.transaction(cfg) as led:
        # B5/E2: snapshot the already-published post ids at transaction ENTRY so the summary's
        # published_in_run is a THIS-RUN delta — a post already published when the pass opened is in
        # `before` and is NOT counted (set difference against the exit state). Ingest already ran (above)
        # and never publishes, so the snapshot here is the correct baseline.
        before = {p.id for p in led.posts_in_state(PostState.published)}

        # transcribe -> signals -> request moments (per source), each quarantined
        for s in list(led.sources.values()):
            try:
                if s.state is SourceState.catalogued:
                    led = transcribe_source(led, cfg, s.id)
                if led.sources[s.id].state is SourceState.transcribed:
                    led = detect_signals(led, cfg, s.id)
                if led.sources[s.id].state is SourceState.signalled:
                    led = request_moments(led, cfg, s.id)
            except Exception as e:
                led.sources[s.id].state = SourceState.error
                led.sources[s.id].error_reason = f"{type(e).__name__}: {e}"
                log("source", s.id, "error", err=str(e)[:120])

        # ingest decided moments -> render aspects -> request captions
        for s in list(led.sources.values()):
            if s.state is SourceState.moments_requested:
                try:
                    led = ingest_moments(led, cfg, s.id)
                except Exception as e:
                    led.sources[s.id].state = SourceState.error
                    led.sources[s.id].error_reason = f"{type(e).__name__}: {e}"
                    log("moments", s.id, "error", err=str(e)[:120])
        # Feed-aware hook editor (opt-in, fail-open): one pass over the WHOLE feed of decided hooks
        # rewrites the weak/duplicated/templated ones into strong, DISTINCT hooks BEFORE any clip
        # burns one — the per-clip moment responder answers in isolation and cannot diversify across
        # the feed. Gated on cfg.hook_editor AND the llm responder (the only path that can answer the
        # gate; without it the gate would never clear and HOLD rendering forever). DEFAULT OFF -> this
        # block is skipped entirely and rendering is byte-identical to today.
        hold_hooks = hold_judge = False
        if cfg.hook_editor:
            try:
                # ALWAYS consume an already-written answer (review HIGH): if the operator flips
                # FANOPS_RESPONDER llm->manual while a gate is pending, ingest must still apply the
                # editor's answer rather than orphan it. Only REQUEST + HOLD on the llm path — the
                # only responder that can answer the gate; holding for an unanswerable (manual) gate
                # would wedge rendering forever.
                if cfg.responder_mode == "llm":
                    led = request_hook_edit(led, cfg)    # open the feed gate (idempotent per feed set)
                led = ingest_hook_edit(led, cfg)         # apply the editor's answer whenever it lands
                if cfg.responder_mode == "llm":
                    hold_hooks = hook_edit_pending(led, cfg)  # still unanswered -> HOLD rendering this pass
            except Exception as e:                       # fail-open: never let the editor wedge a pass
                log("hookedit", "-", "error", err=str(e)[:120])
                hold_hooks = False
        # Specificity critic (Phase 3, opt-in via cfg.hook_judge): runs AFTER the editor on each finalized
        # hook and REJECTS a generic/unanchored one to a clean clip. Same gate contract as the editor —
        # request + HOLD only on the llm path (the only responder that can answer); ingest always applies
        # a written verdict. Fail-open: a critic error never wedges a pass (hold_judge stays False).
        if cfg.hook_judge:
            try:
                if cfg.responder_mode == "llm":
                    led = request_hook_judge(led, cfg)   # open the critic gate (idempotent per batch set)
                led = ingest_hook_judge(led, cfg)        # apply the critic's verdicts whenever they land
                if cfg.responder_mode == "llm":
                    hold_judge = hook_judge_pending(led, cfg)  # unanswered -> HOLD rendering this pass
            except Exception as e:
                log("hookjudge", "-", "error", err=str(e)[:120])
                hold_judge = False
        for m in list(led.moments.values()):
            if m.state is MomentState.decided:
                if hold_hooks and not m.hook_edited:
                    continue                             # wait for the feed editor before burning this hook
                if hold_judge and not m.hook_judged:
                    continue                             # wait for the critic's verdict before burning
                try:
                    led, clips = render_aspects_for(led, cfg, m.id, aspects=aspects)
                    for clip in clips:
                        led = request_captions(led, cfg, clip.id,
                                               [(s.account, s.platform) for s in accts.surfaces()],
                                               accounts=accts)
                except Exception as e:
                    led.moments[m.id].state = MomentState.error
                    led.moments[m.id].error_reason = f"{type(e).__name__}: {e}"
                    log("clip", m.id, "error", err=str(e)[:120])

        # ingest captions -> crosspost -> publish due
        for c in list(led.clips.values()):
            if c.state is ClipState.captions_requested:
                try:
                    led = ingest_captions(led, cfg, c.id)
                except Exception as e:
                    led.clips[c.id].state = ClipState.error
                    led.clips[c.id].error_reason = f"{type(e).__name__}: {e}"
                    log("caption", c.id, "error", err=str(e)[:120])
        # AUDIT M2: the volatile crosspost/publish stages run inside the transaction. Each is
        # wrapped so a raise does NOT abandon the whole pass's in-memory progress before the
        # exit-save — an uncaught raise inside the with-block skips transaction()'s save and rolls
        # back to the prior on-disk snapshot, silently losing this pass's completed transitions.
        # The wrap mirrors the per-unit quarantine of the loops above (log + continue). The ONE
        # exception we deliberately let escape is a FATAL AuthError from publish_due (Blotato or
        # Postiz): a bad key fails every post, so halting + rolling back the pass is the intended F52
        # behavior, handled cleanly by the CLI's run guard. (publish_due also isolates per-post —
        # incl. a malformed scheduled_time, review finding — so this stage-level wrap is a
        # defense-in-depth net for any unforeseen non-auth raise, not the primary isolation.)
        try:
            led = crosspost_clips(led, cfg, accts, base_time=base_time)
        except AuthError:
            raise                                        # F52: a fatal auth error halts (symmetry
            # with publish_due below). crosspost has no Blotato call today, but if one is ever added
            # (e.g. pre-flight account validation) a bad key must halt, not be logged-and-continued.
        except Exception as e:
            log("crosspost", "-", "error", err=str(e)[:120])
        # Reconcile last pass's stranded posts BEFORE publishing this pass (AUDIT H4): resolve any
        # submitting/needs_reconcile post that has a submission_id via GET /v2/posts/:id. This is the
        # BLOTATO reconciler (BlotatoStatusClient) — gated on is_live_backend AND an explicit Blotato
        # backend check: M2 made is_live_backend True for postiz too, but postiz has NO status API, so
        # the reconciler must run ONLY for rest/mcp+key. The postiz backend has no automated status
        # reconciler yet: its needs_reconcile posts are NOT silent (surfaced in `fanops status` + the
        # digest) and are cleared by the backend-agnostic `fanops resolve <post_id> published|failed`
        # recovery verb, exactly as id-less Blotato posts already are. (dryrun never produces these.)
        reconcilable = (led.posts_in_state(PostState.submitting)
                        + led.posts_in_state(PostState.submitted)
                        + led.posts_in_state(PostState.needs_reconcile))
        if reconcilable and cfg.is_live_backend and cfg.poster_backend in ("rest", "mcp"):
            try:
                led = reconcile_posts(led, cfg)
            except Exception as e:                       # status API hiccup must not wedge the pass
                log("reconcile", "-", "error", err=str(e)[:120])
        # publish cutoff = real now (base_time is the SCHEDULE anchor for crosspost; publishing uses
        # actual now). in_transaction=True so publish_due's crash-safe mid-loop saves use the
        # UNLOCKED save and don't self-deadlock against the transaction's held lock (AUDIT B4/B2).
        # AUDIT M2 net: a non-auth raise here must not roll back the pass; a FATAL AuthError (Blotato
        # or Postiz) MUST still escape (F52 — a bad key fails every post; halt + roll back, CLI exits clean).
        try:
            led = publish_due(led, cfg, now=None, in_transaction=True)
        except AuthError:
            raise                                        # F52: halt the run on a bad key
        except Exception as e:
            log("publish", "-", "error", err=str(e)[:120])

        # B5/E2 heartbeat inputs: published_in_run is the set-difference of published ids at EXIT
        # vs `before` (THIS-RUN delta); last_published_age_hours is the age (hours, 2dp) of the
        # newest published post's scheduled_time vs now, or None when none has a parseable time.
        after = led.posts_in_state(PostState.published)
        published_in_run = len([p for p in after if p.id not in before])
        newest = max((_parse(p.scheduled_time) for p in after if p.scheduled_time), default=None)
        last_published_age_hours = (None if newest is None
                                    else round((datetime.now(timezone.utc) - newest).total_seconds() / 3600, 2))
        summary = {
            "sources": len(led.sources), "moments": len(led.moments),
            "clips": len(led.clips), "posts": len(led.posts),
            "published": len(led.posts_in_state(PostState.published)),
            "failed": len(led.posts_in_state(PostState.failed)),
            # B5/E2: this-run published delta + newest published age, for the heartbeat monitor.
            "published_in_run": published_in_run,
            "last_published_age_hours": last_published_age_hours,
            # needs_reconcile (AUDIT C1): ambiguous publish failures parked for human reconcile —
            # may be live on the platform, must NOT be blindly re-queued. Surfaced here so the
            # unattended operator sees it in `fanops run`/`advance` output, not only the digest.
            "needs_reconcile": len(led.posts_in_state(PostState.needs_reconcile)),
            "holds": sum(1 for c in led.clips.values() if c.held),
            "errors": sum(1 for s in led.sources.values() if s.state is SourceState.error),
            "awaiting": {"moments": len(pending(cfg, kind="moments")),
                         "captions": len(pending(cfg, kind="captions")),
                         "hookedit": len(pending(cfg, kind="hookedit"))},
        }
    # digest is read-only reporting: build it from the just-committed ledger, OUTSIDE the lock, so
    # the slow markdown render never extends the lock-held window (it would block an overlapping
    # pass longer than the actual mutation requires).
    write_digest(Ledger.load(cfg), cfg)
    return summary
