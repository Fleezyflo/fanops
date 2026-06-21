"""The stage DAG, extracted from the CLI (FIX F03/F91). advance() runs the deterministic
chain as far as it can and PAUSES at each agent gate (moments, captions). EVERY per-unit stage
call is wrapped so one bad source/moment/clip goes to `error` and is skipped — it never wedges
the whole pass (FIX F03). Returns counts + awaiting{moments,captions}."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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
from fanops.hookscore import log_hook_quality
from fanops.router import route_moments
from fanops.stitch_render import (mine_suggestions, render_approved_stitches,
                                  prewarm_approved_stitches, approved_disabled_count)
from fanops.intro_match import request_intro_match, ingest_intro_match
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

def _enabled_strategies(cfg: Config) -> set[str]:
    """The structural-hook formats turned ON this pass — the per-format gate the producers + render honor
    (a disabled format produces/renders nothing; its approved plans freeze). Empty set -> the whole block
    is skipped and behavior is byte-identical to pre-structural-hooks."""
    return {k for k, on in (("impact_cut", cfg.impact_cut), ("intro_tease", cfg.intro_tease)) if on}

def _parse(ts):
    # Parse an ISO-8601 scheduled_time (may carry a 'Z') into an aware datetime, or None if
    # absent/unparseable — never raises, so the heartbeat age computation can't crash a pass.
    # Defensive None/except wrapper around the shared strict parse_iso (audit (i)).
    try:
        return parse_iso(ts) if ts else None
    except Exception:
        return None

def _prewarm(cfg: Config, aspects: set[Fmt], log) -> None:
    """Pre-warm dispatcher (parallel-source pipeline). DEFAULT OFF -> the EXACT existing sequential
    body below (byte-identical; the pool is never constructed). With FANOPS_CONCURRENT_SOURCES on,
    the slow per-source subprocess stages run in a bounded thread pool instead — see _prewarm_concurrent.
    Either path leaves the SAME on-disk artifacts warm; the main transaction in advance() (the single
    serial reduce / one writer) re-runs the stages in-lock and skips on the warm artifacts exactly as
    today, so the committed ledger state is identical regardless of this flag."""
    if cfg.concurrent_sources:
        _prewarm_concurrent(cfg, aspects, log); return
    _prewarm_sequential(cfg, aspects, log)

@dataclass(frozen=True)
class SourceResult:
    """The PURE result of a worker's per-source produce pass — a source id + an optional error reason.
    A worker NEVER mutates a shared ledger / calls save / opens a transaction; it loads its own private
    throwaway snapshot, runs the slow stages (writing only deterministic on-disk artifacts), and returns
    one of these. The main transaction (the only writer) re-derives the real state in-lock."""
    source_id: str
    error_reason: str | None = None

def _produce_source(cfg: Config, source_id: str, aspects: set[Fmt], *, log) -> SourceResult:
    """Worker body for ONE source: load a PRIVATE throwaway Ledger.load(cfg) snapshot, run the slow
    subprocess chain (transcribe -> detect_signals) on this source, then render THIS source's decided
    moments — warming the exact same on-disk artifacts (transcript JSON, signals sidecar, clip mp4 +
    fingerprint) the sequential _prewarm warms today. Mirrors _prewarm's per-unit fail-open quarantine
    but RETURNS a SourceResult instead of mutating in place. NEVER calls led.save()/_save_unlocked/
    Ledger.transaction — the private in-memory ledger is discarded; only the artifacts + the result
    survive. The on-screen hook is final at decision time (vision author + is_weak_hook floor), so a
    decided moment's render fingerprint is stable — no feed-level hook stage to wait on (determinism)."""
    err: str | None = None
    try:
        led = Ledger.load(cfg)
    except Exception as e:
        log("prewarm", source_id, "warn", err=str(e)[:120]); return SourceResult(source_id, str(e)[:120])
    s = led.sources.get(source_id)
    if s is None or s.origin_kind == "third_party":
        return SourceResult(source_id, None)              # gone / inert — nothing to warm
    try:
        if s.state is SourceState.catalogued:
            led = transcribe_source(led, cfg, source_id)
        if led.sources[source_id].state is SourceState.transcribed:
            led = detect_signals(led, cfg, source_id)
    except Exception as e:                                # fail-open: the commit pass retries in-lock
        log("prewarm", source_id, "warn", err=str(e)[:120]); err = f"{type(e).__name__}: {e}"
    for m in list(led.moments.values()):
        if m.parent_id != source_id: continue             # render only THIS source's moments (disjoint paths)
        if m.state is MomentState.decided:
            try:
                led, _ = render_aspects_for(led, cfg, m.id, aspects=aspects)
            except Exception as e:
                log("prewarm", m.id, "warn", err=str(e)[:120])
    return SourceResult(source_id, err)

