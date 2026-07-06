# src/fanops/studio/actions_segments.py — S2 operator supercut authoring (mirrors actions_casting).
from __future__ import annotations
from fanops.ledger import Ledger
from fanops.models import Moment, _validate_segments
from fanops.moments import _content_token
from fanops.studio.actions_common import ActionResult


def set_segments(cfg, source_id: str, moment_id: str,
                 segments: list[tuple[float, float]]) -> ActionResult:
    """Set ordered non-overlapping supercut spans on a moment of this source. Rejects foreign moments."""
    try:
        with Ledger.transaction(cfg) as led:
            m = led.moments.get(moment_id)
            if m is None or m.parent_id != source_id:
                return ActionResult.failure(f"unknown moment {moment_id} for source {source_id}")
            segs = _validate_segments([(float(s), float(e)) for s, e in segments])
            data = m.model_dump(); data["segments"] = segs
            updated = Moment.model_validate(data)
            token = _content_token(updated.start, updated.end, updated.segments)
            led.moments[moment_id] = updated.model_copy(update={"content_token": token})
    except Exception as exc:
        return ActionResult.failure(f"set segments failed: {str(exc)[:160]}")
    return ActionResult.success({"source": source_id, "moment": moment_id, "segments": len(segs)})


def clear_segments(cfg, source_id: str, moment_id: str) -> ActionResult:
    """Clear supercut spans — revert to single-window token on the current envelope."""
    try:
        with Ledger.transaction(cfg) as led:
            m = led.moments.get(moment_id)
            if m is None or m.parent_id != source_id:
                return ActionResult.failure(f"unknown moment {moment_id} for source {source_id}")
            token = _content_token(m.start, m.end, [])
            led.moments[moment_id] = m.model_copy(update={"segments": [], "content_token": token})
    except Exception as exc:
        return ActionResult.failure(f"clear segments failed: {str(exc)[:160]}")
    return ActionResult.success({"source": source_id, "moment": moment_id, "cleared": True})
