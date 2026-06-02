"""Adjust stage: rank ANALYZED posts that have a real lift_score (FIX F22 — failed posts have
none and are excluded). AMPLIFY = re-open a moment request on the winner's SOURCE, injecting
the winning moment's signature as guidance; write_request auto-invalidates the stale response
(Task 10) so ingest_moments answers fresh and reconciles (Task 11) — v1's amplify silently
no-opped. RETIRE = ledger.retire_clip, which clip/crosspost honor (FIX F55)."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import MomentRequest, PostState, SourceState, MomentState
from fanops.agentstep import write_request

def classify_outcomes(led: Ledger, *, winner_pct: float = 0.3, retire_pct: float = 0.2,
                      lift_floor: float = 20.0) -> dict:
    # Rank ANALYZED posts that carry a real lift_score (failed posts have none — FIX F22).
    analyzed = [p for p in led.posts.values()
                if p.state is PostState.analyzed and "lift_score" in p.metrics]
    if not analyzed:
        return {"winners": [], "losers": []}
    ranked = sorted(analyzed, key=lambda p: p.metrics.get("lift_score", 0.0), reverse=True)
    n = len(ranked)
    win_cut = max(1, round(n * winner_pct))
    winners = [p.id for p in ranked[:win_cut]]
    # Conservative retirement: only the bottom retire_pct AND below an absolute lift_floor.
    # (Decoupled from winners; a clip that clears the floor is never retired just for being
    # bottom-ranked relative to a hit — avoids draining an artist's catalogue every pass.)
    lose_n = round(n * retire_pct)
    bottom = ranked[n - lose_n:] if lose_n > 0 else []
    losers = [p.id for p in bottom if p.metrics.get("lift_score", 0.0) < lift_floor]
    return {"winners": winners, "losers": losers}

def amplify(led: Ledger, cfg: Config, winner_post_ids: list[str], *,
            max_amplify_per_source: int = 3) -> Ledger:
    for pid in winner_post_ids:
        post = led.posts.get(pid)
        if post is None:
            continue
        clip = led.clips.get(post.parent_id)
        moment = led.moments.get(clip.parent_id) if clip else None
        src = led.sources.get(moment.parent_id) if moment else None
        if not src:
            continue
        # E1: per-source amplification budget. A MISSING key defaults to 0 (sources without the
        # count keep amplifying until they hit the cap). At/over the cap, skip the source entirely
        # — no write_request, no state flip — so an autonomous LLM can't grow one source endlessly.
        used = int(src.meta.get("amplify_count", 0))
        if used >= max_amplify_per_source:
            continue
        guidance = (f"AMPLIFY: a moment like '{moment.transcript_excerpt}' ({moment.reason}) "
                    f"hit hard (lift={post.metrics.get('lift_score')}). Find MORE moments in that "
                    f"vein in this source — do not repeat the same timestamps.")
        payload = MomentRequest(source_id=src.id, request_id="", duration=src.duration or 0.0,
                                transcript=src.transcript or [], signal_peaks=src.signal_peaks or [],
                                language=src.language, guidance=guidance).model_dump()
        payload.pop("request_id", None)
        write_request(cfg, kind="moments", key=src.id, payload=payload)   # invalidates stale resp
        src.meta["amplify_count"] = used + 1              # E1: count only successful amplifies
        led.set_source_state(src.id, SourceState.moments_requested)
    return led

def retire(led: Ledger, loser_post_ids: list[str]) -> Ledger:
    for pid in loser_post_ids:
        post = led.posts.get(pid)
        if post is None:
            continue
        led.retire_clip(post.parent_id)                 # suppress this clip (FIX F55)
        clip = led.clips.get(post.parent_id)
        if clip is not None:
            # If no sibling clip of this moment is still live, retire the MOMENT too — else
            # clip.py's render guard (which checks moment state) would re-render it into a
            # fresh live clip on a later pass, silently undoing the retirement.
            live_sibs = [c for c in led.clips_of(clip.parent_id) if not led.is_retired_clip(c.id)]
            if not live_sibs:
                led.set_moment_state(clip.parent_id, MomentState.retired)
    return led
