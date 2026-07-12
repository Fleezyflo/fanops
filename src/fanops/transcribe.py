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
import contextlib, json, shutil, subprocess, sys
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.errors import ToolchainMissingError
from fanops.models import SourceState
from fanops.stage_lock import stage_lock
from fanops.vocals import isolate_vocals

_DEFAULT_DEMUCS_MODEL = "htdemucs"

# Hard floor for the whisper subprocess timeout. The slow whisper run no longer holds the LEDGER flock
# (M1: it runs inside the per-(stage,source) stage_lock instead, which serializes only the SAME source
# against itself — concurrent_workers parallelism survives, the daemon's flock is uncontended). So the
# old "tight cap to protect the flock" reason is gone; the only thing the timeout has to do is bound a
# WEDGED whisper (corrupt audio, model deadlocked) so the producer never hangs forever. 2700s is the
# floor; longer sources scale up by _PREWARM_TIMEOUT_FACTOR (1.5x realtime) so a 58-min source actually
# finishes — the wedge that left a long source frozen at `catalogued` is closed by construction.
_WHISPER_TIMEOUT = 2700.0
_PREWARM_TIMEOUT_FACTOR = 1.5

def _whisper_timeout(duration_seconds: float | None) -> float:
    """The whisper subprocess bound. Length-scaled so a long source finishes; floored at the
    _WHISPER_TIMEOUT baseline. One mode — the lock_held two-mode contract (M1-pre) is gone; whisper now
    runs inside the per-(stage,source) stage_lock and never inside the ledger flock, so the old
    "in-lock tight cap" branch is dead by architecture."""
    if not duration_seconds:
        return _WHISPER_TIMEOUT
    return max(_WHISPER_TIMEOUT, float(duration_seconds) * _PREWARM_TIMEOUT_FACTOR)

def _cached_models(cfg: Config | None = None) -> list[str]:
    """Model names whose checkpoint is already on disk (no download needed). whisper stores
    them as <name>.pt under WHISPER download_root (defaults to ~/.cache/whisper)."""
    root = (cfg.whisper_cache_root if cfg else Path.home() / ".cache" / "whisper")
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

def whisper_cmd(src: str, out_dir: str, model: str = "turbo", language: str = "") -> list[str]:
    # --word_timestamps True makes whisper emit per-segment word timings ([{word,start,end}]) so the
    # overlay can sync active captions word-by-word (without it the captions fall back to an even
    # split of each segment). Negligible extra cost.
    cmd = ["whisper", "--model", model, "--output_format", "json", "--word_timestamps", "True",
           "--output_dir", out_dir, "--task", "transcribe"]
    langs = [x for x in (language or "").replace(",", " ").split() if x]
    if len(langs) == 1: cmd += ["--language", langs[0]]
    return cmd + [src]

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

def purge_source_artifacts(cfg: Config, source_id: str, source_path: str, *,
                           clip_ids: list[str] | None = None, preserve_vocals: bool = False) -> None:
    """MOL-471: delete on-disk transcribe/signals caches for a source so a force-retry cannot adopt stale
    JSON. Idempotent — missing paths are fine. Demucs vocal stem dirs live under transcripts/vocals/.
    Also clears framing, keyframes, manifests, and optional clip render fingerprints.
    MOL-482: when preserve_vocals=True, keep the demucs stem mp3 + htdemucs dir (whisper-only retry)."""
    import shutil
    stem = Path(source_path).stem
    out_dir = cfg.agent_io / "transcripts"
    for p in (out_dir / f"{stem}.json", cfg.agent_io / "signals" / f"{source_id}.json",
              cfg.agent_io / "manifests" / f"{source_id}.json",
              cfg.agent_io / "framing" / f"{source_id}.detect.json"):
        with contextlib.suppress(FileNotFoundError): p.unlink()
    if not preserve_vocals:
        with contextlib.suppress(FileNotFoundError): (out_dir / f"{stem}.mp3").unlink()
        demucs_stem = out_dir / "vocals" / _DEFAULT_DEMUCS_MODEL / stem
        if demucs_stem.exists(): shutil.rmtree(demucs_stem, ignore_errors=True)
    kf = cfg.agent_io / "keyframes" / source_id
    if kf.exists(): shutil.rmtree(kf, ignore_errors=True)
    for cid in clip_ids or ():
        with contextlib.suppress(FileNotFoundError):
            (cfg.clips / f"{cid}.render.json").unlink()


