# Pack: C2 — ingest & source acquisition

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C2-F01 | low | swallow | closed on this tree | `vocals._demucs_env` returns `certifi_ssl_env` and does not catch | `vocals._demucs_env` |
| C2-F02 | high | dead-resume | closed on this tree | A transcript sidecar counts as a finished stage only when it is adoptable (`_cache_is_quality_complete` plus finalize). Later disk stages do not resume past transcribe without that. A legacy segments list, even beside a signals sidecar, rewinds a transient error to `catalogued` so the next pass re-runs ASR. | `artifacts._valid_transcript_json` |
| C2-F03 | low | doc-rot | closed on this tree | `config.isolate_vocals` states that a missing, timed-out, nonzero, or stem-less Demucs raises. The source is set to error. The mix is not transcribed | `config.isolate_vocals` |
| C2-F04 | high | scan-leak | closed on this tree | Native `stage_inbox_candidates` skips a child `.pull` (the URL stage and its `.ingested`). A stranded pull and a disposed pull archive are not minted `source_origin=drop` and are not moved into `01_inbox/.ingested`. A scan whose inbox is `.pull` still catalogues that stage. | `ingest.stage_inbox_candidates` |

## Evidence commands run

```
rg 'except Exception' src/fanops/vocals.py
git ls-files 'src/fanops/ingest.py'
tests/test_ingest.py tests/test_transcribe.py
```

## Full checklist 2026-10-17

Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. C2 sources matched HEAD (`git diff --stat HEAD` empty on the fourteen module paths) except the C2-F02 edit in `artifacts.py` after the read. `config.py` matches HEAD. `doctor.py` is dirty; the toolchain lines below are from `git show HEAD:src/fanops/doctor.py`. No aliases for these symbols in `src/fanops/CLAUDE.md`. Nothing deleted.

Verdict: a fresh inbox drop still reaches a transcript when ffmpeg, ffprobe, faster-whisper, and demucs are present. C2-F01 stays closed. C2-F02 was a dead resume and is fixed in the working tree. C2-F03 is closed: the `config.isolate_vocals` docstring matches the raise.

### Routes / entry points

- Inbox catalogue: `ingest.stage_inbox_candidates` → `ingest_staged` → `_archive_staged`. Callers: `pipeline.advance`, `cli.cmd_ingest`, `studio.actions_run.catalogue_inbox` / `run_ingest` / `run_pull` / `run_ingest_thirdparty`.
- URL pull: `ingest.download_url` (yt-dlp into `01_inbox/.pull`, no ledger lock) then the same stage/mint on that directory. Callers: `cli.cmd_pull`, `actions_run.run_pull`.
- Pre-inbox review: `discover.discover` / `discover.intake`. Callers: `cli.cmd_discover`, `cli.cmd_intake`. Not on the default drop path.
- Transcript: `produce.run_all` → `transcribe_source` (lock-free). The reducer calls `transcribe_source(..., in_lock=True)`, which adopts a warm JSON or defers. Engine body is `transcribe_engine._produce_transcript`. `transcribe.py` re-exports it.
- `ingest_drops` is the one-shot wrapper (stage + mint + archive) used by tests and any caller that omits a pre-staged inbox.

### Data writes

- Media bytes: copy into `cfg.sources/{src_id}{ext}` (`_stage_candidate`, tmp + `os.replace`). Disposed inbox files move to `{inbox}/.ingested/` only after the ledger transaction commits.
- Ledger: `Ledger.add_source` in `_mint_candidate`. Birth state is `pending` when `FANOPS_QUEUE_GATE` is on (default), else `catalogued`.
- Transcript JSON: `fanops._fwrun.transcribe_to_json` writes `04_agent_io/transcripts/{stem}.json` via tmp + `os.replace`. Demucs stem moves to `{stem}.mp3` beside it. Advisory manifest: `artifacts.stamp_stage` → `04_agent_io/manifests/{source_id}.json`.
- Review control files: `discover` writes `00_review/manifest.json` and thumbnails; `intake` writes `00_review/intaken.json` and copies the original into `01_inbox`. Both use `write_json_atomic`. No ledger flock (documented single-operator).
- Shard parts: `ingest_shard.shard_file` stream-copies `{stem}-pNN{ext}` next to the drop. Failure unlinks parts and the original is catalogued with `degraded_reason=shard_failed`.
- Purge: `purge_source_artifacts` unlinks transcript/signals/manifest/framing/keyframes/render fingerprints. `preserve_vocals=True` keeps the demucs stem.

