"""Adjust stage: rank ANALYZED posts that have a real lift_score (FIX F22 — failed posts have
none and are excluded). AMPLIFY = re-open a moment request on the winner's SOURCE, injecting
the winning moment's signature as guidance; write_request auto-invalidates the stale response
(Task 10) so ingest_moments answers fresh and reconciles (Task 11) — v1's amplify silently
no-opped. RETIRE = ledger.retire_clip, which clip/crosspost honor (FIX F55)."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import MomentRequest, PostState, SourceState
from fanops.agentstep import write_request

def classify_outcomes(led: Ledger, *, winner_pct: float = 0.3) -> dict:
    analyzed = [p for p in led.posts.values()
                if p.state is PostState.analyzed and "lift_score" in p.metrics]
    if not analyzed:
        return {"winners": [], "losers": []}
    ranked = sorted(analyzed, key=lambda p: p.metrics.get("lift_score", 0.0), reverse=True)
    cut = max(1, round(len(ranked) * winner_pct))
    return {"winners": [p.id for p in ranked[:cut]], "losers": [p.id for p in ranked[cut:]]}

def amplify(led: Ledger, cfg: Config, winner_post_ids: list[str]) -> Ledger:
    for pid in winner_post_ids:
        post = led.posts.get(pid)
        if post is None:
            continue
        clip = led.clips.get(post.parent_id)
        moment = led.moments.get(clip.parent_id) if clip else None
        src = led.sources.get(moment.parent_id) if moment else None
        if not src:
            continue
        guidance = (f"AMPLIFY: a moment like '{moment.transcript_excerpt}' ({moment.reason}) "
                    f"hit hard (lift={post.metrics.get('lift_score')}). Find MORE moments in that "
                    f"vein in this source — do not repeat the same timestamps.")
        payload = MomentRequest(source_id=src.id, request_id="", duration=src.duration or 0.0,
                                transcript=src.transcript or [], signal_peaks=src.signal_peaks or [],
                                language=src.language, guidance=guidance).model_dump()
        payload.pop("request_id", None)
        write_request(cfg, kind="moments", key=src.id, payload=payload)   # invalidates stale resp
        led.set_source_state(src.id, SourceState.moments_requested)
    return led

def retire(led: Ledger, loser_post_ids: list[str]) -> Ledger:
    for pid in loser_post_ids:
        post = led.posts.get(pid)
        if post is not None:
            led.retire_clip(post.parent_id)             # observable suppression (FIX F55)
    return led
