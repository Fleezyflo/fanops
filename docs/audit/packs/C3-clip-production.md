# Pack: C3 — clip production & framing

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. `git diff origin/main -- src` is empty. Entry symbols re-read 2026-10-18: `clip_ffmpeg._finish_publish` refuses an empty rc-0 temp; `ffmpeg_supercut_cmd` emits `-fps_mode cfr`. `clip.py` was not edited. Later sections that name `d2cded31` or a dirty tree are the earlier close record.


Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. Product behaviour below is from `git show HEAD:path` plus the one supercut edit in this pass. `clip.py` was not edited (picking).

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| C3-F01 | low | cost | closed — one real 9fps ffmpeg on the CT_MULTI path | `detect_window`, `_compute_track`, and `motion_saliency` call `extract_frames_grid` at `_FRAMING_GRID_FPS` | `tests/test_framing_grid_fusion.py` `test_ct_multi_resolve_one_real_ffmpeg` |
| C3-F02 | medium | wrong | closed in this pass | Supercut concat emits `-fps_mode cfr` after the maps, same slot as `ffmpeg_segments_cmd` | `clip_ffmpeg.ffmpeg_supercut_cmd` |
| C3-F03 | high | wrong | closed in this pass | rc 0 with an empty or missing temp is not a publish. `render_reframed` and `render_supercut_reframed` return rc 1 and leave a pre-existing dst untouched, so `render_moment` does not stamp the new fingerprint on the old bytes | `clip_ffmpeg._finish_publish` |

C3-F01 is closed. `test_ct_multi_resolve_one_real_ffmpeg` drives `clip._resolve_framing` through CT_MULTI on a generated mp4 and counts one real `subprocess.run` at `framing._FRAMING_GRID_FPS` (9.0). The detector is stubbed as two faces. `clip.py` was not edited.

## Full checklist 2026-10-17

Launch path can produce a clip. C3-F01, C3-F02, and C3-F03 are closed in this tree. No clip.py edit.

### Entry symbols

| Symbol | Role |
|--------|------|
| `produce.run_all` | Lock-free producer `pipeline.advance` calls before the ledger transaction |
| `produce._produce_one` | Private `Ledger.load`. Renders moments already `MomentState.decided`. Does not save |
| `pipeline._stage_render_and_caption` | In-lock render of `decided` moments via `render_aspects_for` |
| `clip.render_aspects_for` / `clip.render_moment` | One mp4 per aspect. Supercut when `moment.segments` is set; else `fit_window` then reframe |
| `clip._resolve_framing` | `cfg.smart_framing` off → `(None, None, None)`. On → `framing._resolve(...).as_tuple()` |
| `framing._resolve` | `detect_window`, then speaker track, subject focus, or motion saliency |
| `framing._compute_track` | `extract_frames_grid` at `_FRAMING_GRID_FPS`, shared with `detect_window` and `motion_saliency`, then `_track_observe`, `_assemble_track` |
| `keyframes.extract_frames_grid` | Cache hit or one `_run_grid_extract` |
| `clip_ffmpeg.render_reframed` | Stack, else segment concat, else single-pass crop. Atomic publish |
| `clip_ffmpeg.render_supercut_reframed` | Supercut atomic publish. Fail-open caller is `render_moment` |
| `stitch_render.prewarm_approved_stitches` / `render_approved_stitches` | Impact-cut and intro-tease. Both flags default off |
| `compose.prepend_intro` / `compose.compose_clip` | Intro composite and optional template. Not the default moment render |
| `overlay.burn_hook_only` | Per-account hook burn from crosspost, after the clip exists |
| `reframe_apply.apply_run` | Operator `fanops reframe --apply`. Not the produce path |
| `reframe_apply.assert_render_allowed` | `render_moment` raises `MigrationLockHeld` while `reframe.lock` is held |

`pick_visual_start` and `snap_window` are not on the default render. `render_moment` sets the window with `fit_window` only (`pick is the cut`). `snap_window` is used by `reframe._seed_scratch_vstart`. `FANOPS_VISUAL_START` does not move the produced cut.

### Data writes

Producers do not `Ledger.save`. The reduce transaction owns ledger state. Artifacts that survive a discarded producer ledger:

- Mp4: `render_reframed` and `render_supercut_reframed` write `<dst>.part.mp4` and `os.replace` onto `dst` only when `returncode == 0` and the temp exists and is non-empty. Failure leaves `dst` untouched. If that publish does not happen, the returned rc is 1 even when ffmpeg exited 0 (C3-F03).
- `{cid}.ass` from `overlay.write_ass` when a hook is burned.
- `{cid}.render.json` `{"fp": ...}` after a successful `render_moment`, best-effort `write_text`. A failed render does not stamp a skip. An OSError on the stamp only costs a re-render.
- `artifacts.stamp_stage` for `clip`, `framing`, and `keyframes` (OSError ignored).
- `{source_id}.detect.json`: temp file plus `os.replace` inside `stage_lock(stage="framing")`.
- Track and saliency sidecars: `path.write_text` (not the detect-sidecar replace). A torn read recomputes (`_load_cache` returns `{}`).
- Grid jpgs under `agent_io/keyframes/<source_id>/<hash>/`, plus `.complete`. A partial extract without `.complete` is not a hit.
- Produce ASR-retry marker: `marker.write_text("1")`.
- Intro tease: `{cid}.render.json` only when `prepend_intro` returns true. A genuine compose miss writes `{cid}.introfail.json`.
- Reframe apply and overlay reburn replace media only after their own validate/decode gates. They are operator migrations.

