# src/fanops/transcribe.py
"""Local Whisper transcription (free, offline, EN/AR). Shells a bounded subprocess, parses its
JSON into [{start,end,text}] + detected language. Distinguishes 'ran, no speech' (transcript
[], meta.transcribed=True) from 'not run' (transcript None) so a failed run can recover.
Missing JSON -> error state, never a crash.

ENGINE: prefers faster-whisper (the [asr] extra, via the fanops._fwrun runner) at FANOPS_ASR_MODEL
(default **medium**) — strong on music/rap EN+AR; large-v3 is available as the max-accuracy opt-in
(int8 makes even large-v3 practical on CPU). FAILS OPEN to the legacy `whisper` CLI (turbo) when
faster-whisper is absent (CI / air-gapped), so transcription always runs."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SourceState
from fanops.vocals import isolate_vocals

# Hard bound on the whisper run — THE flock-critical timeout: transcribe_source is called INSIDE
# Ledger.transaction (pipeline.py), so an UNBOUNDED hang held the ledger lock against every cron
# pass, Studio write and recovery verb. 45min covers a long (~26min) source on CPU at the medium model.
_WHISPER_TIMEOUT = 2700.0

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
        # may be impossible here. Preference order: fast-and-cached first (turbo), then the largest cached fallbacks.
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
    # --word_timestamps True makes whisper emit per-segment word timings ([{word,start,end}]) so the
    # overlay can sync active captions word-by-word (without it the captions fall back to an even
    # split of each segment). Negligible extra cost.
    return ["whisper", "--model", model, "--output_format", "json", "--word_timestamps", "True",
            "--output_dir", out_dir, "--task", "transcribe", src]

def _fw_available() -> bool:
    """True iff the faster-whisper engine (the [asr] extra) is importable. When False,
    transcribe_source degrades to the legacy `whisper` CLI — fail-open, today's behavior."""
    try: import faster_whisper; return True       # noqa: F401  (probe only)
    except Exception: return False

def fw_cmd(src: str, out_dir: str, model: str, language: str = "") -> list[str]:
    # faster-whisper runner invocation (`python -m fanops._fwrun`). Same --model/--output_dir flags
    # and audio-LAST shape as whisper_cmd, so the per-source .json lookup and the engine-agnostic
    # transcribe tests don't care which engine ran. --language "" -> the runner auto-detects (EN+AR).
    return [sys.executable, "-m", "fanops._fwrun", "--model", model, "--language", language,
            "--output_dir", out_dir, src]