### External calls

- `ffprobe` — `media_probe._run_ffprobe` (dimensions, video-stream). Timeout 30s.
- `ffmpeg` — `ingest_shard` silence detect + stream-copy; `discover.make_thumbnail` (60s). `audio_energy.energy_cmd` and `frames` parsing are pure; the subprocess lives in signals/clip.
- `yt-dlp` — `download_url`, timeout 600s, `--no-playlist`, merge to mp4.
- `python -m demucs` — `vocals.isolate_vocals`, timeout 1800s, env from `_demucs_env`.
- `python -m fanops._fwrun` — `fw_cmd` inside `_produce_transcript`. Timeout is `max(2700, duration * 1.5)`. No whisper-CLI fallback on this HEAD (`whisper_cmd` is still built and tested; production does not run it).
- No C2 HTTP client. First-use model download is inside demucs / faster-whisper. `certifi_ssl_env` sets `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` when certifi imports; `ImportError` logs and returns the base env.

### Env

| Name | Default on HEAD | Effect |
|------|-----------------|--------|
| `FANOPS_QUEUE_GATE` | on | New sources born `pending` until Make clips |
| `FANOPS_SOURCE_SHARD_MIN` | 45 (minutes) | Native files longer than this are shard candidates. `0` disables |
| `FANOPS_ISOLATE_VOCALS` | on | Demucs before ASR |
| `FANOPS_ASR_MODEL` | unset → `asr_model_for` starts at large-v3 and steps down | Explicit pin wins |
| `FANOPS_ASR_LANGUAGE` | `en,ar` | Passed to `_fwrun` |
| `FANOPS_WHISPER_MODEL` | `turbo` | Legacy CLI selector. The live engine does not read it |

### Health / failure posture

- ffprobe missing: `ToolchainMissingError` (clean exit 2). ffprobe timeout: `has_video_stream` returns `None`, file stays in the inbox, log `skipped` / `probe_unavailable`. No video stream: archived, log `no_video_stream`. Copy failure: log `copy_failed`, file stays, not archived.
- `probe_failed` (0×0) is `degraded_reason` on the source. `_reprobe_degraded` clears it when a later probe returns real dimensions. Studio library reads `degraded_reason`.
- yt-dlp missing: `ToolchainMissingError`. Nonzero yt-dlp: `DownloadError` with the stderr tail. Hung download: `TimeoutExpired` propagates.
- Demucs missing, timeout, nonzero, or no stem: `isolate_vocals` logs then raises `ToolchainMissingError`. `_produce_transcript` sets `SourceState.error` / `vocals isolation failed`. It does not transcribe the mix. C2-F01: `_demucs_env` returns `certifi_ssl_env` and has no `except`.
- faster-whisper missing: `SourceState.error` / `faster-whisper not installed`. In-lock cold cache with the extra present: log `defer`, return, producer runs outside the flock. In-lock and the extra absent: `ToolchainMissingError`.
- Whisper timeout, no JSON, or malformed JSON: `SourceState.error` with a reason. `artifacts.is_transient_error` auto-resumes timeouts and refuses toolchain-missing, no-JSON, and `attempt 3/3`.
- `discover.candidate_meta` logs a non-toolchain probe error at debug and still lists the file. `make_thumbnail` returns False with no log if ffmpeg is missing or times out. Both are pinned by `tests/test_discover.py`.
- `stamp_stage` failures in `transcribe_engine` are `except (OSError, ValueError): pass`. The manifest is advisory; the JSON sidecar is the authority. Not a dropped transcript.
- Shard ffmpeg failure is swallowed in `shard_file` and surfaced as `degraded_reason=shard_failed` (the original file is still catalogued). Cleanup failures log `shard_cleanup_failed`.
- Doctor on HEAD (`doctor.py` 404–412) checks ffmpeg, ffprobe, faster-whisper, and yt-dlp. It does not check demucs. A missing demucs is visible as `SourceState.error`, not as a doctor row.
- Stream-copy shard command (`-ss` before `-i`, `-to` segment length, `-c copy`) was probed on ffmpeg 9.0.2. With a keyframe at the cut, the second segment is ~3s of a 6s source. Without an interior keyframe, stream copy cannot split and the second part is the whole file. That is GOP-bounded copy, not a wrong `-to`. Not filed.

### Launch-critical path: inbox drop → transcript

