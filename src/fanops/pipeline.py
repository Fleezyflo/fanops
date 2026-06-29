"""The stage DAG, extracted from the CLI (FIX F03/F91). advance() runs the deterministic
chain as far as it can and PAUSES at each agent gate (moments, captions). EVERY per-unit stage
call is wrapped so one bad source/moment/clip goes to `error` and is skipped — it never wedges
the whole pass (FIX F03). Returns counts + awaiting{moments,captions}."""
from __future__ import annotations
from typing import Optional, TypedDict
from datetime import datetime, timezone
from fanops.config import Config
from fanops.errors import AuthError
from fanops.ledger import Ledger
from fanops.models import (SourceState, MomentState, ClipState, PostState, Fmt, PLATFORM_ASPECT)
from fanops.accounts import Accounts
from fanops.ingest import ingest_drops
from fanops.transcribe import transcribe_source
from fanops.signals import detect_signals
from fanops.moments import request_moments, ingest_moments, request_moment_hooks, ingest_moment_hooks
from fanops.hookscore import log_hook_quality
from fanops.router import route_moments
from fanops.casting import request_moment_casting, ingest_moment_casting, scoped_caption_surfaces
from fanops.stitch_render import (mine_suggestions, render_approved_stitches,
                                  approved_disabled_count)
from fanops.intro_match import request_intro_match, ingest_intro_match
from fanops.clip import render_aspects_for
from fanops.caption import request_captions, ingest_captions
from fanops.crosspost import crosspost_clips
from fanops.post.run import publish_due
from fanops.reconcile import reconcile_due
from fanops.digest import write_digest
from fanops.log import get_logger
from fanops.agentstep import pending
from fanops.timeutil import parse_iso
from fanops import produce

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

# M3 — _prewarm + _prewarm_sequential + _prewarm_concurrent + _produce_source + the
# in-pipeline SourceResult dataclass are deleted. Their replacement is fanops.produce.run_all,
# imported at the top — one entry point, one module owning lock-free side-effect-only artifact
# warming. The reducer (_stage_source_to_moments etc., below) keeps its existing shape: it
# re-runs the slow stages in-lock and they short-circuit on the now-warm artifacts (M1 +
# transcript JSON, M2 + detect sidecar + keyframes cache, render fingerprint). The reducer's
# in-lock subprocess CALLS still appear, but every one is a microsecond cache-hit by
# construction — the bad path (a subprocess actually spawning inside the flock) was already
# closed by M1+M2's stage_lock + on-disk artifact contract.

def _quarantine(coll, eid, error_state, stage, exc, log) -> None:
    """The per-unit failure stamp shared by the source/moment/clip stage loops (FIX F03): flip the entity
    to its error state, record the typed reason, and log — so one bad unit is skipped, never wedging the
    whole pass. `coll` is the LIVE ledger collection passed at call time (after any in-block reassignment),
    so the same object the stage was operating on is the one stamped. The stamp lands via an IMMUTABLE
    model_copy(update=...) setter (audit x-f1): these are ledger records, and the day any of Source/Moment/Clip
    gains frozen=True an in-place `obj.state = ...` would raise INSIDE this except handler and wedge the whole
    pass — the precise failure F03 added quarantine to prevent. Replacing the collection entry keeps it safe."""
    obj = coll[eid]
    coll[eid] = obj.model_copy(update={"state": error_state, "error_reason": f"{type(exc).__name__}: {exc}"})
    log(stage, eid, "error", err=str(exc)[:120])


def _stage_source_to_moments(led: Ledger, cfg: Config, accts: Accounts, log) -> Ledger:
    """transcribe -> signals -> request moments, per source, each quarantined. A third-party asset is
    INERT to clip-production (M1)."""
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
            _quarantine(led.sources, s.id, SourceState.error, "source", e, log)
    return led


def _stage_ingest_moments(led: Ledger, cfg: Config, log) -> Ledger:
    """Ingest each source's DECIDED moments (moments_requested -> moments_decided), per-source quarantine."""
    for s in list(led.sources.values()):
        if s.state is SourceState.moments_requested:
            try:
                led = ingest_moments(led, cfg, s.id)
            except Exception as e:
                _quarantine(led.sources, s.id, SourceState.error, "moments", e, log)
    return led


