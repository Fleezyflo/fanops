# src/fanops/signals.py
"""Free, local signal pass: ffmpeg silencedetect (speech onsets) + scdet (scene cuts).
scdet prints lavfi.scd.score/time on stderr at -loglevel info — showinfo does NOT print a
scene score (the v1 bug). Optional loudness (ebur128) can be added later; silence+scene
cover beat drops and visual cuts."""
from __future__ import annotations
import re, subprocess
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState
from fanops.ingest import probe_dimensions

_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")
_SCD = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+),\s*lavfi\.scd\.time:\s*([0-9.]+)")

def parse_silences(stderr: str) -> list[dict]:
    return [{"t": float(m), "kind": "speech_resume", "score": 0.5}
            for m in _SIL_END.findall(stderr)]

def parse_scene_changes(stderr: str) -> list[dict]:
    return [{"t": float(t), "kind": "scene_cut", "score": float(score)}
            for score, t in _SCD.findall(stderr)]

def _silence_cmd(src: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-i", src, "-af",
            "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"]

def _scene_cmd(src: str) -> list[str]:
    # scdet at info loglevel emits lavfi.scd.score/time lines on stderr.
    return ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", src, "-vf",
            "scdet=threshold=10", "-f", "null", "-"]

def detect_signals(led: Ledger, cfg: Config, source_id: str) -> Ledger:
    src = led.sources[source_id]
    sil = subprocess.run(_silence_cmd(src.source_path), check=False, capture_output=True, text=True)
    sc = subprocess.run(_scene_cmd(src.source_path), check=False, capture_output=True, text=True)
    peaks = parse_silences(sil.stderr) + parse_scene_changes(sc.stderr)
    peaks.sort(key=lambda p: p["t"])
    src.signal_peaks = peaks
    if not src.duration:                              # FIX F76/F85 — guarantee duration here too
        _, _, dur = probe_dimensions(src.source_path)
        src.duration = dur or src.duration
    led.set_source_state(source_id, SourceState.signalled)
    return led
