# src/fanops/vocals.py
"""Local vocal isolation (Demucs) — strip the instrumental so Whisper transcribes the LYRICS, not
the beat. This is the single biggest lever for music/rap transcription accuracy: on real clips,
removing the beat turned near-gibberish Arabic ('ورلستارا') into coherent lyrics ('ورا الستارة')
and fixed clear English errors ('won't'->'want', 'Swing'->'Swear'). Free, on-machine, no API.

OPTIONAL + FAIL-OPEN by contract: if demucs is absent, can't fetch its model, hangs, or fails,
isolate_vocals returns the ORIGINAL audio path so transcription degrades to today's behavior and
never breaks. Two environment gotchas (both solved here so production doesn't hit them):
  1. macOS framework Python often can't verify the TLS cert when demucs fetches its model on first
     use ([SSL: CERTIFICATE_VERIFY_FAILED]) -> point SSL_CERT_FILE/REQUESTS_CA_BUNDLE at certifi.
  2. torchaudio 2.x routes .save() through torchcodec (not installed) -> write the stem as MP3
     (lameenc) via --mp3 instead, which Whisper reads fine.
"""
from __future__ import annotations
import logging
import os, subprocess
from pathlib import Path

from fanops.config import certifi_ssl_env

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
    """`demucs --two-stems=vocals --mp3 -n <model> -o <out> <audio>`. --two-stems=vocals splits only
    vocals vs the rest (faster than the 4-stem default); --mp3 writes via lameenc (avoids the
    torchcodec save path). Output lands at <out>/<model>/<audio-stem>/vocals.mp3."""
    return ["demucs", "--two-stems=vocals", "--mp3", "-n", model, "-o", out_dir, audio_path]


def isolate_vocals(audio_path: str, out_dir: str, *, model: str = _DEFAULT_MODEL) -> str:
    """Return a path to the isolated-vocals MP3 for `audio_path`, or the ORIGINAL `audio_path` if
    isolation is unavailable or fails (FAIL-OPEN — never raises, never blocks transcription). Shells
    demucs bounded by _DEMUCS_TIMEOUT with the cert-fixed env."""
    try:
        r = subprocess.run(demucs_cmd(audio_path, out_dir, model=model), check=False,
                           capture_output=True, text=True, timeout=_DEMUCS_TIMEOUT, env=_demucs_env())
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        # demucs absent / unspawnable / hung -> raw audio. Logged so a silent isolation skip is
        # diagnosable post-hoc (a source transcribed off the raw mix loses the biggest ASR lever).
        logger.warning("isolate_vocals fail-open (%s): %s: %s — transcribing the raw mix",
                       audio_path, type(exc).__name__, str(exc)[:160])
        return audio_path
    if r.returncode != 0:                    # model fetch blocked / separation failed -> raw audio
        logger.warning("isolate_vocals fail-open (%s): demucs rc=%s: %s — transcribing the raw mix",
                       audio_path, r.returncode, (r.stderr or "")[-300:].strip())
        return audio_path
    vocals = Path(out_dir) / model / Path(audio_path).stem / "vocals.mp3"
    if not vocals.exists():                  # rc 0 but no stem written (schema drift) -> raw audio
        logger.warning("isolate_vocals fail-open (%s): demucs rc=0 but %s missing — transcribing the raw mix",
                       audio_path, vocals)
        return audio_path
    return str(vocals)
