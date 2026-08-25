# Trial Reels — hook factory + cover OCR QA

Experimental lane for **Reels/TikTok hook burn-in** and **cover-frame OCR QA**. Lives outside `src/fanops/` so the main app is untouched. Uses **ffmpeg + ASS + tesseract only** (no Pillow, no PNG text overlays, no vendored bidi).

## Hook factory contract

One source clip → **up to 20 vertical cuts** (5 hook policies × 4 ffmpeg stacks).

**Claim-lock:** `lib/desk.py` extracts contiguous attested **sentences and real lines** only — no n-gram windows, no cross-sentence joins, no permutations, no invented words. عزبتني keeps ز.

**Honest ceiling:** the transcript may attest fewer than 20 distinct on-screen texts (live English prose → 4 sentences; live Arabic sung line → 1 line). The runner still ships 20 **edit treatments** (hook timing × stack) by cycling attested claims across slots; `unique_texts` in `desk.json` records the true distinct-text count — never padded to hit a quota.

- Fail closed only on **empty**, **credit-only**, or **no valid claims** — never slice sentences to invent hooks.
- Pass bar: contiguous claim-lock + cover OCR on attested text; **not** “20 unique texts” when the source cannot support them.

```bash
PYTHONPATH=trial-reels python -m lib.runner --file clip.mp4 --transcript clip.transcript.json --out-dir out
```

Desk output includes `unique_texts`, `target_variants`, and `plan_variants()` for the hook×stack grid before ffmpeg render.

## Why cover QA exists

Production ASS stamps (Noto Naskh, 72pt, Alignment 8, MarginV 320) are correct, but cover QA was failing because:

1. **Whole-frame OCR** on purple neon studio lighting returns garbage — the hook lives in the top band only.
2. **Frame 0** is often black or pre-roll — extract the cover at **0.4s**, not `t=0`.

## Layout

| Path | Role |
|------|------|
| `lib/desk.py` | Sentence/line claim extraction; `expand_variant_slots()` + `plan_variants()` |
| `lib/runner.py` | One clip → 20 vertical cuts (claims × hook policies × stacks) |
| `lib/captions.py` | `write_ass(events, font)` — ASS builder for RTL hooks (top safe zone) |
| `lib/cover_qa.py` | Tight ASS stamp band (MarginV 320) → language-aware preprocess → tesseract → match attested card words |
| `tests/` | Hermetic unit tests + ffmpeg/tesseract integration on a generated purple fixture |

## ASS captions

```python
from lib.captions import write_ass, write_ass_file

ass = write_ass(
    [{"start": 0.0, "end": 2.5, "text": "عزبتني"}],
    "Noto Naskh Arabic",
)
write_ass_file(ass, "hook.ass")
# ffmpeg burn-in: -vf "subtitles='hook.ass'"
```

Style contract: **Noto Naskh Arabic**, **72pt**, **Alignment 8** (top-center), **MarginV 320**, white text + heavy black outline for purple neon and high-contrast footage.

## Cover OCR QA

```bash
# From repo root (PYTHONPATH picks up trial-reels/lib)
PYTHONPATH=trial-reels python -m lib.cover_qa /path/to/clip.mp4 --words عزبتني كفاية

# Or invoke the package main
PYTHONPATH=trial-reels python trial-reels /path/to/cover.png --json
```

Pipeline:

1. Extract still at **t=0.4s** (`--at` to override)
2. Crop tight **ASS stamp band** around Alignment 8 / MarginV 320 (not the whole top of frame)
3. Preprocess: grayscale + contrast and white-stroke isolation (no invert/negate)
4. Tesseract **`ara`** or **`eng`** per clip language (PSM 11 sparse for Arabic)
5. Match attested on-screen card phrases (defaults include `عزبتني`, `كفاية`, `لك كفاية عزبتني` from `clip_5a92132dc6de`)

### Attested words

Pass explicit hook phrases from the clip card:

```bash
PYTHONPATH=trial-reels python -m lib.cover_qa clip.mp4 --words "لك كفاية عزبتني"
```

English cards are matched case-insensitively when included in `--words`.

## Tests

Requires **ffmpeg**; OCR tests also need **tesseract** with `ara` + `eng` language packs.

```bash
PYTHONPATH=trial-reels python -m pytest trial-reels/tests -q
```

The purple fixture is generated in-test via ffmpeg `drawtext` (no Pillow).

## Cover extract timing recommendation

Use **`ffmpeg -ss 0.4`** (implemented as `COVER_EXTRACT_S = 0.4` in `cover_qa.py`) instead of the first frame. Real covers from `clip_5a92132dc6de` show the white Arabic hook on a magenta wall — frame 0 misses or OCRs the wrong band when fades/black lead-in are present.
