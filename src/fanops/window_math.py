"""Cut-window math: EOF clamp, transcript snap, and playable-duration helpers."""
from __future__ import annotations

from fanops.models import Clip

# Cut length is the picked moment. No talk/song/short floor or ceiling at render — fit_window
# defaults are EOF-clamp only. Callers pass hi=source duration (or inf if unprobed).
# Length is pick-owned (moment start/end). This module does not apply bands.TALK/SONG/SHORT.


def realized_clip_seconds(clip: Clip | None, moment) -> float | None:
    """Playable duration for platform-cap checks: rendered cut_seconds when set, else moment envelope."""
    if clip is None:
        return None
    if clip.cut_seconds is not None:
        return clip.cut_seconds
    if moment is not None:
        return moment.end - moment.start
    return None


# How far (seconds) snap_window may move the window start onto a transcript-line start. A small
# nudge: it polishes mid-word starts; end follows by the same Δ (no length floor).
_SNAP_MAX_SHIFT_S = 1.5


def _nearest(value: float, candidates: list[float], max_shift: float) -> float | None:
    in_range = [c for c in candidates if abs(c - value) <= max_shift]
    return min(in_range, key=lambda c: abs(c - value)) if in_range else None


def _trusted_transcript(src) -> list[dict]:
    from fanops.transcribe import trusted_segments
    return trusted_segments(src.transcript or [], src_lang=getattr(src, "language", None))


def snap_window(start: float, end: float, transcript: list[dict] | None,
                *, duration: float = 0.0, max_shift: float = _SNAP_MAX_SHIFT_S) -> tuple[float, float]:
    """Nudge start onto a nearby transcript-line start; end follows by the same Δ so the pick span is kept.
    No transcript / no nearby start → identity. Lines missing a numeric start are skipped. Zero/EOF clip
    the slid edges (duration<=0 means unprobed -> no EOF clamp); invert after clip restores the original.
    Pure. Does not independently snap end. Unused on the render/reframe default path (pick is the cut)."""
    if not transcript:
        return start, end
    starts = [ln["start"] for ln in transcript if isinstance(ln.get("start"), (int, float))]
    ns = _nearest(start, starts, max_shift)
    if ns is None:
        return start, end
    d = ns - start
    s, e = ns, end + d
    if s < 0:
        s = 0.0
    if duration and e > duration:
        e = duration
    return (s, e) if s < e else (start, end)


def fit_window(start: float, end: float, duration: float,
               *, lo: float = 0.0, hi: float = float("inf")) -> tuple[float, float]:
    """EOF-clamp a picked [start,end]. The pick is the cut — lo/hi are ignored (no pad, no band trim).
    Start floored at 0; end clamped to probed `duration` (duration<=0 -> unprobed, no EOF clamp)."""
    s = max(0.0, start)
    e = duration if duration and end > duration else end
    return (s, e) if s < e else (start, end)
