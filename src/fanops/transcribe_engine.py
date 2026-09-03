# src/fanops/transcribe_engine.py
"""ASR engine subprocess orchestration: faster-whisper runner, vocal isolation, cache adopt, stage_lock."""
from __future__ import annotations
import contextlib, json, subprocess, sys, time
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.errors import ToolchainMissingError
from fanops.models import SourceState
from fanops.stage_lock import stage_lock
from fanops.speech_trust import (_cache_is_quality_complete, _finalize_segments,
                                 _transcript_schema)

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

def whisper_cmd(src: str, out_dir: str, model: str = "turbo", language: str = "") -> list[str]:
    # --word_timestamps True makes whisper emit per-segment word timings ([{word,start,end}]) so the
    # overlay can sync active captions word-by-word (without it the captions fall back to an even
    # split of each segment). Negligible extra cost.
    cmd = ["whisper", "--model", model, "--output_format", "json", "--word_timestamps", "True",
           "--output_dir", out_dir, "--task", "transcribe"]
    langs = [x for x in (language or "").replace(",", " ").split() if x]
    if len(langs) == 1: cmd += ["--language", langs[0]]
    return cmd + [src]

def fw_cmd(src: str, out_dir: str, model: str, language: str = "") -> list[str]:
    # faster-whisper runner invocation (`python -m fanops._fwrun`). Same --model/--output_dir flags
    # and audio-LAST shape as whisper_cmd, so the per-source .json lookup and the engine-agnostic
    # transcribe tests don't care which engine ran. --language "" -> the runner auto-detects (EN+AR).
    return [sys.executable, "-m", "fanops._fwrun", "--model", model, "--language", language,
            "--output_dir", out_dir, src]

def _log_transcript_tiers(cfg: Config | None, source_id: str, segments: list[dict]) -> None:
    if not cfg: return
    full = degraded = rejected = 0
    for seg in segments or []:
        tier = seg.get("trust_tier", "rejected")
        if tier == "full": full += 1
        elif tier == "degraded": degraded += 1
        else: rejected += 1
    get_logger(cfg)("transcribe", source_id, "transcript_tiers", full=full, degraded=degraded, rejected=rejected)

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


