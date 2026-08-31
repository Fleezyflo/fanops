# src/fanops/transcribe.py
"""Local Whisper transcription (free, offline, EN/AR). Shells a bounded subprocess, parses its
JSON into [{start,end,text}] + detected language. Distinguishes 'ran, no speech' (transcript
[], meta.transcribed=True) from 'not run' (transcript None) so a failed run can recover.
Missing JSON -> error state, never a crash.

Speech-trust (L1–L3, always-on — no env toggle): each segment gets a stamped trust_tier (full /
degraded / rejected). Production gates (subs burn, moment pick, hook excerpt, framing classify)
consume only full-tier segments via trusted_segments / window_has_trusted_speech /
excerpt_for_window. degraded = legacy cache missing ASR quality keys → _adopt_cached_transcript
refuses adoption and the next pass re-transcribes. rejected = empty text, script flap, or failed
decoder-quality L1 (avg_logprob / compression_ratio). no_speech_prob is stored but never a veto —
it is a window-level speech-vs-music prior and false-rejects sung/rapped lyrics.

real_transcript_signal is a SEPARATE E2E-only contract: it proves whisper ran on real audio
(whisper-shaped segments + ≥4 word tokens total), NOT per-segment trust. Do NOT substitute it
for segment_trusted / window_has_trusted_speech in production paths.

ENGINE: faster-whisper only (the [asr] extra, via the fanops._fwrun runner) at FANOPS_ASR_MODEL
(default **medium**) — strong on music/rap EN+AR; large-v3 is available as the max-accuracy opt-in
(int8 makes even large-v3 practical on CPU). Absent [asr] is SourceState.error / ToolchainMissingError.
There is no whisper-CLI fallback."""
from __future__ import annotations
import contextlib, json, subprocess, sys, time
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

