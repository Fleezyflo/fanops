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

ENGINE: faster-whisper only (the [asr] extra, via the fanops._fwrun runner). Default model is
**large-v3** (`used_model = model or "large-v3"`) — never a smaller duration/timeout degrade; callers
may pass `model=` to override. Absent [asr] is SourceState.error / ToolchainMissingError.
There is no whisper-CLI fallback."""
from __future__ import annotations
import subprocess
from fanops.vocals import isolate_vocals
from fanops.speech_trust import (_AVG_LOGPROB_MIN, _COMPRESSION_RATIO_MAX, _cache_is_quality_complete,
                                 _finalize_segments, _segment, _segment_metadata_pass, _trust_tier,
                                 excerpt_for_window, real_transcript_signal, segment_trusted,
                                 trusted_segments, window_has_trusted_speech)
from fanops.transcribe_engine import (_PREWARM_TIMEOUT_FACTOR, _WHISPER_TIMEOUT, _adopt_cached_transcript,
                                      _fw_available, _produce_transcript, _transcribe_toolchain_present,
                                      _whisper_timeout, adopt_transcript_keep_state, asr_retry_marker,
                                      fw_cmd, purge_source_artifacts, transcribe_source, whisper_cmd)

__all__ = [
    "_AVG_LOGPROB_MIN",
    "_COMPRESSION_RATIO_MAX",
    "_PREWARM_TIMEOUT_FACTOR",
    "_WHISPER_TIMEOUT",
    "_adopt_cached_transcript",
    "_cache_is_quality_complete",
    "_finalize_segments",
    "_fw_available",
    "_produce_transcript",
    "_segment",
    "_segment_metadata_pass",
    "_transcribe_toolchain_present",
    "_trust_tier",
    "_whisper_timeout",
    "adopt_transcript_keep_state",
    "asr_retry_marker",
    "excerpt_for_window",
    "fw_cmd",
    "isolate_vocals",
    "purge_source_artifacts",
    "real_transcript_signal",
    "segment_trusted",
    "subprocess",
    "transcribe_source",
    "trusted_segments",
    "whisper_cmd",
    "window_has_trusted_speech",
]