1. File lands in `01_inbox` (drop, Studio upload, or `intake`).
2. `stage_inbox_candidates` sweeps partials, skips PII names and native stills, probes for a video stream, optionally shards, hashes, copies to `02_sources`.
3. `ingest_staged` mints the source. Default queue gate parks it at `pending`. Nothing is transcribed yet.
4. Make clips (`release_batch` / `release_all_held`) flips `pending` → `catalogued` and runs prepare.
5. `produce._produce_one` calls `transcribe_source`. Isolation on: demucs, then `fw_cmd`. Success writes the JSON and sets `transcribed` on the producer’s private ledger (discarded). The JSON remains.
6. The reduce transaction adopts that JSON (`in_lock=True`) and moves the real row to `transcribed`.
7. Failure sets `SourceState.error` and `error_reason`. The next `advance` adopts a quality-complete sidecar or, for a transient error with no adoptable sidecar, rewinds to `catalogued` (C2-F02).

C2-F02 on HEAD: `_valid_transcript_json` treated any `segments` list as done. `_adopt_cached_transcript` refuses a list whose text has no `avg_logprob` / `compression_ratio`. `reconcile_source_progress` then promoted the row to `transcribed` or `signalled` with an empty transcript, and later passes never called ASR. Working tree: the sidecar must be adoptable, and later disk stages do not count without it. Repro (not pytest): legacy JSON plus a v3 signals sidecar → `infer_resume_stage` is `None`, state `catalogued`, transcript `None`. The same row with `talk_seg` still infers `signalled`.

C2-F03 is closed. `config.isolate_vocals` now states that a missing, timed-out, nonzero, or stem-less Demucs raises `ToolchainMissingError`, the source is set to error, and the mix is not transcribed. `vocals.isolate_vocals` still logs the historical token `fail-open` and then raises. `tests/test_vocals.py` expects `ToolchainMissingError` and pins that log token. `vocals.py` was not edited.

### Commands run

```
git rev-parse HEAD
git status --short --branch
git log --oneline --decorate -5
git diff --stat origin/main...HEAD
git diff --stat HEAD -- src/fanops/ingest.py src/fanops/ingest_shard.py src/fanops/discover.py src/fanops/transcribe.py src/fanops/transcribe_engine.py src/fanops/vocals.py src/fanops/media_probe.py src/fanops/frames.py src/fanops/audio_energy.py src/fanops/bands.py src/fanops/intro_match.py src/fanops/speech_trust.py src/fanops/adjust.py src/fanops/artifacts.py
git ls-tree -l HEAD src/fanops/ingest.py src/fanops/ingest_shard.py src/fanops/discover.py src/fanops/transcribe.py src/fanops/transcribe_engine.py src/fanops/vocals.py src/fanops/media_probe.py src/fanops/frames.py src/fanops/audio_energy.py src/fanops/bands.py src/fanops/intro_match.py src/fanops/speech_trust.py src/fanops/adjust.py src/fanops/artifacts.py
rg -n "def (_demucs_env|stage_inbox_candidates|ingest_drops|download_url|transcribe_source|_produce_transcript|_valid_transcript_json|isolate_vocals)" src/fanops/vocals.py src/fanops/ingest.py src/fanops/transcribe_engine.py src/fanops/artifacts.py
rg -n "except " src/fanops/vocals.py src/fanops/ingest.py src/fanops/discover.py src/fanops/transcribe_engine.py src/fanops/ingest_shard.py src/fanops/media_probe.py src/fanops/artifacts.py
rg -n "FANOPS_ISOLATE_VOCALS|FANOPS_ASR_MODEL|FANOPS_ASR_LANGUAGE|FANOPS_SOURCE_SHARD_MIN|FANOPS_QUEUE_GATE|FANOPS_WHISPER_MODEL" src/fanops/config.py
git show HEAD:src/fanops/doctor.py | rg -n "ffmpeg|ffprobe|faster-whisper|yt-dlp|demucs|isolate"
git diff HEAD -- src/fanops/doctor.py | rg -n "ffmpeg|ffprobe|faster-whisper|yt-dlp|demucs"
rg -n "alias|_valid_transcript_json|_disk_stages|infer_resume_stage as" src/fanops/CLAUDE.md
ffmpeg 9.0.2 stream-copy probe: 6s lavfi source, shard_file shape `-ss 3 -i -to 3 -c copy` (whole-file when the GOP has no interior keyframe; ~3.07s when `-g 30`)
.venv/bin/python repro: legacy segments + signals sidecar v3 → infer None, state catalogued, transcript None; talk_seg + same sidecar → infer signalled
```

