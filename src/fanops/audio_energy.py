# src/fanops/audio_energy.py
"""Theme 1 (pipeline-quality): a real audio-ENERGY pass so signal peaks rank on loudness, not a
constant 0.5. Pure parse of ffmpeg's astats RMS metadata — proven on ffmpeg 8.1.1, ZERO new
dependency (we already shell out to ffmpeg everywhere):

    ffmpeg -hide_banner -i IN -af "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level" -f null -

emits, per reset window, a `... pts_time:T` line followed by `lavfi.astats.Overall.RMS_level=-NN.NN`
(dBFS, negative; digital silence = -inf). NB: parse the `ametadata=print` metadata channel, NOT the
ebur128 `M:` console line — ffmpeg 8.x collapsed that to `Summary:` and no longer prints it per-window.

Like signals.py this module is PURE (parse + cmd build); the subprocess lives in signals.detect_signals.
`rms_to_strength` maps dBFS into a [0,1] band so audio peaks become COMMENSURABLE with normalized
scene-cut scores in impact_cut (without a common band, an energy peak can never out-rank a scene cut)."""
from __future__ import annotations
import re

_PTS = re.compile(r"pts_time:\s*([0-9]+\.?[0-9]*)")
_RMS = re.compile(r"lavfi\.astats\.Overall\.RMS_level=\s*(-?inf|-?[0-9]+\.?[0-9]*)", re.IGNORECASE)

# dBFS band mapped to [0,1] strength. RMS rarely reaches 0; a musical drop sits near -6..-10 dB, quiet
# speech near -35..-45 dB. Floor/ceil chosen so a loud drop ~> 0.9 and quiet speech ~> 0.2; -inf -> 0.
_RMS_FLOOR_DB = -50.0
_RMS_CEIL_DB = -5.0

def energy_cmd(src: str) -> list[str]:
    """Build the ffmpeg astats RMS pass (per-window RMS to the metadata channel, null sink)."""
    return ["ffmpeg", "-hide_banner", "-vn", "-i", src, "-af",
            "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-"]   # -vn (MOL-119): astats is audio-only — decoding video was pure waste + timeout risk

def parse_energy(text: str) -> list[dict]:
    """Pair each `pts_time:T` line with the NEXT RMS_level reading -> [{"t": float, "rms": float}].
    A reading of -inf (digital silence) is kept as rms=-inf (strength 0). Unpaired/garbled lines are
    skipped, never raised (ffmpeg text is semi-trusted)."""
    out: list[dict] = []
    cur_t = None
    for line in text.splitlines():
        mt = _PTS.search(line)
        if mt:
            try: cur_t = float(mt.group(1))
            except ValueError: cur_t = None
            continue
        mr = _RMS.search(line)
        if mr and cur_t is not None:
            raw = mr.group(1).lower()
            rms = float("-inf") if raw.endswith("inf") else float(raw)
            out.append({"t": round(cur_t, 3), "rms": rms})
            cur_t = None                                  # consume the pts_time; next reading needs a new one
    return out

def rms_to_strength(rms_db: float) -> float:
    """Map an RMS dBFS reading to a [0,1] loudness strength (clamped). -inf / very quiet -> 0.0,
    at/above the ceiling -> 1.0. Monotonic in loudness so it composes with normalized scene scores."""
    if rms_db == float("-inf"): return 0.0
    s = (rms_db - _RMS_FLOOR_DB) / (_RMS_CEIL_DB - _RMS_FLOOR_DB)
    return round(max(0.0, min(1.0, s)), 4)
