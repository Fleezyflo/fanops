"""The stage DAG, extracted from the CLI (FIX F03/F91). advance() runs the deterministic
chain as far as it can and PAUSES at each agent gate (moments, captions). EVERY per-unit stage
call is wrapped so one bad source/moment/clip goes to `error` and is skipped — it never wedges
the whole pass (FIX F03). Returns counts + awaiting{moments,captions}."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (SourceState, MomentState, ClipState, PostState, Fmt, PLATFORM_ASPECT)
from fanops.accounts import Accounts
from fanops.ingest import ingest_drops
from fanops.transcribe import transcribe_source
from fanops.signals import detect_signals
from fanops.moments import request_moments, ingest_moments
from fanops.clip import render_aspects_for
from fanops.caption import request_captions, ingest_captions
from fanops.crosspost import crosspost_clips
from fanops.post.run import publish_due
from fanops.reconcile import reconcile_posts
from fanops.digest import write_digest
from fanops.log import get_logger
from fanops.agentstep import pending

def _aspects_for(accts: Accounts) -> set[Fmt]:
    return {PLATFORM_ASPECT.get(s.platform, Fmt.r9x16) for s in accts.surfaces()} or {Fmt.r9x16}

def advance(cfg: Config, *, base_time: str) -> dict:
    led = Ledger.load(cfg)
    accts = Accounts.load(cfg)
    log = get_logger(cfg)
    aspects = _aspects_for(accts)

    led = ingest_drops(led, cfg)

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
    for m in list(led.moments.values()):
        if m.state is MomentState.decided:
            try:
                led, clips = render_aspects_for(led, cfg, m.id, aspects=aspects)
                for clip in clips:
                    led = request_captions(led, cfg, clip.id,
                                           [(s.account, s.platform) for s in accts.surfaces()])
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
    led = crosspost_clips(led, cfg, accts, base_time=base_time)
    # Reconcile last pass's stranded posts BEFORE publishing this pass (AUDIT H4): resolve any
    # submitting/needs_reconcile post that has a submission_id via GET /v2/posts/:id. Only when a
    # live backend + key exist and there is actually something to reconcile (dryrun never produces
    # these states, and constructing the status client without a key would raise).
    reconcilable = (led.posts_in_state(PostState.submitting)
                    + led.posts_in_state(PostState.submitted)
                    + led.posts_in_state(PostState.needs_reconcile))
    if reconcilable and cfg.poster_backend != "dryrun" and cfg.blotato_api_key:
        try:
            led = reconcile_posts(led, cfg)
        except Exception as e:                       # status API hiccup must not wedge the pass
            log("reconcile", "-", "error", err=str(e)[:120])
    led = publish_due(led, cfg, now=None)   # publish cutoff = real now (base_time is the SCHEDULE anchor for crosspost; publishing uses actual now)

    led.save()
    write_digest(led, cfg)
    return {
        "sources": len(led.sources), "moments": len(led.moments),
        "clips": len(led.clips), "posts": len(led.posts),
        "published": len(led.posts_in_state(PostState.published)),
        "failed": len(led.posts_in_state(PostState.failed)),
        # needs_reconcile (AUDIT C1): ambiguous publish failures parked for human reconcile —
        # may be live on the platform, must NOT be blindly re-queued. Surfaced here so the
        # unattended operator sees it in `fanops run`/`advance` output, not only the digest.
        "needs_reconcile": len(led.posts_in_state(PostState.needs_reconcile)),
        "holds": sum(1 for c in led.clips.values() if c.held),
        "errors": sum(1 for s in led.sources.values() if s.state is SourceState.error),
        "awaiting": {"moments": len(pending(cfg, kind="moments")),
                     "captions": len(pending(cfg, kind="captions"))},
    }