def real_transcript_signal(transcript: list[dict]) -> bool:
    """True iff `transcript` is proof that REAL whisper ran on REAL audio — NOT that any one
    specific word survived (CI-2). E2E-only: do NOT use for subs, hooks, framing, or moment gates —
    use trusted_segments / window_has_trusted_speech / excerpt_for_window instead. Used by the
    real-tooling E2E in place of a brittle single-token check that bet on macOS `say`'s acoustics
    and failed under the Linux CI's espeak vocoder.

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
    """True iff the faster-whisper engine (the [asr] extra) is importable."""
    try: import faster_whisper; return True       # noqa: F401  (probe only)
    except ImportError: return False

def fw_cmd(src: str, out_dir: str, model: str, language: str = "") -> list[str]:
    # faster-whisper runner invocation (`python -m fanops._fwrun`). Same --model/--output_dir flags
    # and audio-LAST shape as whisper_cmd, so the per-source .json lookup and the engine-agnostic
    # transcribe tests don't care which engine ran. --language "" -> the runner auto-detects (EN+AR).
    return [sys.executable, "-m", "fanops._fwrun", "--model", model, "--language", language,
            "--output_dir", out_dir, src]

_SEGMENT_QUALITY_KEYS = ("avg_logprob", "no_speech_prob", "compression_ratio")
# Decoder-quality floors. no_speech_prob is stored (schema v2 / cache completeness) but is NOT a
# pass/fail input: faster-whisper copies a window-level speech-vs-music prior onto every segment in
# the decode chunk, so rap over a beat scores 0.8–0.9 while avg_logprob still says the lyrics are
# confident. VAD already dropped silence; L1 here is "did the decoder commit to this text".
_AVG_LOGPROB_MIN = -1.0
_COMPRESSION_RATIO_MAX = 2.4

def _segment_metadata_pass(seg: dict) -> bool:
    """L1: quality keys present, avg_logprob + compression_ratio in range. Partial keys -> False.
    no_speech_prob is required to be present (cache completeness) and is not thresholded."""
    if not all(k in seg for k in _SEGMENT_QUALITY_KEYS):
        return False
    try:
        if float(seg["avg_logprob"]) < _AVG_LOGPROB_MIN: return False
        if float(seg["compression_ratio"]) > _COMPRESSION_RATIO_MAX: return False
    except (TypeError, ValueError):
        return False
    return True

def _segment(s: dict) -> dict:
    """One transcript segment: {start,end,text}, plus `words` ([{word,start,end}]) when whisper
    emitted word timestamps (--word_timestamps). Optional quality keys (avg_logprob, no_speech_prob,
    compression_ratio) pass through when present — additive, old JSON without them is unchanged."""
    seg = {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
    words = s.get("words")
    if isinstance(words, list) and words and all(isinstance(w, dict) and "word" in w for w in words):
        seg["words"] = [{"word": w["word"], "start": w.get("start"), "end": w.get("end")} for w in words]
    for k in _SEGMENT_QUALITY_KEYS:
        if k in s:
            try: seg[k] = float(s[k])
            except (TypeError, ValueError): pass
    return seg

def _transcript_schema(segments: list[dict]) -> int:
    """Sidecar schema version: 2 when any segment carries ASR quality metadata."""
    for seg in segments or []:
        if any(k in seg for k in _SEGMENT_QUALITY_KEYS):
            return 2
    return 1

def _cache_is_quality_complete(data: dict) -> bool:
    """True when every non-empty cached segment carries all ASR quality keys."""
    for s in data.get("segments") or []:
        text = (s.get("text") or "").strip()
        if not text: continue
        if not all(k in s for k in _SEGMENT_QUALITY_KEYS):
            return False
    return True

def _trust_tier(seg: dict, *, src_lang: str | None = None) -> str:
    """Compose L1 metadata + L2 script coherence into full / degraded / rejected."""
    text = (seg.get("text") or "").strip()
    if not text:
        return "rejected"
    if not _segment_script_coherent(text, src_lang=src_lang):
        return "rejected"
    if all(k in seg for k in _SEGMENT_QUALITY_KEYS):
        if not _segment_metadata_pass(seg):
            return "rejected"
        return "full"
    return "degraded"

def _finalize_segments(raw: list[dict], src_lang: str | None) -> list[dict]:
    """Normalize raw whisper segments and stamp trust_tier + trusted on each."""
    out: list[dict] = []
    for s in raw or []:
        seg = _segment(s)
        tier = _trust_tier(seg, src_lang=src_lang)
        seg["trust_tier"] = tier
        seg["trusted"] = tier == "full"
        out.append(seg)
    return out

def _log_transcript_tiers(cfg: Config | None, source_id: str, segments: list[dict]) -> None:
    if not cfg: return
    full = degraded = rejected = 0
    for seg in segments or []:
        tier = seg.get("trust_tier", "rejected")
        if tier == "full": full += 1
        elif tier == "degraded": degraded += 1
        else: rejected += 1
    get_logger(cfg)("transcribe", source_id, "transcript_tiers", full=full, degraded=degraded, rejected=rejected)

_SCRIPT_LATIN_ON_AR = 0.70          # Latin-majority segment on an ar source -> junk transliteration
_SCRIPT_CJK_ON_EN_AR = 0.30         # CJK-majority segment on en/ar source -> junk hallucination
_SPEECH_MIN_WORDS = 2               # mirrors framing._window_has_speech bar

def _lang_base(tag: str | None) -> str | None:
    if not tag: return None
    base = tag.strip().lower().replace("_", "-").split("-", 1)[0]
    return base or None

def _script_counts(text: str) -> tuple[int, int, int]:
    """Return (alpha, latin, cjk) char counts for script-coherence checks."""
    alpha = latin = cjk = 0
    for c in text or "":
        if not c.isalpha(): continue
        alpha += 1
        o = ord(c)
        if '\u0600' <= c <= '\u06ff': pass                          # Arabic — not latin
        elif o < 128 or (0x00C0 <= o <= 0x024F): latin += 1         # Basic Latin + Latin-1 supplement
        elif 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF: cjk += 1
    return alpha, latin, cjk

def _segment_script_coherent(text: str, *, src_lang: str | None) -> bool:
    """L2: reject obvious script flaps (Arabic-as-Latin junk, CJK on en/ar sources). Fail-open when unknown."""
    base = _lang_base(src_lang)
    alpha, latin, cjk = _script_counts(text)
    if not alpha: return False
    if base == "ar" and latin / alpha > _SCRIPT_LATIN_ON_AR: return False
    if base in ("en", "ar") and cjk / alpha > _SCRIPT_CJK_ON_EN_AR: return False
    return True

def segment_trusted(seg: dict, *, src_lang: str | None = None) -> bool:
    """True only for full-trust segments (L1 metadata pass + L2 script coherence).
    Always recomputes from quality keys — a stored trust_tier is a snapshot, not authority
    (a stale rejected stamp from a previous formula must not freeze live lyrics as junk)."""
    return _trust_tier(seg, src_lang=src_lang) == "full"

def trusted_segments(transcript: list[dict] | None, *, src_lang: str | None = None) -> list[dict]:
    """Filter to full-trust segments only; None/[] -> []. Recomputes; ignores stored trust_tier."""
    return [s for s in (transcript or []) if segment_trusted(s, src_lang=src_lang)]

def window_has_trusted_speech(src, start: float, end: float) -> bool:
    """True when trusted transcript segments overlap [start,end) with >= _SPEECH_MIN_WORDS tokens."""
    lang = getattr(src, "language", None)
    words = 0
    for seg in trusted_segments(getattr(src, "transcript", None) or [], src_lang=lang):
        try:
            s, e = seg.get("start"), seg.get("end")
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)): continue
            if e <= start or s >= end: continue
            words += sum(1 for tok in (seg.get("text") or "").split() if any(c.isalpha() for c in tok))
        except (AttributeError, TypeError):
            continue
    return words >= _SPEECH_MIN_WORDS

def excerpt_for_window(src, start: float, end: float, *, max_chars: int = 240) -> str:
    """Join full-trust segment text overlapping [start,end); truncate to max_chars."""
    lang = getattr(src, "language", None)
    parts: list[str] = []
    for seg in trusted_segments(getattr(src, "transcript", None) or [], src_lang=lang):
        try:
            s, e = seg.get("start"), seg.get("end")
            if not isinstance(s, (int, float)) or not isinstance(e, (int, float)): continue
            if e <= start or s >= end: continue
            text = (seg.get("text") or "").strip()
            if text: parts.append(text)
        except (AttributeError, TypeError):
            continue
    joined = " ".join(parts)
    return joined if len(joined) <= max_chars else joined[:max_chars]

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
    return _fw_available()


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
                voc = isolate_vocals(src.source_path, str(out_dir / "vocals"))
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
    if not _fw_available():
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
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout_s)
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
