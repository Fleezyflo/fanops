# Trial Reels — transcription + ingest lane

Isolated experiment for dual-ear Whisper transcription and flexible ingest.
Does **not** modify FanOps app code under `src/fanops/`.

## Dual-ear merge

| Ear | Model |
|-----|-------|
| Primary | `large-v3` |
| Secondary | `large-v3-turbo` |

`transcribe(wav, work, fresh=False)` runs both ears and merges segments.

**No forced `--language`.** Forcing Arabic made large-v3 hallucinate subtitle credits
on English VO while turbo heard English correctly.

Merge goes beyond low-confidence takeover:

1. **Dual-agree** — identical normalized text → keep it, mark `dual_agreed=true`.
2. **Primary low-conf** — prefer secondary when it is more confident.
3. **Confident disagreement** — script/language mismatch, known hallucination markers
   (e.g. `ترجمة نانسي قنقر`), or Arabic ear disagreement → prefer secondary (turbo).

### API

```python
from pathlib import Path
from lib.ears import transcribe, merge_ears

result = transcribe(Path("audio.wav"), Path("work/"), fresh=False)
# {
#   "text": "...",
#   "segments": [{"start", "end", "text", "avg_logprob", "no_speech_prob", "low_conf", "dual_agreed"}],
#   "language": "en",
#   "ear": "large-v3+large-v3-turbo",
# }
```

`merge_ears(primary, secondary)` is a pure function for unit tests with fixture JSON.

## Toolchain

| Tool | Path |
|------|------|
| Whisper CLI | `.venv/bin/whisper` only |
| ffmpeg | `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` |
| Device | MPS if available, else CPU |

- **No model downloads** in this lane — models must already be cached.
- **No** silent `ggml-small` fallback.
- **No** HeyGen integration.
- **Cloud ASR** (`TRIAL_REELS_CLOUD_ASR`) stays closed unless explicitly set.
- **Dialect model** — optional local path via `TRIAL_REELS_DIALECT_MODEL` (never fetched).

`fresh=True` wipes `work/.whisper_cache` before transcribing.

## Ingest shapes

`collect_sources(args)` supports four CLI shapes:

| Shape | Args |
|-------|------|
| Single file | `--file clip.mp4` |
| Folder | `--folder ./batch/` |
| Drain inbox | no file/folder/from-fanops → all media in `in/` |
| FanOps clip | `--from-fanops <id>` under `/Users/molhamhomsi/FanOps/MohFlow-FanOps/03_clips` |

## Tests

Fixture JSON only — no Whisper weights required:

```bash
python -m pytest trial-reels/tests -q
```

Cases:

- Credit-primary Arabic hallucination + English secondary → keeps English
- Arabic dual-agree `عزبتني` → never `عذبتيني`
- Four ingest source shapes
