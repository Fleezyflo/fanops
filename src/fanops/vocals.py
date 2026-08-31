# src/fanops/vocals.py
"""Local vocal isolation (Demucs) — strip the instrumental so Whisper transcribes the LYRICS, not
the beat. This is the single biggest lever for music/rap transcription accuracy: on real clips,
removing the beat turned near-gibberish Arabic ('ورلستارا') into coherent lyrics ('ورا الستارة')
and fixed clear English errors ('won't'->'want', 'Swing'->'Swear'). Free, on-machine, no API.

OPTIONAL but fail-closed when isolation is requested: if demucs is absent, can't fetch its model,
hangs, or fails, isolate_vocals raises ToolchainMissingError so the caller errors the source rather
than Whisper-decoding the mix. Two environment gotchas (both solved here so production doesn't hit
them):
  1. macOS framework Python often can't verify the TLS cert when demucs fetches its model on first
     use ([SSL: CERTIFICATE_VERIFY_FAILED]) -> point SSL_CERT_FILE/REQUESTS_CA_BUNDLE at certifi.
  2. torchaudio 2.x routes .save() through torchcodec (not installed) -> write the stem as MP3
     (lameenc) via --mp3 instead, which Whisper reads fine.
"""
from __future__ import annotations
import logging
import os, subprocess, sys
from pathlib import Path

from fanops.config import certifi_ssl_env
from fanops.errors import ToolchainMissingError

logger = logging.getLogger(__name__)

# Same flock-critical bound as the whisper run (clip.py / transcribe.py): demucs runs INSIDE the
# transcribe pass's ledger transaction, so an unbounded hang would hold the lock. ~30s/clip on CPU
# in practice; 30min is generous headroom for a long source.
_DEMUCS_TIMEOUT = 1800.0
_DEFAULT_MODEL = "htdemucs"     # demucs' default hybrid-transformer model; robust + good vocal SDR


def _demucs_env() -> dict:
    """Subprocess env carrying the macOS SSL cert fix. demucs downloads its checkpoint over https on
    first use; the framework Python frequently can't verify the cert."""
    return certifi_ssl_env(dict(os.environ), logger=logger)


def demucs_cmd(audio_path: str, out_dir: str, *, model: str = _DEFAULT_MODEL) -> list[str]:
    """`python -m demucs --two-stems=vocals --mp3 -n <model> -o <out> <audio>`. Same interpreter as
    fanops (the [asr] extra), never a PATH `demucs` binary — launchd/Studio PATH does not include
    the venv and that FileNotFoundError fail-opened every live source onto the raw mix.
    --two-stems=vocals splits only vocals vs the rest (faster than the 4-stem default); --mp3 writes
    via lameenc (avoids the torchcodec save path). Output lands at <out>/<model>/<audio-stem>/vocals.mp3."""
    return [sys.executable, "-m", "demucs", "--two-stems=vocals", "--mp3", "-n", model, "-o", out_dir, audio_path]


def isolate_vocals(audio_path: str, out_dir: str, *, model: str = _DEFAULT_MODEL) -> str:
    """Return a path to the isolated-vocals MP3 for `audio_path`. Raises ToolchainMissingError if
    isolation is unavailable or fails (fail-closed — never return the mix). Shells
    `python -m demucs` bounded by _DEMUCS_TIMEOUT with the cert-fixed env."""
    try:
        r = subprocess.run(demucs_cmd(audio_path, out_dir, model=model), check=False,
                           capture_output=True, text=True, timeout=_DEMUCS_TIMEOUT, env=_demucs_env())
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        # demucs absent / unspawnable / hung — log then refuse (never return the mix).
        logger.warning("isolate_vocals fail-open (%s): %s: %s — refusing to transcribe the mix",
                       audio_path, type(exc).__name__, str(exc)[:160])
        raise ToolchainMissingError(
            f"demucs unavailable ({type(exc).__name__}: {str(exc)[:160]})") from exc
    if r.returncode != 0:                    # model fetch blocked / separation failed
        tail = (r.stderr or "")[-300:].strip()
        logger.warning("isolate_vocals fail-open (%s): demucs rc=%s: %s — refusing to transcribe the mix",
                       audio_path, r.returncode, tail)
        raise ToolchainMissingError(f"demucs rc={r.returncode}: {tail}")
    vocals = Path(out_dir) / model / Path(audio_path).stem / "vocals.mp3"
    if not vocals.exists():                  # rc 0 but no stem written (schema drift)
        logger.warning("isolate_vocals fail-open (%s): demucs rc=0 but %s missing — refusing to transcribe the mix",
                       audio_path, vocals)
        raise ToolchainMissingError(f"demucs rc=0 but {vocals} missing")
    return str(vocals)