def _stage_moment_hooks(led: Ledger, cfg: Config, accts: Accounts, log) -> Ledger:
    """M1b PASS 2 (frame-seeing hook): for each source whose picks reconciled (picks_decided), open a
    per-pick moment_hooks gate (request, write-once) seeing THAT window's frames, then ingest any landed
    hooks (promote picked->decided; source->moments_decided once every pick's hook lands). Per-source
    quarantine, mirroring the pick gate. The responder answers between passes — the SAME multi-gate
    convergence as moments->captions (one extra cycle)."""
    for s in list(led.sources.values()):
        if s.state is SourceState.picks_decided:
            try:
                led = request_moment_hooks(led, cfg, s.id, accounts=accts)   # personas + learned hook styles ride here
                led = ingest_moment_hooks(led, cfg, s.id, accounts=accts)   # AGENT-5: intersect author-echoed handle keys with real accounts
            except Exception as e:
                _quarantine(led.sources, s.id, SourceState.error, "moment_hooks", e, log)
    return led


def _stage_casting(led: Ledger, cfg: Config, accts: Accounts, log) -> Ledger:
    """M1 (Option C) per-account moment SELECTION (default ON via cfg.account_casting): an LLM gate chooses,
    per account, that account's OWN set of moments from the decided pool, writing Moment.affinities BEFORE
    the render loop. The crosspost affinity gate then fans a cast moment ONLY to its accounts. request is
    write-once; ingest applies the selection once the responder answers (a no-op until then). Per-source
    quarantine, fail-open (log-only — affinities just stay []). OFF -> no gate, byte-identical fan-out. NB:
    the LLM gate is the SOLE production selector (the old token-overlap heuristic casting.cast_moments was
    removed in WS-M1/MOM-7; the operator cast_add/cast_remove override is the manual selection path)."""
    if not cfg.account_casting:
        return led
    for s in list(led.sources.values()):
        rel = [m for m in led.moments.values() if m.parent_id == s.id
               and m.state in (MomentState.decided, MomentState.clipped)]
        # P1 backfill: process a source with DECIDED moments (the normal path) OR one stranded with
        # CLIPPED-uncast moments (raced past the gate before the answer landed) — write-once keeps it
        # idempotent (a source already cast has non-empty affinities -> skipped). OFF firewall: this whole
        # block is account_casting-guarded.
        has_decided = any(m.state is MomentState.decided for m in rel)
        has_clipped_uncast = any(m.state is MomentState.clipped and not m.affinities for m in rel)
        if not has_decided and not has_clipped_uncast:
            continue
        try:
            led = request_moment_casting(led, cfg, s.id, accts)
            led = ingest_moment_casting(led, cfg, s.id, accts)
        except Exception as e:
            # xc-2: a request/ingest failure (e.g. an OSError opening the gate) must be VISIBLE, not just logged —
            # route it through the same degraded_reason channel ingest uses, so the operator sees the casting
            # downgrade. Crosspost independently DEFERS this source this pass via casting_gate_failed_to_open
            # (it won't silently fan-to-all); the gate re-opens next pass.
            log("casting", s.id, "error", err=str(e)[:120])
            cur = led.sources.get(s.id)
            if cur is not None:
                led.sources[s.id] = cur.model_copy(update={"degraded_reason": f"casting failed this pass: {str(e)[:120]}"})
    return led


