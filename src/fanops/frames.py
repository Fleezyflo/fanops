# src/fanops/frames.py
"""Pure frame-strength scoring for P1's first-frame picker (the top muted-autoplay lever after the
text hook). A muted scroller decides in the first frame, so the cut should open on the STRONGEST
available still — not a black/flat/transition frame (which is exactly where a scene-CUT often lands,
so scene-cut alone is the wrong signal; it is at most a tiebreaker here).

No pixel library is in the base install (PIL/numpy ride only the [compose] extra), so strength comes
from ffmpeg's `signalstats` filter: luma mean (YAVG) + spatial contrast (YMAX-YMIN), parsed from text.
These functions are PURE — the subprocess that produces the text lives in clip.py (the mocked seam).

HONEST SCOPE: luma+contrast is a brightness/busyness FLOOR that rejects degenerate (near-black, blown,
near-uniform) starts and picks the least-weak candidate. It is NOT a saliency/face arbiter — P4's
first-frame dim becomes the real arbiter once reach data exists; face/saliency is a later lever."""
from __future__ import annotations
import re

# 8-bit luma (0-255). Below _MIN_LUMA = near-black opening (weakest still); above _MAX_LUMA = blown
# highlight (a white burned hook would be illegible over it). _MIN_CONTRAST rejects a near-uniform
# frame (a blur/transition/flat wall) — low YMAX-YMIN means there is nothing on screen to stop a scroll.
_MIN_LUMA = 16.0
_MAX_LUMA = 244.0
_MIN_CONTRAST = 24.0

_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
_YMIN = re.compile(r"lavfi\.signalstats\.YMIN=([0-9.]+)")
_YMAX = re.compile(r"lavfi\.signalstats\.YMAX=([0-9.]+)")

def parse_signalstats(text: str) -> tuple[float, float] | None:
    """Return (luma, contrast) from one frame's ffmpeg signalstats text, or None if YAVG/YMIN/YMAX are
    not all present (a probe that produced nothing usable -> the caller treats the frame as unscorable).
    luma = YAVG; contrast = YMAX - YMIN (spatial spread). Reads stdout+stderr indifferently."""
    ya = _YAVG.search(text); yi = _YMIN.search(text); yx = _YMAX.search(text)
    if not (ya and yi and yx):
        return None
    return float(ya.group(1)), float(yx.group(1)) - float(yi.group(1))

def frame_strength(*, luma: float, contrast: float) -> float | None:
    """Score one candidate frame, or None if it fails a degeneracy floor (near-black / blown / flat).
    Strength is the spatial contrast (a busier frame stops a scroll better); brightness only gates."""
    if luma < _MIN_LUMA or luma > _MAX_LUMA:
        return None
    if contrast < _MIN_CONTRAST:
        return None
    return contrast

def pick_strongest(candidates: list[dict]) -> dict | None:
    """Pick the strongest candidate frame, or None if none clears the floors. Each candidate is
    {t, luma, contrast, scene}. Rank by strength (contrast), break ties by scene-cut score (land on a
    real visual cut), then prefer the EARLIEST t (the least disruptive shift of the cut start). PURE."""
    scored = []
    for c in candidates:
        s = frame_strength(luma=c.get("luma", 0.0), contrast=c.get("contrast", 0.0))
        if s is not None:
            scored.append((s, c.get("scene", 0.0), c))
    if not scored:
        return None
    scored.sort(key=lambda r: (-r[0], -r[1], r[2].get("t", 0.0)))
    return scored[0][2]