def _adopt_cached_transcript(led: Ledger, source_id: str, cached: Path, *, cfg: Config | None = None,
                             keep_state: bool = False) -> bool:
    """Adopt the on-disk whisper JSON into the in-memory Source row. Returns True iff adoption
    succeeded (the cache existed AND parsed AND had the expected shape). A corrupt/truncated cache
    or incomplete quality metadata returns False so the caller can fall through to a real run that
    overwrites it.

    Pulled out as a free function (instead of a closure inside transcribe_source) because the
    stage-lock re-check needs to call exactly the same adoption logic — DRY across the
    'before-lock fast path' and 'after-lock idempotent re-check'.
    keep_state=True refreshes transcript text without rewinding SourceState (a later-stage
    re-transcribe must not demote picks_decided back to transcribed)."""
    try:
        data = json.loads(cached.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not _cache_is_quality_complete(data):
        return False
    try:
        src = led.sources[source_id]
        lang = data.get("language")
        src.transcript = _finalize_segments(data.get("segments", []), lang)
        src.language = lang
        src.meta["transcribed"] = True
        if not keep_state:
            led.set_source_state(source_id, SourceState.transcribed)
        _log_transcript_tiers(cfg, source_id, src.transcript or [])
        return True
    except (KeyError, TypeError, AttributeError):
        return False


def asr_retry_marker(cfg: Config, source_path: str) -> Path:
    """One-shot marker: isolate+ASR already retried for a later-stage hook pass."""
    return cfg.agent_io / "transcripts" / f"{Path(source_path).stem}.asr_retry"


def adopt_transcript_keep_state(led: Ledger, cfg: Config, source_id: str) -> bool:
    """Refresh Source.transcript from the sidecar JSON without changing SourceState."""
    src = led.sources.get(source_id)
    if src is None or not src.source_path:
        return False
    cached = cfg.agent_io / "transcripts" / f"{Path(src.source_path).stem}.json"
    if not cached.exists():
        return False
    return _adopt_cached_transcript(led, source_id, cached, cfg=cfg, keep_state=True)


def _transcribe_toolchain_present() -> bool:
    """Cheap probe: faster-whisper ([asr] extra). No whisper-CLI fallback."""
    import fanops.transcribe as facade
    return facade._fw_available()


def transcribe_source(led: Ledger, cfg: Config, source_id: str, *, model: str | None = None,
                      in_lock: bool = False, force: bool = False) -> Ledger:
    src = led.sources[source_id]
    if not force and src.meta.get("transcribed") is True:           # idempotent only when it actually ran
        return led
    out_dir = cfg.agent_io / "transcripts"
    # M1 fast path: the whisper JSON is named by the source stem and is DETERMINISTIC per source.
    # If a previous producer already wrote it, adopt and short-circuit — no lock acquisition needed
    # for this happy path. A corrupt/truncated cache returns False here so we fall into the locked
    # produce path which will overwrite it. The stem is the SOURCE stem in both engines (isolation
    # moves vocals to "{source_stem}.mp3"), so the lookup is stable.
    cached = out_dir / f"{Path(src.source_path).stem}.json"
    if not force and cached.exists() and _adopt_cached_transcript(led, source_id, cached, cfg=cfg):
        try:
            from fanops.artifacts import stamp_stage
            rel = str(cached.relative_to(cfg.agent_io))
            stamp_stage(cfg, source_id, "transcribe", artifact=rel,
                        schema=_transcript_schema(src.transcript or []), sha256=src.sha256)
        except (OSError, ValueError): pass
        return led
    # MOL-122 / H10: the reducer calls this INSIDE the ledger flock only to ADOPT the producer's warm
    # JSON. On a cold cache (producer failed or hasn't run), running whisper here would hold the flock
    # for up to the duration-scaled timeout — DEFER to the lock-free producer pass. A genuinely-absent
    # toolchain fails in microseconds and must still quarantine — probe PATH cheaply first.
    if in_lock:
        if not _transcribe_toolchain_present():
            raise ToolchainMissingError(
                "faster-whisper not found — pip install -e '.[asr]' (in-lock probe)")
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
        if not force and cached.exists() and _adopt_cached_transcript(led, source_id, cached, cfg=cfg):
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
    import fanops.transcribe as facade
    # Vocal isolation (the music-transcription fix): strip the beat with Demucs so Whisper reads the
    # LYRICS, not the instrumental. Isolation ON + demucs/move failure -> SourceState.error (never
    # decode the mix). Isolation OFF leaves `audio` as the source path. The isolated mp3 is moved
    # next to the whisper output under the SOURCE stem so the per-source .json lookup stays unique.
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
            try:
                voc = facade.isolate_vocals(src.source_path, str(out_dir / "vocals"))
            except ToolchainMissingError as e:
                led.set_source_state(source_id, SourceState.error,
                                     error_reason=f"vocals isolation failed: {e}")
                return led
            # a demucs vocal stem exists -> framing.classify_window reads non-speech windows as MUSIC
            target = out_dir / f"{Path(src.source_path).stem}.mp3"
            # Move under the SOURCE stem so whisper writes {source_stem}.json (stable cache key).
            # Move failure used to fall back to the mix — that silent degrade is gone; error instead.
            try:
                Path(voc).replace(target); audio = str(target)
                src.meta["vocals_isolated"] = True
            except OSError as e:
                led.set_source_state(source_id, SourceState.error,
                                     error_reason=f"vocals isolation failed: {e}")
                return led
    # Engine: faster-whisper only. A missing [asr] extra used to fail open to Homebrew `whisper`,
    # which left no per-source JSON and stalled the source at catalogued. Refuse instead.
    if not facade._fw_available():
        src.meta["preserve_vocals_on_retry"] = False
        led.set_source_state(source_id, SourceState.error,
                             error_reason="faster-whisper not installed — pip install -e '.[asr]'")
        return led
    attempts = int(src.meta.get("whisper_timeout_attempts", 0))
    engine = "faster-whisper"
    used_model = model or "large-v3"
    cmd = fw_cmd(audio, str(out_dir), used_model, cfg.asr_language)
    timeout_s = _whisper_timeout(src.duration)
    t0 = time.monotonic()
    try:
        r = facade.subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout_s)
    except (FileNotFoundError, OSError) as e:
        # whisper ABSENT from PATH (or unspawnable): subprocess.run raises before the process
        # starts, which check=False does not cover (it only suppresses a nonzero RETURNCODE).
        # Record SourceState.error gracefully — mirroring the no-JSON branch below — rather than
        # letting the raise escape to the pipeline as an opaque "FileNotFoundError: whisper".
        # MOL-814: toolchain never reached whisper — demucs stems (if any) are not a whisper-only miss.
        src.meta["preserve_vocals_on_retry"] = False
        led.set_source_state(source_id, SourceState.error,
                              error_reason=f"toolchain missing: {cmd[0]} ({type(e).__name__})")
        return led
    except subprocess.TimeoutExpired:
        # whisper HUNG (corrupt audio, model wedged) and was killed at the timeout. Same graceful
        # shape as the branches above/below; `transcribed` stays unset so a recovered source
        # re-runs on the next pass. The stage_lock in the caller releases on this return.
        kills = attempts + 1
        src.meta["whisper_timeout_attempts"] = kills
        # MOL-814 / MOL-482: whisper-only failure — keep demucs stems across force-reset.
        src.meta["preserve_vocals_on_retry"] = True
        get_logger(cfg)("transcribe", source_id, "timeout_killed", model=used_model, timeout_s=timeout_s,
                        duration=src.duration or "")
        suffix = " (attempt 3/3)" if kills >= 3 else ""
        led.set_source_state(source_id, SourceState.error,
                             error_reason=f"whisper timed out after {timeout_s:.0f}s{suffix}")
        return led
    js = out_dir / f"{Path(audio).stem}.json"        # whisper names its json by the INPUT stem
    if not js.exists():
        # MOL-814 / MOL-482: whisper ran but emitted nothing — stems from a prior isolate are still good.
        src.meta["preserve_vocals_on_retry"] = True
        led.set_source_state(source_id, SourceState.error,
                              error_reason=f"whisper produced no JSON (rc={r.returncode}): {(r.stderr or '')[:200]}")
        return led
    try:
        data = json.loads(js.read_text())
        src.transcript = _finalize_segments(data.get("segments", []), data.get("language"))
        _log_transcript_tiers(cfg, source_id, src.transcript or [])
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
        # whisper killed mid-write (disk full, OOM) leaves TRUNCATED JSON; a schema drift loses
        # start/end/text keys. Same per-source shape as the absent/timeout/no-JSON branches above —
        # a bare JSONDecodeError named neither whisper nor the file (stage-6 audit).
        # MOL-814: deliberate — same as the old substring reader, malformed JSON does NOT preserve stems.
        src.meta["preserve_vocals_on_retry"] = False
        led.set_source_state(source_id, SourceState.error,
                              error_reason=f"whisper JSON malformed ({js.name}): {type(e).__name__}: {str(e)[:160]}")
        return led
    src.language = data.get("language")
    src.meta["transcribed"] = True
    led.set_source_state(source_id, SourceState.transcribed)
    # Provenance: record WHICH engine+model produced this transcript, and the measured wall-time
    # (wall_s vs duration = the real per-host RTF — the calibration data _ASR_MODEL_RTF needs).
    wall_s = round(time.monotonic() - t0, 1)
    get_logger(cfg)("transcribe", source_id, "transcribed", engine=engine, model=used_model,
                    wall_s=wall_s, duration=src.duration or "", language=src.language or "",
                    segments=len(src.transcript or []))
    try:
        from fanops.artifacts import stamp_stage
        rel = str(js.relative_to(cfg.agent_io))
        stamp_stage(cfg, source_id, "transcribe", artifact=rel,
                    schema=_transcript_schema(src.transcript or []), sha256=src.sha256,
                    extra={"engine": engine, "model": used_model, "wall_s": wall_s})
    except (OSError, ValueError): pass
    return led