## Wave 1 evidence (dirty tree)

Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e` on `audit/production-audit-2026-09`. Lane diff vs HEAD at the start of this pass was `src/fanops/artifacts.py` only (the C2-F02 adoptable-sidecar gate). `ingest.py`, `vocals.py`, and `transcribe.py` matched HEAD. `config.py` was not edited. No `stage_inbox_candidates as` / `_inbox_media as` aliases. Nothing deleted. pytest was not run.

C2-F01 stays closed: `vocals._demucs_env` returns `certifi_ssl_env` and has no `except`. C2-F02 stays closed: `_valid_transcript_json` requires `_cache_is_quality_complete` plus `_finalize_segments`, and `_disk_stages` returns no later stage without that. C2-F03 is closed: `config.isolate_vocals` matches the raise. The log line in `vocals.isolate_vocals` still contains the token `fail-open`; the function raises, and `tests/test_vocals.py` pins that token. Not a second finding.

C2-F04 was wrong on this tree. `.venv` is Python 3.12.8 and `Path.rglob("*")` matches `.pull` and `.ingested`. Native `ingest_drops` (the `advance` / `cmd_ingest` scan of `01_inbox`) minted three sources, all `source_origin=drop`: the manual drop, `01_inbox/.pull/stranded.mp4`, and `01_inbox/.pull/.ingested/done.mp4`, and moved all three into `01_inbox/.ingested`. `source_origin` is write-once, so a later pull only logs `origin_path_conflict`. Fix: skip a child `.pull` the same way `.ingested` is skipped. When the scan box is the pull stage, that skip is a nested `box/.pull` and the pull's own files still match. After the fix the same repro added 1 (`drop`, 6 bytes); stranded and the disposed archive stayed put; a pull-stage scan then added 1 (`url`, 8 bytes) and left `done.mp4` in `.pull/.ingested`.

Missing-transcript / demucs posture: no whisper JSON sets `SourceState.error` (`whisper produced no JSON`) and `is_transient_error` is false, so the row stays in error; a missing demucs raises `ToolchainMissingError`, `_produce_transcript` sets `SourceState.error` (`vocals isolation failed`) and does not transcribe the mix, and that reason is transient so the next advance retries isolation. Working-tree doctor (`doctor.py` 412–420; no toolchain hunk in `git diff HEAD`) checks ffmpeg, ffprobe, faster-whisper, and yt-dlp, not demucs.

### Commands run

```
git rev-parse HEAD
git status --short --branch
git log --oneline --decorate -8
git diff --stat HEAD -- src/fanops/ingest.py src/fanops/ingest_shard.py src/fanops/discover.py src/fanops/transcribe.py src/fanops/transcribe_engine.py src/fanops/vocals.py src/fanops/media_probe.py src/fanops/frames.py src/fanops/audio_energy.py src/fanops/bands.py src/fanops/intro_match.py src/fanops/speech_trust.py src/fanops/adjust.py src/fanops/artifacts.py src/fanops/config.py
git diff HEAD -- src/fanops/artifacts.py
rg -n "def (_demucs_env|isolate_vocals|stage_inbox_candidates|ingest_drops|download_url|transcribe_source|_produce_transcript|_valid_transcript_json|_cache_is_quality_complete|infer_resume_stage|_adopt_cached_transcript)" src/fanops
rg -n "except " src/fanops/vocals.py src/fanops/ingest.py src/fanops/discover.py src/fanops/transcribe_engine.py src/fanops/ingest_shard.py src/fanops/media_probe.py src/fanops/artifacts.py src/fanops/transcribe.py
rg -n "stage_inbox_candidates as|_inbox_media as|from fanops.ingest import" --glob '*.py'
git diff HEAD -- src/fanops/doctor.py | rg -n "ffmpeg|ffprobe|faster-whisper|yt-dlp|demucs|isolate"
.venv/bin/python --version   # Python 3.12.8
.venv/bin/python rglob probe: Path.rglob("*") lists .pull, .pull/.ingested, and .ingested
.venv/bin/python BEFORE: ingest_drops on inbox with manual.mp4 + .pull/stranded.mp4 + .pull/.ingested/done.mp4 → added 3, all origin drop, all three moved to 01_inbox/.ingested
.venv/bin/python AFTER: native added 1 origin drop bytes 6; stranded and done.mp4 stayed; pull-stage ingest added 1 origin url bytes 8; done.mp4 stayed; is_transient_error("vocals isolation failed: demucs unavailable (FileNotFoundError: ...)") is True; is_transient_error("whisper produced no JSON (rc=1): boom") is False
```

pytest was not run.
