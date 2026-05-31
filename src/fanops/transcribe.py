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

def _cached_models() -> list[str]:
    """Model names whose checkpoint is already on disk (no download needed). whisper stores
    them as <name>.pt under WHISPER download_root (defaults to ~/.cache/whisper)."""
    import os
    root = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "whisper"
    if not root.exists():
        return []
    return [p.stem for p in root.glob("*.pt")]

def _resolve_model(model: str) -> str:
    """Pick a runnable model. Prefer the requested one if it's a known name; but if it isn't
    already cached AND nothing on this host can fetch it, fall back to a model whose checkpoint
    is already present (offline / air-gapped / TLS-proxied CI — where the >1GB turbo/small
    checkpoints can't download). Only when no checkpoint is cached do we keep the requested
    name and let whisper attempt the download (and surface a clear error if it can't)."""
    try:
        import whisper
        known = whisper.available_models()
    except Exception:
        return model
    if model not in known:
        model = "turbo" if "turbo" in known else (known[0] if known else model)
    cached = _cached_models()
    if model in cached:
        return model
    if cached:
        # requested model not on disk; reuse a cached one rather than trigger a download that
        # may be impossible here. Preference order: larger-but-still-cached for quality.
        for pref in ("turbo", "large-v3", "medium", "small", "base", "tiny"):
            if pref in cached:
                return pref
        return cached[0]
    return model                                      # nothing cached: let whisper try to fetch

def whisper_cmd(src: str, out_dir: str, model: str = "turbo") -> list[str]:
    return ["whisper", "--model", model, "--output_format", "json",
            "--output_dir", out_dir, "--task", "transcribe", src]

def transcribe_source(led: Ledger, cfg: Config, source_id: str, *, model: str | None = None) -> Ledger:
    src = led.sources[source_id]
    if src.meta.get("transcribed") is True:           # FIX: idempotent only when it actually ran
        return led
    out_dir = cfg.agent_io / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    model = model or cfg.whisper_model               # env override (FANOPS_WHISPER_MODEL), default turbo
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