def _prewarm_concurrent(cfg: Config, aspects: set[Fmt], log) -> None:
    """Parallel map phase (parallel-source pipeline): warm each source's slow subprocess artifacts in a
    bounded ThreadPoolExecutor instead of one-source-at-a-time, so a long video no longer head-of-line
    blocks the queue. The pool produces ONLY artifacts + pure SourceResults — NO worker touches the
    ledger (the one-writer rule). The throwaway ledger here is loaded ONLY to snapshot the eligible
    source ids. The stitch prewarm tail stays serial. The single main transaction in advance() is the
    reduce (the only writer) and is UNCHANGED — it re-runs the stages in-lock and skips on the warm artifacts."""
    try:
        led = Ledger.load(cfg)
    except Exception as e:
        log("prewarm", "-", "warn", err=str(e)[:120]); return
    ids = [s.id for s in led.sources.values() if s.origin_kind != "third_party"]   # M1: third-party assets are INERT
    if ids:
        with ThreadPoolExecutor(max_workers=cfg.concurrent_workers) as ex:   # bound = rate-limit guardrail, not correctness
            futs = [ex.submit(_produce_source, cfg, sid, aspects, log=log) for sid in ids]
            for fut in as_completed(futs):
                try: fut.result()                         # each worker already fail-opens; this guards a thread-level crash
                except Exception as e: log("prewarm", "-", "warn", err=f"worker crash: {type(e).__name__}: {str(e)[:120]}")
    # M4/M6 structural-hooks stitch prewarm stays SERIAL (independent of the per-source map; warms
    # operator-APPROVED stitch renders lock-free), mirroring _prewarm_sequential's tail.
    strategies = _enabled_strategies(cfg)
    if strategies:
        try: prewarm_approved_stitches(led, cfg, log, strategies=strategies)   # the mutated coordinator led (matches _prewarm_sequential)
        except Exception as e: log("prewarm", "-", "warn", err=str(e)[:120])