def _segment(s: dict) -> dict:
    """One transcript segment: {start,end,text}, plus `words` ([{word,start,end}]) when whisper
    emitted word timestamps (--word_timestamps). The words list is kept only when it's a non-empty
    list of dicts carrying a "word" key, so a schema quirk can never poison the overlay's sync."""
    seg = {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
    words = s.get("words")
    if isinstance(words, list) and words and all(isinstance(w, dict) and "word" in w for w in words):
        seg["words"] = [{"word": w["word"], "start": w.get("start"), "end": w.get("end")} for w in words]
    return seg

def transcribe_source(led: Ledger, cfg: Config, source_id: str, *, model: str | None = None) -> Ledger:
    src = led.sources[source_id]
    if src.meta.get("transcribed") is True:           # FIX: idempotent only when it actually ran
        return led
    out_dir = cfg.agent_io / "transcripts"
    # Phase D (out-of-lock): the whisper JSON is named by the source stem and is DETERMINISTIC per
    # source. A lock-free pre-warm pass runs whisper to this path BEFORE the ledger transaction; if that
    # artifact is already present + parseable, adopt it and SKIP the multi-minute subprocess (and vocal
    # isolation) entirely — this is what keeps whisper OUT of the lock. The stem is the SOURCE stem in
    # both engines (isolation moves vocals to "{source_stem}.mp3"), so it's stable. A corrupt/truncated
    # cache is NOT adopted: parse failure falls through to a real run (which overwrites it).
    cached = out_dir / f"{Path(src.source_path).stem}.json"
    if cached.exists():
        try:
            data = json.loads(cached.read_text())
            src.transcript = [_segment(s) for s in data.get("segments", [])]
            src.language = data.get("language")
            src.meta["transcribed"] = True
            led.set_source_state(source_id, SourceState.transcribed)
            return led
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            pass                                       # corrupt cache -> fall through to a real run
    out_dir.mkdir(parents=True, exist_ok=True)
    # Vocal isolation (the music-transcription fix): strip the beat with Demucs so Whisper reads the
    # LYRICS, not the instrumental. FAIL-OPEN — isolate_vocals returns the RAW path if demucs is
    # absent/fails, so this never blocks transcription. The isolated mp3 is moved next to the whisper
    # output under the SOURCE stem so the per-source .json lookup below stays unique + unchanged.
    audio = src.source_path
    if cfg.isolate_vocals:
        voc = isolate_vocals(src.source_path, str(out_dir / "vocals"))
        if voc != src.source_path:
            target = out_dir / f"{Path(src.source_path).stem}.mp3"
            # ECC fix #3: on a move failure (e.g. cross-device) fall back to the SOURCE path, NOT the
            # vocals path. The vocals stem ("vocals") made whisper write vocals.json, which the
            # per-source cache lookup ({source_stem}.json) never finds -> re-transcribe every run +
            # clobbered shared vocals.json. Source-stem fallback keeps the cache deterministic (we
            # lose vocal isolation only in this rare failure case — fail-open to the raw mix).
            try: Path(voc).replace(target); audio = str(target)
            except OSError: audio = src.source_path
    # Engine: prefer faster-whisper (FANOPS_ASR_MODEL, default medium) — the proven music
    # winner; fail open to the legacy `whisper` CLI (FANOPS_WHISPER_MODEL turbo) when the [asr] extra
    # is absent. Both write JSON named by the INPUT stem, so the parse below is engine-agnostic.
    if _fw_available():
        cmd = fw_cmd(audio, str(out_dir), model or cfg.asr_model, cfg.asr_language)
    else:
        cmd = whisper_cmd(audio, str(out_dir), _resolve_model(model or cfg.whisper_model))
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=_WHISPER_TIMEOUT)
    except (FileNotFoundError, OSError) as e:
        # whisper ABSENT from PATH (or unspawnable): subprocess.run raises before the process
        # starts, which check=False does not cover (it only suppresses a nonzero RETURNCODE).
        # Record SourceState.error gracefully — mirroring the no-JSON branch below — rather than
        # letting the raise escape to the pipeline as an opaque "FileNotFoundError: whisper".
        src.state = SourceState.error
        src.error_reason = f"toolchain missing: {cmd[0]} ({type(e).__name__})"
        return led
    except subprocess.TimeoutExpired:
        # whisper HUNG (corrupt audio, model wedged) and was killed at the bound — the lock-held
        # window stays finite. Same graceful shape as the branches above/below; `transcribed`
        # stays unset so a recovered source re-runs.
        src.state = SourceState.error
        src.error_reason = f"whisper timed out after {_WHISPER_TIMEOUT:.0f}s"
        return led
    js = out_dir / f"{Path(audio).stem}.json"        # whisper names its json by the INPUT stem
    if not js.exists():
        src.state = SourceState.error
        src.error_reason = f"whisper produced no JSON (rc={r.returncode}): {(r.stderr or '')[:200]}"
        return led
    try:
        data = json.loads(js.read_text())
        src.transcript = [_segment(s) for s in data.get("segments", [])]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
        # whisper killed mid-write (disk full, OOM) leaves TRUNCATED JSON; a schema drift loses
        # start/end/text keys. Same per-source shape as the absent/timeout/no-JSON branches above —
        # a bare JSONDecodeError named neither whisper nor the file (stage-6 audit).
        src.state = SourceState.error
        src.error_reason = f"whisper JSON malformed ({js.name}): {type(e).__name__}: {str(e)[:160]}"
        return led
    src.language = data.get("language")
    src.meta["transcribed"] = True
    led.set_source_state(source_id, SourceState.transcribed)
    return led