### External calls (ffmpeg)

| Caller | Process |
|--------|---------|
| `keyframes._run_grid_extract` | One `ffmpeg` `-vf fps=,scale=` for the window. Timeout 60s |
| `keyframes.extract_keyframes` | One `ffmpeg` per still. Hook eyes, not the clip mp4 |
| `clip_ffmpeg.render_reframed` / `render_supercut_reframed` | `ffmpeg` encode. Timeout `_FFMPEG_TIMEOUT` 600s |
| `overlay.ffmpeg_has_textfilter` | `ffmpeg -filters` once, cached. Absence returns false |
| `overlay.burn_hook_only` | `ffmpeg` burn, atomic `.part` |
| `visual_start._probe_frame_strength` | Two `ffmpeg` probes per candidate. Not called by `render_moment` |
| `reframe_apply.decodes` / `ffprobe_json` | `ffmpeg` decode check and `ffprobe` |
| `clip._probe_duration` | `ingest.probe_dimensions` (ffprobe). Stitch duration gate |
| `compose._moviepy_render` / `_moviepy_prepend_render` | MoviePy, which shells ffmpeg. Default off for a normal moment |

`reframe.attribution` shells `git`. Not the produce path.

### Env

These modules do not call `os.getenv`. Flags are `Config` properties.

| Env | Default | Effect on this path |
|-----|---------|---------------------|
| `FANOPS_SMART_FRAMING` | on | Off: centered crop, no grid. On: `framing._framing_runtime_or_raise`. Missing or broken cv2 raises `ToolchainMissingError` before a centered file. The `config.smart_framing` docstring still says fail-open; the code does not. `config.py` is publish-owned and was not edited |
| `FANOPS_VISUAL_START` | on | Does not change `render_moment`. The cut is the pick |
| `FANOPS_AWARE_REFRAME` | off | Top bias only when `moment.framing` is unset. `"top"` / `"center"` on the moment win |
| `FANOPS_BURN_SUBS` | on | Ignored at render. A hook burns when present; transcript captions do not |
| `FANOPS_SUBTITLE_FONT` | Arial Unicode MS | ASS font |
| `FANOPS_IMPACT_CUT` | off | No impact-cut plans or stitch renders |
| `FANOPS_INTRO_TEASE` | off | No intro matcher and no prepend |
| `FANOPS_ROOT` | via `Config` | `extract_frames_grid` with `source_id` and `cfg=None` builds `Config()` and can write the grid under the live root. `detect_window`, `_compute_track`, and `motion_saliency` pass `cfg` |

### Fail-closed vs fail-open on missing frames

| Condition | Posture |
|-----------|---------|
| cv2 / YuNet missing while smart framing is on | Fail closed. `ToolchainMissingError` before any crop. `render_moment` does not catch it. `pipeline._stage_render_and_caption` quarantines the moment to `MomentState.error` |
| ffmpeg absent, grid timeout, encode rc ≠ 0, or rc 0 with an empty glob | Fail open at the grid. `_run_grid_extract` returns `[]`. With a trace: `FFMPEG_UNAVAILABLE`, `FRAME_EXTRACTION_FAILED`, or `NO_FRAMES`. `_resolve` always allocates a `FramingTrace`. `clip._resolve_framing` keeps only the tuple, so the clip does not store that event |
| Empty grid | `detect_window` returns `None` and does not cache it. Classification treats absent stats as no-people. The tuple can still be a centered crop |
| `out_dir.mkdir` OSError inside `_run_grid_extract` | Propagates. `speaker_track` turns it into `None`. `subject_focus` and `motion_saliency` can let it out of `_resolve` (`capture_failures` is false on the produce path) |
| Render ffmpeg absent, timeout, rc ≠ 0, or empty output | Fail closed for the ship. `ClipState.error`, moment stays `decided`, no fingerprint stamp. A later pass retries. C3-F03: a re-render whose new temp is empty or missing used to return rc 0 and stamp the new fingerprint on the prior mp4. `_finish_publish` returns rc 1 and leaves that file untouched |
| Supercut ffmpeg failure | Logged `supercut_fail_open`, then the envelope `fit_window` render |
| Stack or segment graph rejected inside `render_reframed` | Fail open to the next rung, then the single-pass crop. The published file is whatever rung succeeded. `render_moment` stamps the fingerprint of the inputs it asked for. A fallback file can therefore match on the next pass. Changing that stamp is `clip.py`. Not edited |
| Compose / prepend failure | Fail open to a byte copy of the base, return false. Intro commit stamps a fingerprint only on a real composite |