def _adopt_cached_transcript(led: Ledger, source_id: str, cached: Path) -> bool:
    """Adopt the on-disk whisper JSON into the in-memory Source row. Returns True iff adoption
    succeeded (the cache existed AND parsed AND had the expected shape). A corrupt/truncated cache
    returns False so the caller can fall through to a real run that overwrites it.

    Pulled out as a free function (instead of a closure inside transcribe_source) because the
    stage-lock re-check needs to call exactly the same adoption logic — DRY across the
    'before-lock fast path' and 'after-lock idempotent re-check'."""
    try:
        data = json.loads(cached.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    try:
        src = led.sources[source_id]
        src.transcript = [_segment(s) for s in data.get("segments", [])]
        src.language = data.get("language")
        src.meta["transcribed"] = True
        led.set_source_state(source_id, SourceState.transcribed)
        return True
    except (KeyError, TypeError, AttributeError):
        return False


def _transcribe_toolchain_present() -> bool:
    """Cheap PATH probe: faster-whisper ([asr] extra) OR legacy whisper CLI."""
    return _fw_available() or shutil.which("whisper") is not None


def transcribe_source(led: Ledger, cfg: Config, source_id: str, *, model: str | None = None,
                      in_lock: bool = False) -> Ledger:
    src = led.sources[source_id]
    if src.meta.get("transcribed") is True:           # idempotent only when it actually ran
        return led
    out_dir = cfg.agent_io / "transcripts"
    # M1 fast path: the whisper JSON is named by the source stem and is DETERMINISTIC per source.
    # If a previous producer already wrote it, adopt and short-circuit — no lock acquisition needed
    # for this happy path. A corrupt/truncated cache returns False here so we fall into the locked
    # produce path which will overwrite it. The stem is the SOURCE stem in both engines (isolation
    # moves vocals to "{source_stem}.mp3"), so the lookup is stable.
    cached = out_dir / f"{Path(src.source_path).stem}.json"
    if cached.exists() and _adopt_cached_transcript(led, source_id, cached):
        try:
            from fanops.artifacts import stamp_stage
            rel = str(cached.relative_to(cfg.agent_io))
            stamp_stage(cfg, source_id, "transcribe", artifact=rel, schema=1, sha256=src.sha256)
        except (OSError, ValueError): pass
        return led
    # MOL-122 / H10: the reducer calls this INSIDE the ledger flock only to ADOPT the producer's warm
    # JSON. On a cold cache (producer failed or hasn't run), running whisper here would hold the flock
    # for up to the duration-scaled timeout — DEFER to the lock-free producer pass. A genuinely-absent
    # toolchain fails in microseconds and must still quarantine — probe PATH cheaply first.
    if in_lock:
        if not _transcribe_toolchain_present():
            raise ToolchainMissingError(
                "whisper/faster-whisper not found — install [asr] or whisper CLI to transcribe (in-lock probe)")
        get_logger(cfg)("transcribe", source_id, "defer", reason="cold cache in-lock; deferring whisper to producer")
        return led
    out_dir.mkdir(parents=True, exist_ok=True)
    # M1 produce critical section: per-(stage,source) lock — only ONE producer for this source at a
    # time. A second producer for the SAME source blocks here, the first finishes and atomically
    # writes JSON, the second enters, _adopt_cached_transcript succeeds, returns. The "two whisper
    # subprocesses on one audio" race is now unconstructable by design. Concurrent sources do NOT
    # serialize (the lock is keyed on source_id).
    with stage_lock(cfg, stage="transcribe", key=source_id):
        # Re-check INSIDE the lock — this is the short-circuit that closes the race. The first
        # producer wrote the JSON; the second producer reaches this line and adopts. Crucially the
        # subprocess.run below NEVER executes in the second producer.
        if cached.exists() and _adopt_cached_transcript(led, source_id, cached):
            return led
        return _produce_transcript(led, cfg, source_id, src, out_dir, model)


def _produce_transcript(led: Ledger, cfg: Config, source_id: str, src, out_dir: Path,
                        model: str | None) -> Ledger:
    """The slow side of transcribe_source — runs vocal isolation + the whisper subprocess + parses
    the JSON. Called ONLY from inside the stage_lock critical section in transcribe_source, so a
    concurrent caller for the same source never executes this. Extracted as a helper to keep
    transcribe_source's lock structure (acquire / re-check / produce / return) legible.

    Side-effects (write JSON, mutate `src`) match the prior in-function body byte-for-byte; the
    only contract change is that callers no longer pass lock_held= and the timeout is the single
    length-scaled cap — both deliberate consequences of M1's architecture collapse."""
    # Vocal isolation (the music-transcription fix): strip the beat with Demucs so Whisper reads the
    # LYRICS, not the instrumental. FAIL-OPEN — isolate_vocals returns the RAW path if demucs is
    # absent/fails, so this never blocks transcription. The isolated mp3 is moved next to the whisper
    # output under the SOURCE stem so the per-source .json lookup below stays unique + unchanged.
    audio = src.source_path
    if cfg.isolate_vocals:
        stem_mp3 = out_dir / f"{Path(src.source_path).stem}.mp3"
        if stem_mp3.exists() and src.sha256:
            from fanops.artifacts import _load_manifest
            m = _load_manifest(cfg, source_id)
            if not m.get("sha256") or m["sha256"] == src.sha256:
                audio = str(stem_mp3); src.meta["vocals_isolated"] = True
        if audio == src.source_path:
            from fanops.pipeline_run import note_stage
            note_stage(cfg, "transcribe:demucs", source_id)
            voc = isolate_vocals(src.source_path, str(out_dir / "vocals"))
            if voc != src.source_path:
                src.meta["vocals_isolated"] = True            # a demucs vocal stem exists -> framing.classify_window
                                                              # reads non-speech windows as MUSIC (wider lock), not silence
                target = out_dir / f"{Path(src.source_path).stem}.mp3"
                # ECC fix #3: on a move failure (e.g. cross-device) fall back to the SOURCE path, NOT the
                # vocals path. The vocals stem ("vocals") made whisper write vocals.json, which the
                # per-source cache lookup ({source_stem}.json) never finds -> re-transcribe every run +
                # clobbered shared vocals.json. Source-stem fallback keeps the cache deterministic (we
                # lose vocal isolation only in this rare failure case — fail-open to the raw mix).
                try: Path(voc).replace(target); audio = str(target)
                except OSError: audio = src.source_path
    # Engine: prefer faster-whisper at a DURATION-AWARE model (cfg.asr_model_for with timeout_attempts
    # for retry downgrade after prior kills). Fail open to the legacy `whisper` CLI when the [asr] extra is
    # absent — and that fallback is ALSO duration-aware (cfg.whisper_model_for, audit c0-f2).
    attempts = int(src.meta.get("whisper_timeout_attempts", 0))
    if _fw_available():
        used_model = model or cfg.asr_model_for(src.duration, timeout_attempts=attempts)
        cmd = fw_cmd(audio, str(out_dir), used_model, cfg.asr_language)
    else:
        used_model = model or cfg.whisper_model_for(src.duration, timeout_attempts=attempts)
        cmd = whisper_cmd(audio, str(out_dir), _resolve_model(used_model), cfg.asr_language)
    timeout_s = _whisper_timeout(src.duration)
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout_s)
    except (FileNotFoundError, OSError) as e:
        # whisper ABSENT from PATH (or unspawnable): subprocess.run raises before the process
        # starts, which check=False does not cover (it only suppresses a nonzero RETURNCODE).
        # Record SourceState.error gracefully — mirroring the no-JSON branch below — rather than
        # letting the raise escape to the pipeline as an opaque "FileNotFoundError: whisper".
        src.state = SourceState.error
        src.error_reason = f"toolchain missing: {cmd[0]} ({type(e).__name__})"
        return led
    except subprocess.TimeoutExpired:
        # whisper HUNG (corrupt audio, model wedged) and was killed at the timeout. Same graceful
        # shape as the branches above/below; `transcribed` stays unset so a recovered source
        # re-runs on the next pass. The stage_lock in the caller releases on this return.
        kills = attempts + 1
        src.meta["whisper_timeout_attempts"] = kills
        get_logger(cfg)("transcribe", source_id, "timeout_killed", model=used_model, timeout_s=timeout_s,
                        duration=src.duration or "")
        src.state = SourceState.error
        suffix = " (attempt 3/3)" if kills >= 3 else ""
        src.error_reason = f"whisper timed out after {timeout_s:.0f}s{suffix}"
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
    try:
        from fanops.artifacts import stamp_stage
        rel = str(js.relative_to(cfg.agent_io))
        stamp_stage(cfg, source_id, "transcribe", artifact=rel, schema=1, sha256=src.sha256)
    except (OSError, ValueError): pass
    return led
