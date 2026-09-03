"""Strongest-opening-frame cut entry refinement (reconstruction / pre-warm only on default render path)."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from fanops import frames

# P1 T1 (strongest-frame cut start). How far the entry may shift to land on a stronger frame (a small
# nudge, like snap_window's max_shift — never invents a length floor), how many candidate frames to probe,
# the per-frame probe bound (keyframes.py idiom), and the minimum move to count as a real visual pick.
_VSTART_MAX_SHIFT_S = 1.5
_VSTART_CANDIDATES = 5
_VSTART_PROBE_TIMEOUT = 30.0
_VSTART_MIN_MOVE_S = 0.05
# vstart sidecar schema version (C2/H2): Theme 3 added sharpness to the pick, so the cached DECISION
# can change. A pre-sharpness sidecar (no/lower `v`) is a cache miss -> re-probe, never served stale.
_VSTART_V = 2
_SCENE_NEAR_S = 0.3          # a scene-cut peak within this of a candidate counts as "on a cut" (tiebreak)


def _vstart_candidate_times(start: float, end: float) -> list[float]:
    """Evenly-spaced candidate entry times in [start, min(start+shift, end)], INCLUDING `start` itself
    (so 'no better frame than the current start' is always reachable -> no spurious move). Pure."""
    hi = min(start + _VSTART_MAX_SHIFT_S, end)
    if hi <= start:
        return [start]
    n = _VSTART_CANDIDATES
    return [start + (hi - start) * i / (n - 1) for i in range(n)]


def _signalstats_cmd(src: str, t: float) -> list[str]:
    # One bounded ffmpeg per candidate: seek, decode ONE frame, print its luma stats (YAVG/YMIN/YMAX)
    # via the signalstats+metadata filter. `-f null -` discards output (no jpg written) — we only parse
    # the printed text. info loglevel makes metadata=print emit the lavfi.signalstats.* lines.
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-ss", f"{t:.3f}", "-i", src,
            "-frames:v", "1", "-vf", "signalstats,metadata=print", "-f", "null", "-"]


def _sharpness_cmd(src: str, t: float) -> list[str]:
    # Theme 3: a SECOND tiny pass for a relative sharpness proxy — the discrete Laplacian convolution
    # (`0 -1 0 / -1 4 -1 / 0 -1 0`) on a gray frame, then signalstats YAVG = mean edge energy. ffmpeg-only
    # (zero new dep). NB this is mean-of-Laplacian (relative, in-clip ranking), NOT variance-of-Laplacian.
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-ss", f"{t:.3f}", "-i", src,
            "-frames:v", "1", "-vf", "format=gray,convolution=0 -1 0 -1 4 -1 0 -1 0,signalstats,metadata=print",
            "-f", "null", "-"]


def _probe_frame_sharpness(src: str, t: float):
    """Run the Laplacian sharpness probe for ONE time and return the edge-energy proxy or None. FAIL-OPEN
    (any ffmpeg/parse failure -> None): sharpness is an ENHANCEMENT, so it degrades to contrast-only."""
    try:
        r = subprocess.run(_sharpness_cmd(src, t), check=False, capture_output=True, text=True,
                           timeout=_VSTART_PROBE_TIMEOUT)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return frames.parse_sharpness((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or ""))


def _probe_frame_strength(src: str, t: float):
    """Probe ONE candidate time -> (luma, contrast, sharpness) or None. luma/contrast from signalstats;
    sharpness from a second Laplacian pass (fail-open to None -> contrast-only ranking). Fail-open
    (ffmpeg absent/hung/error -> None) exactly like keyframes.extract_keyframes — never raises."""
    try:
        r = subprocess.run(_signalstats_cmd(src, t), check=False, capture_output=True, text=True,
                           timeout=_VSTART_PROBE_TIMEOUT)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    # getattr-defensive: the probe is fail-open, so a result missing stdout/stderr -> no stats -> None
    # (a real capture_output run always has both as strings; this also tolerates minimal test fakes).
    lc = frames.parse_signalstats((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or ""))
    if lc is None:
        return None
    return lc[0], lc[1], _probe_frame_sharpness(src, t)      # sharpness fail-open -> None (contrast-only)


def _scene_score_near(scene_peaks, t: float) -> float:
    # signal_peaks is loaded from an unvalidated JSON sidecar, so a non-numeric t/score must not raise
    # out of the picker (fail-open contract) — a bad peak just contributes no tiebreak.
    best = 0.0
    for p in scene_peaks or []:
        if not isinstance(p, dict) or p.get("kind") != "scene_cut":
            continue
        try:
            pt = float(p.get("t", 0.0))
            ps = float(p.get("score", 0.0))
        except (ValueError, TypeError):
            continue
        if abs(pt - t) <= _SCENE_NEAR_S:
            best = max(best, ps)
    return best


def pick_visual_start(src_path: str, start: float, end: float, *, scene_peaks, out_dir) -> tuple[float, str]:
    """Refine the cut entry onto the strongest opening frame within a bounded shift. Returns
    (new_start, kind): kind="visual" when a stronger frame moved the start, else "transcript" (the
    snap start is kept). The decision is CACHED in a per-(source,window) sidecar so the in-lock
    commit pass adopts it with NO ffmpeg (Phase D); the lock-free pre-warm pays the probe cost once.
    Fail-open: any probe failure leaves the start unchanged. PURE selection lives in frames.py."""
    out = Path(out_dir)
    key = hashlib.sha256(f"{src_path}|{round(start, 3)}|{round(end, 3)}".encode()).hexdigest()[:16]
    sidecar = out / f"vstart_{key}.json"
    if sidecar.exists():
        try:
            d = json.loads(sidecar.read_text())
            if d.get("v") != _VSTART_V:                    # C2/H2: stale (pre-sharpness) sidecar -> cache miss, re-probe
                raise KeyError("stale sidecar version")
            return float(d["start"]), str(d["kind"])      # cached -> no re-probe (commit stays lock-cheap)
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass                                            # corrupt/stale sidecar -> fall through to a real probe
    cands = []
    for t in _vstart_candidate_times(start, end):
        ls = _probe_frame_strength(src_path, t)
        if ls is not None:
            cands.append({"t": t, "luma": ls[0], "contrast": ls[1], "sharpness": ls[2],
                          "scene": _scene_score_near(scene_peaks, t)})
    win = frames.pick_strongest(cands)
    if win is not None and abs(win["t"] - start) > _VSTART_MIN_MOVE_S:
        new_start, kind = float(win["t"]), "visual"
    else:
        new_start, kind = start, "transcript"
    try:
        out.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"v": _VSTART_V, "start": new_start, "kind": kind}))
    except OSError:
        pass                                                # write failure just re-probes next time
    return new_start, kind