### Launch-critical path (produce a clip)

1. `pipeline.advance` catalogs the drop, then `produce.run_all`.
2. `_produce_one` renders only moments that are already `decided`. A moment promoted `picked` → `decided` in this same pass is not rendered here. The private ledger is discarded. An mp4 and `{cid}.render.json` from an earlier decided moment stay on disk.
3. Inside `Ledger.transaction`: `_stage_moment_hooks`, then `_stage_render_and_caption` → `render_aspects_for` → `render_moment`.
4. A matching fingerprint skips ffmpeg. Otherwise ffmpeg runs in that transaction, bounded by 600s. `clip_ffmpeg` documents that bound because `render_moment` can hold the ledger flock.
5. Success: `ClipState.rendered`, moment `clipped`, fingerprint written.
6. `led.is_suppressed(moment)` returns no clips.
7. Impact-cut and intro-tease stay off unless their env flags are on. They are not required to produce the bare clip.

Existing supercut mp4s whose fingerprint already matches are not re-encoded by C3-F02. The flag is not a fingerprint field, same as the segment-concat fix in `a9f48f19`. The next render of that window uses CFR.

### clip.py

No edit. A change to the fallback fingerprint stamp, or to when `pick_visual_start` runs, would be in `clip.py` (picking).

`src/fanops/CLAUDE.md` has no alias for `ffmpeg_supercut_cmd`. Nothing was deleted.

## Evidence commands run

```
git rev-parse HEAD
git grep -n 'extract_frames_grid|_DETECT_FPS|_ASD_FPS' HEAD -- src/fanops/framing.py src/fanops/keyframes.py
git show HEAD:src/fanops/keyframes.py   # _window_cache_key includes fps
git show HEAD:src/fanops/clip_ffmpeg.py # ffmpeg_supercut_cmd before this pass had no -fps_mode
git show a9f48f19 --stat                # segments concat CFR; supercut not in that commit
git grep -n 'os.getenv\|os.environ' HEAD -- src/fanops/clip.py src/fanops/clip_ffmpeg.py src/fanops/framing.py src/fanops/keyframes.py src/fanops/produce.py src/fanops/compose.py src/fanops/overlay.py src/fanops/reframe_apply.py
git grep -n 'def advance\|run_all(\|_stage_render_and_caption' HEAD -- src/fanops/pipeline.py
rg -n 'ffmpeg_supercut_cmd|as ffmpeg_' src/fanops/CLAUDE.md
PYTHONPATH=src python3 -c 'from fanops.clip_ffmpeg import ffmpeg_supercut_cmd; c=ffmpeg_supercut_cmd("s.mp4","o.mp4",[(10,12),(30,33)],"9:16",src_w=1920,src_h=1080); assert c[c.index("-fps_mode")+1]=="cfr"'
```

pytest was not run.

## Wave 1 evidence (dirty tree)

Subject remains `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. This pass edited `src/fanops/clip_ffmpeg.py` and `tests/test_render_atomicity.py`. `clip.py` was not edited. Nothing was deleted. No `as` alias for `ffmpeg_supercut_cmd`, `render_supercut_reframed`, or `_finish_publish` under `src/fanops`.

C3-F01 is closed. `test_ct_multi_resolve_one_real_ffmpeg` counts one real ffmpeg at `framing._FRAMING_GRID_FPS` (9.0) on the CT_MULTI path. `keyframes._window_cache_key` was not changed.

C3-F02 stays closed. `ffmpeg_supercut_cmd` still places `-fps_mode cfr` after both `-map` options.

C3-F03 was a real publish hole. `os.replace` runs only when the temp exists and is non-empty, but the function still returned ffmpeg's rc 0 when the temp was empty or missing. On a re-render the previous mp4 is still at `dst` with size > 0, so `render_moment` treated that old file as this render and wrote `{cid}.render.json`. `_finish_publish` returns rc 1 in that case and does not replace. A direct call (not pytest) kept `PRIOR-GOOD`, set `ClipState.error`, left the moment `decided`, and wrote no fingerprint. A nonempty rc 0 still publishes. The ladder's early stack and segment returns still publish only when that rung's temp is non-empty; they were not the hole.

pytest was not run.

```
git rev-parse HEAD
git diff --stat -- src/fanops/clip_ffmpeg.py tests/test_render_atomicity.py
rg -n "extract_frames_grid\(|fps=_DETECT_FPS|fps=_ASD_FPS" src/fanops/framing.py
rg -n "ffmpeg_supercut_cmd as |render_supercut_reframed as |_finish_publish as " src/fanops
PYTHONPATH=src python3  # inline, pre-fusion: empty and missing temp return rc 1 and keep prior bytes; nonempty rc 0 publishes; supercut -fps_mode cfr sits after -map. That pass still saw _DETECT_FPS 4.0. _DETECT_FPS is gone; the grid rate is _FRAMING_GRID_FPS.
PYTHONPATH=src python3  # inline render_moment re-render: ClipState.error, moment decided, no render.json, prior bytes kept; error_reason starts "ffmpeg rc=1"
```