def _stage_render_and_caption(led: Ledger, cfg: Config, accts: Accounts, aspects: set[Fmt], log) -> Ledger:
    """For each DECIDED moment: render its aspects, then request captions for each rendered clip, scoped to
    the affinity-admitted surfaces (M5). A failed-aspect clip (ClipState.error) is NOT laundered into a
    phantom captioned post with a dangling mp4. Per-moment quarantine."""
    for m in list(led.moments.values()):
        if m.state is MomentState.decided:
            try:
                led, clips = render_aspects_for(led, cfg, m.id, aspects=aspects)
                for clip in clips:
                    if clip.state is not ClipState.rendered: continue   # a failed-aspect clip (ClipState.error) must not be laundered into a phantom captioned post with a dangling mp4
                    # M5: scope the caption request to the affinity-admitted surfaces. Casting OFF / an
                    # uncast moment -> all surfaces (byte-identical). Within a decision cycle this is a
                    # SUPERSET of the crosspost survivors (which narrow further by batch target), so every
                    # minted post has a caption; a post-captioning RE-DECISION swap is caught by crosspost's
                    # cap-is-None skip. Crosspost stays the SOLE casting-intent gate; meta_captions is never
                    # read as casting intent.
                    led = request_captions(led, cfg, clip.id,
                                           scoped_caption_surfaces(cfg, led, m, accts.surfaces()),
                                           accounts=accts)
            except Exception as e:
                _quarantine(led.moments, m.id, MomentState.error, "clip", e, log)
    return led


def _stage_structural_hooks(led: Ledger, cfg: Config, log) -> Ledger:
    """M4/M5/M6 structural-hooks: after the bare clips render, run the per-format producers + render the
    operator-approved plans. intro_tease first OPENS its LLM-vision matcher gate (request) + applies any
    landed pairings (ingest) so mine_suggestions can pair them. Ledger-only mutation here (safe in-lock);
    the heavy approved-plan RENDER is lock-free in the prewarm pass. Per-format opt-in + fail-open. Both
    formats OFF -> byte-identical to pre-structural-hooks. A forward-only kill-switch logs (never silently
    freezes) any approved plan of a now-disabled format."""
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
    n = approved_disabled_count(led, enabled=strategies)
    if n:
        log("structural_hooks", "-", "warn",
            err=f"{n} approved plans for disabled formats (feature OFF) — will render when re-enabled")
    return led


def _stage_ingest_captions(led: Ledger, cfg: Config, log) -> Ledger:
    """Ingest each captions_requested clip's landed captions (captions_requested -> captioned), per-clip
    quarantine."""
    for c in list(led.clips.values()):
        if c.state is ClipState.captions_requested:
            try:
                led = ingest_captions(led, cfg, c.id)
            except Exception as e:
                _quarantine(led.clips, c.id, ClipState.error, "caption", e, log)
    return led


def _stage_crosspost(led: Ledger, cfg: Config, accts: Accounts, base_time: str, log) -> Ledger:
    """AUDIT M2: the volatile crosspost stage runs inside the transaction, wrapped so a raise does NOT
    abandon the whole pass's in-memory progress before the exit-save. The ONE exception we deliberately let
    escape is a FATAL AuthError (F52): a bad key fails every post, so halting + rolling back the pass is
    intended (handled by the CLI run guard). crosspost has no Blotato call today, but if one is ever added
    a bad key must halt, not be logged-and-continued."""
    try:
        return crosspost_clips(led, cfg, accts, base_time=base_time)
    except AuthError:
        raise
    except Exception as e:
        log("crosspost", "-", "error", err=str(e)[:120])
        return led


def _reconcile_safe(cfg: Config, log) -> None:
    """Reconcile last pass's stranded posts AFTER the main txn commits, BEFORE publishing (AUDIT H4 + M1
    reconcile-out-of-lock): reconcile_due pre-polls each backend status with NO lock held, then applies the
    cached results in its OWN tight transaction (N status GETs never hold the ledger flock). Gated on
    is_live_backend (per-channel readiness); resolves each post's provider via effective_provider and skips
    dryrun/provider-less posts. A FATAL AuthError halts (symmetry with publish); any other hiccup must not
    wedge the pass. `fanops resolve` stays the manual escape hatch."""
    if cfg.is_live_backend:
        try:
            reconcile_due(cfg)
        except AuthError:
            raise                                        # bad key: every poll fails -> halt
        except Exception as e:                           # status API hiccup must not wedge the pass
            log("reconcile", "-", "error", err=str(e)[:120])


def _publish_safe(cfg: Config, log) -> None:
    """publish-out-of-lock: publishing runs AFTER the main transaction COMMITS — its network I/O (media
    upload + poster.publish) must NOT hold the ledger flock (it would starve a concurrent Studio/daemon
    writer up to the 30s lock timeout). publish_due owns its locking: per-post claim->network->finalize,
    network lock-free. publish cutoff = real now (base_time anchors the crosspost SCHEDULE; publishing uses
    actual now). A FATAL AuthError halts the run (F52)."""
    try:
        publish_due(cfg, now=None)
    except AuthError:
        raise                                            # F52: halt the run on a bad key
    except Exception as e:
        log("publish", "-", "error", err=str(e)[:120])


