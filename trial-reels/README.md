# trial-reels

Isolated experiment lane for ffmpeg edit stacks and hook policies. **Does not touch FanOps app code.**

## Layout

| Path | Role |
|------|------|
| `lib/stacks.py` | FFmpeg `filter_complex` builders (punch_cuts, open_loop, fake_out, end_loop) |
| `lib/hooks.py` | Five hook policies with distinct in/out windows and lyric stamping |
| `recipes.json` | Recipe manifest (`target_seconds`, hooks, stacks, `rehooks_s`) |
| `tests/` | Graph + policy contract tests |

## Constraints

- **ffmpeg-full only** — `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` (falls back to `ffmpeg` on PATH when absent, e.g. CI).
- **ASS subtitles** via `subtitles` filter — no drawtext, Pillow, or PNG overlays.
- **loudnorm** `I=-14` inside `filter_complex` on `[a]` — never `-af` alongside `-filter_complex`.
- **10-bit safe** — graphs normalise with `format=yuv420p` before pixel ops.
- No model downloads, HeyGen, Higgsfield, or spoken new lines.

## Stacks

| Stack | Behaviour |
|-------|-----------|
| `punch_cuts` | 3 jump trims + concat; gated at ≥ 3.6 s source |
| `open_loop` | Shorter unresolved cut (tail trimmed); no rehooks; drop last lyric |
| `fake_out` | 0.15 s black flash via `color=` (never `geq`) |
| `end_loop` | Repeat last 1 s after body |

Rehooks fire at 3 s and 8 s for all stacks **except** `open_loop`.

## Hook policies

| Policy | Hook window | Lyric stamp |
|--------|-------------|-------------|
| `result_first` | 0–18 % of cut | first half |
| `mid_action` | 22–58 % | middle band |
| `direct_you` | 4–28 % | lines containing "you" |
| `bold_claim` | 0–14 % | first line only |
| `cold_proof` | 38–72 % | closing band |

Cut starts at `cite_start`; length is `min(8, remaining)`.

## Usage

```python
from trial_reels.lib.stacks import build_stack_graph, ffmpeg_cmd
from trial_reels.lib.hooks import hook_window, stamp_lyric_events, LyricEvent

graph = build_stack_graph("punch_cuts", duration_s=12.0, has_audio=True)
cmd = ffmpeg_cmd("fake_out", input_path="in.mp4", output_path="out.mp4", duration_s=10.0)

win = hook_window("result_first", cite_start_s=4.0, total_duration_s=30.0)
stamped = stamp_lyric_events(
    "direct_you",
    [LyricEvent(5.0, 6.0, "did you hear that")],
    cite_start_s=4.0,
    total_duration_s=30.0,
)
```

## Tests

```bash
pytest trial-reels/tests/
```

Tests lock filter graphs (loudnorm in `[a]`, no `geq` in fake_out, no `-af` with `filter_complex`) and verify the five hook policies differ.
