# src/fanops/transcribe.py
"""Local Whisper transcription (free, offline, EN/AR). Shells out to `whisper`, parses its
JSON into [{start,end,text}] + detected language. Distinguishes 'ran, no speech' (transcript
[], meta.transcribed=True) from 'not run' (transcript None) so a failed run can recover.
Falls back turbo->small. Missing JSON -> error state, never a crash."""
from __future__ import annotations
import json, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState

def _resolve_model(model: str) -> str:
    try:
        import whisper
        if model in whisper.available_models():
            return model
    except Exception:
        pass
    return "small"

def whisper_cmd(src: str, out_dir: str, model: str = "turbo") -> list[str]:
    return ["whisper", "--model", model, "--output_format", "json",
            "--output_dir", out_dir, "--task", "transcribe", src]

def transcribe_source(led: Ledger, cfg: Config, source_id: str, *, model: str = "turbo") -> Ledger:
    src = led.sources[source_id]
    if src.meta.get("transcribed") is True:           # FIX: idempotent only when it actually ran
        return led
    out_dir = cfg.agent_io / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(whisper_cmd(src.source_path, str(out_dir), _resolve_model(model)),
                       check=False, capture_output=True, text=True)
    js = out_dir / f"{Path(src.source_path).stem}.json"
    if not js.exists():
        src.state = SourceState.error
        src.error_reason = f"whisper produced no JSON (rc={r.returncode}): {(r.stderr or '')[:200]}"
        return led
    data = json.loads(js.read_text())
    src.transcript = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                      for s in data.get("segments", [])]
    src.language = data.get("language")
    src.meta["transcribed"] = True
    led.set_source_state(source_id, SourceState.transcribed)
    return led