# WS2 (audit x-f2/xc-3): the ONE canonical list of agent-gate kinds. Every surface that enumerates gates —
# the awaiting summary, the convergence check, the LOUD blocked-note, the run.log breadcrumb, `fanops status` —
# derives from THIS so a future 5th gate cannot be silently omitted from any of them (the bug was a 4th gate,
# moment_casting, added to the awaiting dict but never to the operator-facing surfaces). Order = pipeline order.
GATE_KINDS = ("moments", "moment_hooks", "moment_casting", "captions")


class AwaitingCounts(TypedDict):
    """The per-kind count of agent gates still awaiting a responder answer (the run loop converges only
    when all are 0). Keys mirror GATE_KINDS / responder._SCHEMA / agentstep.pending kinds."""
    moments: int
    moment_hooks: int
    moment_casting: int
    captions: int


class RunSummary(TypedDict):
    """advance()'s return: the post-run heartbeat/summary. A TypedDict (NOT a dataclass) on purpose — the
    value is `print()`ed as a dict for operators, flows to the Studio as an ActionResult.detail payload, and
    is key-accessed by the CLI/run loop, so the runtime shape MUST stay a plain dict; this only documents the
    keys + lets a checker catch a mistyped key. last_published_age_hours is None when nothing has published."""
    sources: int
    moments: int
    clips: int
    posts: int
    published: int
    failed: int
    published_in_run: int
    last_published_age_hours: Optional[float]
    needs_reconcile: int
    holds: int
    hook_burn_failed: int
    frames_unread: int
    errors: int
    awaiting: AwaitingCounts


def _build_summary(cfg: Config, before: set) -> RunSummary:
    """B5/E2 heartbeat + summary, built from a POST-publish READ-ONLY reload (the publish committed via its
    own finalize txns). published_in_run = published ids now MINUS `before` (snapshotted at main-txn ENTRY —
    the THIS-RUN delta, incl. reconcile-driven publishes); last_published_age_hours is the age (hours, 2dp)
    of the newest published post's scheduled_time vs now, or None when none parses. Also writes the
    read-only digest from the same snapshot, OUTSIDE the lock."""
    led = Ledger.load(cfg)                               # post-publish snapshot, READ-ONLY (no save/lock)
    after = led.posts_in_state(PostState.published)
    published_in_run = len([p for p in after if p.id not in before])
    newest = max((_parse(p.scheduled_time) for p in after if p.scheduled_time), default=None)
    last_published_age_hours = (None if newest is None
                                else round((datetime.now(timezone.utc) - newest).total_seconds() / 3600, 2))
    summary: RunSummary = {
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
        # AGENT-9: hooks authored with frames ATTACHED but UNREAD (text-grounded, not frame-grounded) —
        # surfaced like hook_burn_failed so the operator sees the degraded grounding, not only run.log.
        "frames_unread": sum(1 for m in led.moments.values() if m.hook_frames_unread),
        "errors": sum(1 for s in led.sources.values() if s.state is SourceState.error),
        # All three agent-gate kinds the responder answers (responder._SCHEMA): moments (pick) blocks the
        # hook gate, moment_hooks blocks the clip/caption stages, captions blocks crosspost — so `fanops
        # run` must see every one to know it has NOT converged.
        # WS2: built from GATE_KINDS (the single source) so every gate — incl. moment_casting (P1: the run loop
        # must WAIT for casting) and any future kind — is counted without a per-call edit here.
        "awaiting": {k: len(pending(cfg, kind=k)) for k in GATE_KINDS},
    }
    write_digest(led, cfg)                               # read-only reporting, same snapshot, OUTSIDE the lock
    return summary


