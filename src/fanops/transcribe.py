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

def real_transcript_signal(transcript: list[dict]) -> bool:
    """True iff `transcript` is proof that REAL whisper ran on REAL audio — NOT that any one
    specific word survived (CI-2). Used by the real-tooling E2E in place of a brittle single-token
    check that bet on macOS `say`'s acoustics and failed under the Linux CI's espeak vocoder.

    The contract has two parts, both required, so the check is robust across TTS engines yet still
    rejects a fake/empty/stub transcript (the v1 bug this E2E guards against — "false safety is
    worse than honest absence"):
      1. STRUCTURE — at least one segment with whisper's real shape: numeric start/end and
         end > start (a fabricated string with no timing is not whisper output).
      2. SUBSTANCE — the joined text has >= 4 alphabetic word tokens (a one-word stub, which a
         naive `len(text) > 0` would wrongly accept, is rejected).
    A robust *content* anchor (the word "anymore", which survives both `say` and espeak in the
    real run logs) is asserted by the E2E/its unit guard directly against the text, not here, so
    this helper stays vocoder-agnostic.
    """
    import re
    has_real_segment = any(
        isinstance(seg.get("start"), (int, float))
        and isinstance(seg.get("end"), (int, float))
        and seg["end"] > seg["start"]
        for seg in transcript
    )
    if not has_real_segment:
        return False
    joined = " ".join(str(seg.get("text", "")) for seg in transcript)
    words = re.findall(r"[^\W\d_]+", joined)             # alphabetic tokens (Unicode-aware: EN+AR)
    return len(words) >= 4

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
    cmd = whisper_cmd(src.source_path, str(out_dir), _resolve_model(model))
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except (FileNotFoundError, OSError) as e:
        # whisper ABSENT from PATH (or unspawnable): subprocess.run raises before the process
        # starts, which check=False does not cover (it only suppresses a nonzero RETURNCODE).
        # Record SourceState.error gracefully — mirroring the no-JSON branch below — rather than
        # letting the raise escape to the pipeline as an opaque "FileNotFoundError: whisper".
        src.state = SourceState.error
        src.error_reason = f"toolchain missing: {cmd[0]} ({type(e).__name__})"
        return led
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
