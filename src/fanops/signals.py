# src/fanops/signals.py
"""Free, local signal pass: ffmpeg silencedetect (speech onsets) + scdet (scene cuts).
scdet prints lavfi.scd.score/time on stderr at -loglevel info — showinfo does NOT print a
scene score (the v1 bug). Optional loudness (ebur128) can be added later; silence+scene
cover beat drops and visual cuts."""
from __future__ import annotations
import json, re, subprocess
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState
from fanops.ingest import probe_dimensions
from fanops.errors import ToolchainMissingError
from fanops.audio_energy import energy_cmd, parse_energy, rms_to_strength

_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")
_SCD = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+),\s*lavfi\.scd\.time:\s*([0-9.]+)")

# Sidecar schema version (C2/H2). Theme 1 changed the peak `score` shape (energy-derived speech +
# normalized scene), so a pre-Theme-1 sidecar (no/lower `v`) MUST be a cache miss — else an
# already-ingested source serves the old constant 0.5 forever. Bump this on any peak-shape change.
_SIDECAR_V = 2
# scdet score is a 0..100 change metric; energy strength is [0,1]. To let them compete in the SAME
# `score` field (impact_cut._impact_peak) we normalize scene scores into [0,1] — but ONLY in energy
# mode, so the no-energy fallback stays byte-identical to today (scene raw, speech 0.5).
_SCENE_SCALE = 100.0

def parse_silences(stderr: str) -> list[dict]:
    return [{"t": float(m), "kind": "speech_resume", "score": 0.5}
            for m in _SIL_END.findall(stderr)]

def parse_scene_changes(stderr: str) -> list[dict]:
    return [{"t": float(t), "kind": "scene_cut", "score": float(score)}
            for score, t in _SCD.findall(stderr)]

def _nearest_rms(t: float, windows: list[dict]):
    # The RMS of the energy window closest in time to `t` (or None if there are no windows).
    best = None; best_d = None
    for w in windows:
        try: d = abs(float(w["t"]) - t)
        except (TypeError, ValueError, KeyError): continue
        if best_d is None or d < best_d: best_d = d; best = w.get("rms")
    return best

def apply_energy(peaks: list[dict], windows: list[dict]) -> list[dict]:
    """Pure: return a NEW peak list scored on real energy. In ENERGY MODE (windows present) a
    speech_resume peak's `score` becomes the loudness strength of its nearest energy window (with an
    `energy` field), and a scene_cut's `score` is normalized to [0,1] — so audio and scene peaks are
    COMMENSURABLE in impact_cut's single `score` field. With NO windows the peaks are returned
    unchanged (today's behavior: speech 0.5, scene raw) — the enhancement fails soft, never alters."""
    if not windows:
        return [dict(p) for p in peaks]
    out: list[dict] = []
    for p in peaks:
        q = dict(p)
        if p.get("kind") == "speech_resume":
            rms = _nearest_rms(float(p["t"]), windows)
            strength = rms_to_strength(rms) if rms is not None else 0.0
            q["score"] = strength; q["energy"] = strength
        elif p.get("kind") == "scene_cut":
            try: q["score"] = round(min(1.0, float(p["score"]) / _SCENE_SCALE), 4)
            except (TypeError, ValueError): pass
        out.append(q)
    return out

def _silence_cmd(src: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-i", src, "-af",
            "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"]

def _scene_cmd(src: str) -> list[str]:
    # scdet at info loglevel emits lavfi.scd.score/time lines on stderr.
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", src, "-vf",
            "scdet=threshold=10", "-f", "null", "-"]

# Hard bound per signal pass: detect_signals runs inside advance()'s ledger transaction, so an
# unbounded ffmpeg hang on a corrupt source held the flock forever. A timeout raises
# TimeoutExpired, which propagates BY DESIGN to the per-source quarantine (same retriable
# SourceState.error treatment as ToolchainMissingError below).
_FFMPEG_TIMEOUT = 600.0

def _run_ffmpeg(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run an ffmpeg signal-detection command, translating a PRE-LAUNCH FileNotFoundError/OSError
    (ffmpeg absent from PATH) into a typed ToolchainMissingError. detect_signals runs INSIDE the
    pipeline's per-source quarantine, so this typed error is caught there and the source goes to
    SourceState.error with a clear 'toolchain missing' reason (instead of a bare 'FileNotFoundError:
    ffmpeg'); the pass never crashes. A HUNG ffmpeg is killed at _FFMPEG_TIMEOUT and the raised
    TimeoutExpired propagates to that same quarantine. check=False semantics otherwise: a nonzero
    RETURNCODE is fine (we parse stderr regardless)."""
    try:
        return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    except (FileNotFoundError, OSError) as e:
        raise ToolchainMissingError(
            f"ffmpeg not found on PATH — install ffmpeg to detect signals ({type(e).__name__})") from e

def detect_signals(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    src = led.sources[source_id]
    # Phase D (out-of-lock): signal detection is DETERMINISTIC per (content-addressed) source. A
    # lock-free pre-warm pass writes the per-source sidecar BEFORE the ledger transaction; if it's
    # present + parseable, adopt it and SKIP the two ffmpeg passes — keeping them OUT of the lock. A
    # corrupt sidecar is not adopted (parse failure falls through to a real run, which overwrites it).
    sidecar = cfg.agent_io / "signals" / f"{source_id}.json"
    if sidecar.exists():
        try:
            d = json.loads(sidecar.read_text())
            if d.get("v") != _SIDECAR_V:               # C2/H2: stale (pre-energy) sidecar -> cache miss, recompute
                raise KeyError("stale sidecar version")
            src.signal_peaks = d["peaks"]
            src.duration = d.get("duration") or src.duration
            led.set_source_state(source_id, SourceState.signalled)
            return led
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass                                       # corrupt/stale sidecar -> fall through to a real run
    sil = _run_ffmpeg(_silence_cmd(src.source_path))
    sc = _run_ffmpeg(_scene_cmd(src.source_path))
    peaks = parse_silences(sil.stderr) + parse_scene_changes(sc.stderr)
    # Theme 1 energy pass: a real loudness signal so peaks rank on impact, not a constant. It is an
    # ENHANCEMENT — it MUST fail soft (degrade to today's scoring), never quarantine a source. ffmpeg
    # is already proven present here (the two required passes ran), so a failure means astats is
    # unavailable/hung; either way apply_energy([], ...) leaves the peaks unchanged.
    try:
        er = _run_ffmpeg(energy_cmd(src.source_path))
        windows = parse_energy((er.stdout or "") + "\n" + (er.stderr or ""))
    except (ToolchainMissingError, subprocess.TimeoutExpired):
        windows = []
    peaks = apply_energy(peaks, windows)
    peaks.sort(key=lambda p: p["t"])
    src.signal_peaks = peaks
    if not src.duration:                              # FIX F76/F85 — guarantee duration here too
        _, _, dur = probe_dimensions(src.source_path)
        src.duration = dur or src.duration
    led.set_source_state(source_id, SourceState.signalled)
    # persist the sidecar so the in-lock commit pass skips the ffmpeg passes (Phase D). Best-effort:
    # a write failure just means the commit pass re-runs ffmpeg (today's behavior), never a crash.
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"v": _SIDECAR_V, "peaks": peaks, "duration": src.duration}, default=str))
    except OSError:
        pass
    return led