def _prewarm_sequential(cfg: Config, aspects: set[Fmt], log) -> None:
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
        if s.origin_kind == "third_party": continue       # M1: third-party assets are INERT to clip-production
        try:
            if s.state is SourceState.catalogued:
                led = transcribe_source(led, cfg, s.id)
            if led.sources[s.id].state is SourceState.transcribed:
                led = detect_signals(led, cfg, s.id)
        except Exception as e:                            # fail-open: the commit pass retries in-lock
            log("prewarm", s.id, "warn", err=str(e)[:120])
    for m in list(led.moments.values()):
        if m.state is MomentState.decided:
            try:
                led, _ = render_aspects_for(led, cfg, m.id, aspects=aspects)
            except Exception as e:
                log("prewarm", m.id, "warn", err=str(e)[:120])
    # M4/M6 structural-hooks: warm the heavy render for operator-APPROVED stitches here, lock-free (impact-cut
    # ffmpeg + intro-tease MoviePy), so the in-lock commit adopts the warm mp4 via the fingerprint-skip —
    # keeping the transcode OUT of the ledger lock (PRD: "approval gates the render, which runs lock-free").
    strategies = _enabled_strategies(cfg)
    if strategies:
        try: prewarm_approved_stitches(led, cfg, log, strategies=strategies)   # fail-open per unit (mirror the concurrent path); a prewarm/[compose]-ImportError must NOT crash advance()
        except Exception as e: log("prewarm", "-", "warn", err=str(e)[:120])

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
            if s.origin_kind == "third_party": continue   # M1: third-party assets are INERT to clip-production
            try:
                if s.state is SourceState.catalogued:
                    led = transcribe_source(led, cfg, s.id)
                if led.sources[s.id].state is SourceState.transcribed:
                    led = detect_signals(led, cfg, s.id)
                if led.sources[s.id].state is SourceState.signalled:
                    led = request_moments(led, cfg, s.id, accounts=accts)   # P4(c): proven-hook STYLE block
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
        # Task 9 scoreboard: one read-only digest line of hook quality (null/viewer_pov_rate) on EVERY
        # pass. viewer_pov_rate (narration_signature) measures the FINAL on-screen hooks the vision
        # author wrote — independent of any subsystem flag — so the operator's hook-quality visibility
        # stays on by default (it rode the now-deleted editor/critic flags before). Read-only + fail-open.
        try: log_hook_quality(led, cfg)
        except Exception as e: log("hookscore", "-", "error", err=str(e)[:120])
        # M2 structural-hooks router (opt-in, observe-only): classify each decided hook into a
        # hook_strategy reason BEFORE the render loop. Renders nothing; a router error never wedges the
        # pass (fail-open). Default OFF -> byte-identical to today.
        if cfg.hook_router:
            try:
                led = route_moments(led, cfg)
            except Exception as e:
                log("router", "-", "error", err=str(e)[:120])
        for m in list(led.moments.values()):
            if m.state is MomentState.decided:
                try:
                    led, clips = render_aspects_for(led, cfg, m.id, aspects=aspects)
                    for clip in clips:
                        if clip.state is not ClipState.rendered: continue   # a failed-aspect clip (ClipState.error) must not be laundered into a phantom captioned post with a dangling mp4
                        led = request_captions(led, cfg, clip.id,
                                               [(s.account, s.platform) for s in accts.surfaces()],
                                               accounts=accts)
                except Exception as e:
                    led.moments[m.id].state = MomentState.error
                    led.moments[m.id].error_reason = f"{type(e).__name__}: {e}"
                    log("clip", m.id, "error", err=str(e)[:120])
        # M4/M5/M6 structural-hooks: after the bare clips render, run the per-format producers + render the
        # operator-approved plans. intro_tease first OPENS its LLM-vision matcher gate (request) + applies any
        # landed pairings (ingest) so mine_suggestions can pair them. Ledger-only mutation here (safe in-lock);
        # the heavy approved-plan RENDER is lock-free in the prewarm pass. Per-format opt-in + fail-open: a
        # producer error never wedges a pass. Both formats OFF -> byte-identical to pre-structural-hooks.
        strategies = _enabled_strategies(cfg)
        if strategies:
            try:
                if cfg.intro_tease:                          # M6: the matcher gate (fail-open; no answer -> no plan)
                    led = request_intro_match(led, cfg)
                    led = ingest_intro_match(led, cfg)
                led = mine_suggestions(led, cfg, log, strategies=strategies)   # ranked, top-N-capped, multi-strategy
                led = render_approved_stitches(led, cfg, strategies=strategies)  # adopts prewarmed mp4s
            except Exception as e:
                log("structural_hooks", "-", "error", err=str(e)[:120])
        # Forward-only kill-switch (PRD): an approved plan of a DISABLED format is NOT rendered and NOT
        # retracted — but never a SILENT freeze. Log the count so the operator knows it renders on re-enable.
        n = approved_disabled_count(led, enabled=strategies)
        if n:
            log("structural_hooks", "-", "warn",
                err=f"{n} approved plans for disabled formats (feature OFF) — will render when re-enabled")

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
        # submitting/needs_reconcile post that has a submission_id. The reconciler is backend-agnostic
        # (reconcile.py dispatches per backend): Blotato (rest/mcp) over GET /v2/posts/:id; Postiz (P2)
        # over the date-windowed GET /public/v1/posts `state` field. Gated on is_live_backend (key
        # present) AND a known-live backend — dryrun never produces these and key-less postiz is not
        # live. Each daemon fire thus publishes due posts AND heals parked ones. `fanops resolve` stays
        # the manual escape hatch (e.g. a Postiz post genuinely absent from its page -> 'unknown', parked).
        reconcilable = (led.posts_in_state(PostState.submitting)
                        + led.posts_in_state(PostState.submitted)
                        + led.posts_in_state(PostState.needs_reconcile))
        if reconcilable and cfg.is_live_backend and cfg.poster_backend in ("rest", "mcp", "postiz"):
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
            # V2 M1/F9: clips that rendered but silently lost their on-screen hook (couldn't burn) —
            # surfaced here so the unattended operator sees the drop, not only in run.log.
            "hook_burn_failed": sum(1 for c in led.clips.values() if c.hook_burn_failed),
            "errors": sum(1 for s in led.sources.values() if s.state is SourceState.error),
            # Both agent-gate kinds the responder answers (responder._SCHEMA): moments blocks the
            # clip/caption stages, captions blocks crosspost, so `fanops run` must see them to know it
            # has NOT converged.
            "awaiting": {"moments": len(pending(cfg, kind="moments")),
                         "captions": len(pending(cfg, kind="captions"))},
        }
    # digest is read-only reporting: build it from the just-committed ledger, OUTSIDE the lock, so
    # the slow markdown render never extends the lock-held window (it would block an overlapping
    # pass longer than the actual mutation requires).
    write_digest(Ledger.load(cfg), cfg)
    return summary