def advance(cfg: Config, *, base_time: str) -> RunSummary:
    accts = Accounts.load(cfg)
    log = get_logger(cfg)
    aspects = _aspects_for(accts)

    # Phase D: ingest in a SHORT transaction FIRST so a brand-new drop is catalogued and VISIBLE to the
    # lock-free pre-warm below — otherwise its transcribe would run inside the main lock. ingest_drops is
    # idempotent (content-addressed dedup), so this never double-catalogues.
    with Ledger.transaction(cfg) as led:
        led, _ = ingest_drops(led, cfg)
    # M3: lock-free producer pass — warms transcript JSON, signals sidecar, render mp4 +
    # fingerprint, stitch mp4 for every catalogued/decided unit. The reduce transaction below
    # re-runs the slow stages and they short-circuit on the warm artifacts (M1 + M2 caches).
    # Lock-free; saves nothing; fail-open per source.
    produce.run_all(cfg, aspects, log)

    # AUDIT B4: the load-mutate-save COMMIT runs inside ONE ledger transaction — the lock is acquired
    # BEFORE load and the single save happens on clean exit. This closes the lost-update window the
    # save()-only lock left open (two overlapping cron passes both loaded a stale snapshot; last save()
    # won; the other's updates — a published post, a submitting flip — vanished silently). A second live
    # pass is excluded (typed LockBusyError, bounded by timeout), not silently overwritten. (Phase D: the
    # SLOW subprocesses already ran lock-free above; this transaction only flips state + does the cheap
    # gate/crosspost/publish work, so the lock-held window is short.)
    # WHOLE-PASS ROLLBACK on a late UNCAUGHT raise is DELIBERATE (audit x-f5): Ledger.transaction saves
    # ONLY on a clean exit, so an exception escaping any stage below discards EVERY in-memory transition this
    # pass made and leaves the last committed snapshot on disk — a half-applied pass is never persisted
    # (correctness). The volatile stages whose raise must NOT cost the pass (crosspost/publish) wrap their own
    # work (AUDIT M2); anything still uncaught rolls the pass back BY DESIGN. This is SAFE despite the expense
    # because the heavy artifacts (transcripts/renders/composites) were warmed OUT OF LOCK by _prewarm above,
    # so a rolled-back pass loses only cheap in-memory state-flips: the next pass re-runs the stages, which
    # fingerprint-SKIP on the warm artifacts and recover the work instead of redoing it (pinned by
    # test_advance_rollback_recovers_warm_artifacts).
    with Ledger.transaction(cfg) as led:
        # B5/E2: snapshot the already-published post ids at transaction ENTRY so the summary's
        # published_in_run is a THIS-RUN delta — a post already published when the pass opened is in
        # `before` and is NOT counted (set difference against the exit state). Ingest already ran (above)
        # and never publishes, so the snapshot here is the correct baseline.
        before = {p.id for p in led.posts_in_state(PostState.published)}
        led = _stage_source_to_moments(led, cfg, accts, log)
        led = _stage_ingest_moments(led, cfg, log)
        led = _stage_moment_hooks(led, cfg, accts, log)
        # Task 9 scoreboard: one read-only digest line of hook quality on EVERY pass — independent of any
        # subsystem flag, so the operator's hook-quality visibility stays on by default. Read-only + fail-open.
        try: log_hook_quality(led, cfg)
        except Exception as e: log("hookscore", "-", "error", err=str(e)[:120])
        # M2 structural-hooks router (opt-in, observe-only): classify each decided hook into a hook_strategy
        # reason BEFORE the render loop. Renders nothing; fail-open. Default OFF -> byte-identical to today.
        if cfg.hook_router:
            try:
                led = route_moments(led, cfg)
            except Exception as e:
                log("router", "-", "error", err=str(e)[:120])
        led = _stage_casting(led, cfg, accts, log)
        led = _stage_render_and_caption(led, cfg, accts, aspects, log)
        led = _stage_structural_hooks(led, cfg, log)
        led = _stage_ingest_captions(led, cfg, log)
        led = _stage_crosspost(led, cfg, accts, base_time, log)
    _reconcile_safe(cfg, log)                            # stranded-post reconcile, out of lock (AUDIT H4)
    _publish_safe(cfg, log)                              # publish-out-of-lock (own per-post locking)
    return _build_summary(cfg, before)                   # post-publish read-only snapshot + digest
