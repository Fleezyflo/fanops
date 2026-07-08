# C3: Clip Production & Framing

## Files covered (all 8 read in full)

1. `src/fanops/clip.py` (762 lines) — read
2. `src/fanops/framing.py` (530 lines) — read
3. `src/fanops/keyframes.py` (145 lines) — read
4. `src/fanops/stitch_render.py` (357 lines) — read
5. `src/fanops/overlay.py` (339 lines) — read
6. `src/fanops/impact_cut.py` (87 lines) — read
7. `src/fanops/compose.py` (298 lines) — read
8. `src/fanops/produce.py` (132 lines) — read

Cross-checked against `.reports/call_graph.json` (every function below cites real caller lists pulled from the graph) and `.reports/structural_index.json` for the full per-file symbol inventory — no function/class/constant omitted.

## Pipeline/data-flow overview

### A. Dynamic-reframe decision chain (framing.py → clip.py)

```
clip._resolve_framing(cfg, src, cs, ce)     [gated on cfg.smart_framing; OFF -> (None,None,None) byte-identical]
  │
  ├─► framing.detect_window(cfg, src, start=cs, end=ce)
  │      ONE grid pass (keyframes.extract_frames_grid @ _DETECT_FPS=4.0fps, width=_KF_WIDTH=960)
  │      -> YuNet face boxes per frame, cached <agent_io>/framing/<source_id>.detect.json (_DETECT_V=1)
  │      per-(framing, source_id) stage_lock guards concurrent producers
  │      fail-open -> None (no [framing] extra / no detector / no frames / any error)
  │
  ├─► framing.classify_window(cfg, src, start=cs, end=ce, stats=detect_stats)
  │      PURE routing (no ffmpeg/cv2) over face-count + transcript-speech + vocals_isolated meta:
  │        faces==0                          -> CT_NOPEOPLE  ("no-people")
  │        faces>=2 AND speech               -> CT_MULTI     ("multi-speaker-talk")
  │        faces==1 AND speech               -> CT_SINGLE    ("single-speaker-talk")
  │        face, no speech, vocals_isolated  -> CT_MUSIC     ("music")
  │        face, no speech, no vocals        -> CT_SILENT    ("silent")
  │
  ├─ if CT_MULTI:
  │     framing.speaker_track(cfg, src, start=cs, end=ce, src_w, src_h)
  │       -> OWN grid pass @ _ASD_FPS=9.0fps (finer than the 4fps classify grid — mouth-motion needs it)
  │       -> _track_observe (per-frame L/R mouth-ROI motion) -> _assemble_track (hysteresis commit,
  │          _ASD_HOLD_S=0.35s dwell) -> _merge_brief_segments (absorb shots < _ASD_MIN_SEG_S=1.5s)
  │       -> list of (t0,t1,fx,fy,fh,ey) segments, cached <source_id>.track.json (_SIDECAR_V=5)
  │       track found  -> return (None, track, "multi-speaker-talk")   [routes to SEGMENT-CONCAT render]
  │       track empty  -> falls through, reclassified as CT_SINGLE (not a real 2-shot)
  │
  ├─ if CT_SINGLE / CT_MUSIC / CT_SILENT:
  │     framing.subject_focus(cfg, src, start=cs, end=ce)
  │       -> reduces the SAME detect_window grid (no re-probe) via _median_face:
  │          dominant (largest-fh) face per frame -> median (fx,fy,fh,ey) + confidence
  │       -> None if confidence < _MIN_CONF=0.34 (need a face in >=2 of 5 sampled frames)
  │       found -> return (focus_4tuple, None, content_type)      [routes to SINGLE-PASS static-lock render]
  │
  ├─ if CT_MUSIC / CT_SILENT / CT_NOPEOPLE (still unresolved):
  │     framing.motion_saliency(cfg, src, start=cs, end=ce)
  │       -> OWN grid pass @ _ASD_FPS -> inter-frame pixel-diff centroid (_saliency_centroid)
  │       -> (fx,fy) 2-tuple (NO face height -> NEVER zooms) or None, cached <source_id>.saliency.json
  │       found -> return (sal_2tuple, None, None)                 [routes to SINGLE-PASS pan-only render]
  │
  └─ else: return (None, None, None)                               [routes to SINGLE-PASS centered crop — today's default]
```

### B. Render fail-open ladder (clip.render_reframed)

```
clip.render_moment(...)
  cs,ce = fit_window -> snap_window -> [pick_visual_start if cfg.visual_start]
  focus, track, content_type = _resolve_framing(cfg, src, cs, ce)
  extra_vf, hook_burn_failed = _subtitles_vf(...)         # hook / transcript burn, itself fail-open
  fp = _render_fingerprint(...)                            # content-address; idempotent skip if fp matches on-disk sidecar
  │
  └─► render_reframed(src_path, dst, cs, ce, aspect, focus=focus, track=track, content_type=content_type, ...)
        │
        ├─ RUNG 1: if track and len(track) > 1:
        │     ffmpeg_segments_cmd -> _segments_filter_complex (per-segment crop chain -> concat -> optional subs)
        │     subprocess.run(seg_cmd, timeout=600s)
        │     if rc==0 AND dst exists AND size>0: RETURN (segment-concat render committed)
        │     else: FALL THROUGH to Rung 2 (a working ffmpeg REJECTED the segment graph — fail-open, no raise)
        │
        └─ RUNG 2 (always reached if Rung 1 absent or fell through):
              ffmpeg_clip_cmd -> reframe_filter (single -vf: _focus_crop / _track_crop-unreachable-here /
                                 centered crop, per focus/track/top_bias/content_type)
              subprocess.run(cmd, timeout=600s)  -> RETURNED AS-IS to render_moment's caller
```

`render_moment` then classifies the subprocess result:
`FileNotFoundError/OSError` (ffmpeg absent) → `ClipState.error`, moment stays `decided` (retryable) — never crashes the pipeline transaction.
`subprocess.TimeoutExpired` → `ClipState.error`, same retry contract.
`rc != 0` or `dst` missing/zero-byte → `ClipState.error`.
Stitch path (`is_stitch`) additionally duration-checks the output (`_probe_duration` vs `impact_cut.DURATION_TOLERANCE`) and errors if it drifts — even a `rc==0`, nonzero-size stitch can fail here.
Success → `Clip` row written, moment set to `MomentState.clipped`, fingerprint sidecar stamped (best-effort, `except OSError: pass`).

**Net effect: every failure mode (missing binary, hang, bad segment graph, corrupt output, wrong duration) degrades to either the next rung or a recorded `ClipState.error` — never an unhandled exception, never a silently wrong-length clip shipped as `rendered`.**

### C. Full clip-production pipeline wiring (who calls what, in order)

```
pipeline.advance()                                   [outside this cluster]
   │
   ├─► produce.run_all(cfg, aspects, log)             THE LOCK-FREE PRODUCER ENTRY POINT
   │      per source (optionally ThreadPoolExecutor-parallel via cfg.concurrent_sources):
   │        produce._produce_one(cfg, source_id, aspects, log)
   │          catalogued  -> transcribe.transcribe_source        [outside cluster]
   │          transcribed -> signals.detect_signals               [outside cluster]
   │          decided moments -> clip.render_aspects_for(led, cfg, moment_id, aspects=aspects)
   │                                for asp in aspects: clip.render_moment(...)
   │                                  -> clip._resolve_framing -> framing.detect_window/classify_window/
   │                                     speaker_track/subject_focus/motion_saliency
   │                                  -> clip._subtitles_vf -> overlay.build_ass / ffmpeg_has_textfilter /
   │                                     hook_legibility_warnings / subtitles_vf / write_ass
   │                                  -> clip.render_reframed -> clip.ffmpeg_segments_cmd / ffmpeg_clip_cmd
   │      after the per-source loop, if impact_cut or intro_tease enabled:
   │        stitch_render.prewarm_approved_stitches(led, cfg, log, strategies)
   │          impact_cut plans  -> clip.render_moment(..., cut_window=(cs,ce), born_state=stitch_draft)
   │          intro_tease plans -> compose.prepend_intro(base, intro_source, out, tease_text, intro_seconds)
   │                                 -> compose._moviepy_prepend_render (MoviePy 2.x, lazy import)
   │
   └─► [in-lock] pipeline._stage_render_and_caption / _stage_structural_hooks (reducer)  [outside cluster]
          adopts warm on-disk artifacts (fingerprint match -> skip re-render):
          clip.render_aspects_for(...) again — this time nearly always a fingerprint hit, no ffmpeg
          stitch_render.mine_suggestions(led, cfg, log, strategies)
             _impact_cut_candidates -> impact_cut.make_stitch_plan(clip, moment, src, base_fp)
                                          -> impact_cut.plan_impact_cut -> impact_cut._impact_peak
             _intro_tease_candidates -> reads Moment.intro_matches (from intro_match.py, outside cluster)
             ranks by rank_score, dedupes, caps at MAX_SUGGESTIONS_PER_PASS=5, re-routes moment hook_strategy
          stitch_render.render_approved_stitches(led, cfg, strategies)   IN-LOCK COMMIT
             _precheck (base clip gone? base fingerprint drifted -> auto-dismiss?)
             impact_cut  -> _commit_impact -> clip.render_moment(cut_window=...)  [usually a warm adopt]
             intro_tease -> _commit_intro  -> adopts the compose.prepend_intro composite (NEVER renders
                                              MoviePy in-lock; un-prewarmed plan waits for next pass)
          stitch_render.approved_disabled_count -> logged so a disabled format's frozen plans are visible

Separately, per-account creative-variation path (outside pipeline.advance, called from crosspost.py / Studio):
   crosspost.render_moment_file -> clip.render_account_cut(led, cfg, moment_id, aspect, profile, hook, out_path)
                                       -> clip._resolve_framing (same content-adaptive crop)
                                       -> clip.render_reframed (same fail-open ladder)
                                     OR falls back to overlay.burn_hook_only(shared_clip, out, hook)
                                       [cheap 2nd-pass hook burn on the ALREADY-rendered shared clip]
   studio.actions.reburn_hook -> same two functions, operator-triggered re-burn

compose.compose_clip(base, out, TemplateSpec) -> cli.cmd_compose  [operator-run CLI verb, outside advance();
   MoviePy 2.x intro/outro cards + title + crossfade — NOT part of the autonomous pipeline]
```

## Per-file breakdown

### `clip.py` — render a Moment into platform-ready clips (762 lines)

**Module constants**
- `_TARGETS = {"9:16": (1080,1920), "1:1": (1080,1080), "16:9": (1920,1080)}` (clip.py:19) — target render pixel size per aspect; also drives the `.ass` `PlayResX/Y` so libass scaling matches.
- `_FFMPEG_TIMEOUT = 600.0` (clip.py:24) — hard bound on one ffmpeg render; prevents an unbounded hang from holding the ledger flock (`render_moment` runs inside `advance()`'s transaction).
- `_MIN_CLIP_S, _MAX_CLIP_S = TALK.lo, TALK.hi` (clip.py:31) — default band floor/ceiling (12.0–22.0s) used as `fit_window`'s default `lo`/`hi` when no band is passed.
- `_SNAP_MAX_SHIFT_S = 1.5` (clip.py:35) — max seconds `snap_window` may nudge a cut edge onto a transcript-line boundary.
- `_VSTART_MAX_SHIFT_S = 1.5`, `_VSTART_CANDIDATES = 5`, `_VSTART_PROBE_TIMEOUT = 30.0`, `_VSTART_MIN_MOVE_S = 0.05` (clip.py:83-86) — bounds for the strongest-opening-frame entry refinement (`pick_visual_start`).
- `_VSTART_V = 2` (clip.py:89) — vstart sidecar schema version; a stale (pre-sharpness) sidecar is a cache miss.
- `_SCENE_NEAR_S = 0.3` (clip.py:90) — a scene-cut peak within this of a visual-start candidate counts as a tiebreak boost.
- `_FACE_FRAC_TALK = 0.42` (clip.py:200) — target on-screen face-box height fraction for talk content (deliberately upsized from a prior 0.32; bounded by `_ZOOM_MAX`).
- `_FACE_FRAC_MUSIC = 0.26` (clip.py:202) — target face fraction for music/performance (wider, keeps stage/body context).
- `_EYELINE_FRAC = 0.40` (clip.py:203) — eyes placed at 40% of output height (upper third).
- `_ZOOM_MAX = 1.6` (clip.py:204) — max zoom magnification for the static single-subject crop.
- `_ZOOM_MAX_TRACK = 1.7` (clip.py:205) — per-shot zoom cap for a near speaker in a 2-shot (segment render).
- `_GENTLE_MIN_FACE_FRAC = 0.12`, `_GENTLE_ZOOM_MAX = 1.15` (clip.py:207-208) — an already-9:16 source only gets a gentle zoom when the detected face is smaller than 0.12, capped at 1.15x.
- `_SMALL_FACE_FRAC = 0.18` (clip.py:422) — below this source face-height fraction the subject is classified FAR (typically profile + mic-occluded).
- `_ZOOM_MAX_FAR = 1.25` (clip.py:424) — the far-subject zoom cap: held wide for context rather than punched in on the mic.
- `_REFRAME_GEOM_V = 4` (clip.py:504) — geometry-math version stamped into the render fingerprint; bump forces re-render after a crop-math change (v4 = static locked-off crop, adaptive far-speaker zoom, min-shot merge).

**Functions**
- `_nearest(value, candidates, max_shift)` (clip.py:37) — pure: nearest candidate within `max_shift`, else `None`. Called by `snap_window`.
- `snap_window(start, end, transcript, *, duration=0.0, max_shift=_SNAP_MAX_SHIFT_S)` (clip.py:41) — nudges `[start,end]` onto nearby transcript-line boundaries so a clip never begins mid-word/ends mid-phrase; returns unchanged when no transcript or when snapping would invert the window. Pure. Called by `render_moment`, `render_account_cut`.
- `fit_window(start, end, duration, *, lo=_MIN_CLIP_S, hi=_MAX_CLIP_S)` (clip.py:62) — fits a picked window to `[lo,hi]` seconds: in-band unchanged, short grows forward, long trims to `hi`, source shorter than `lo` yields the whole source. Pure. Called by `render_moment`, `render_account_cut`, `moments.request_moment_hooks`.
- `_vstart_candidate_times(start, end)` (clip.py:92) — evenly-spaced candidate entry times including `start` itself. Pure. Called by `pick_visual_start`.
- `_signalstats_cmd(src, t)` (clip.py:101) — builds the ffmpeg signalstats probe command (one frame, luma/contrast). Pure command-builder. Called by `_probe_frame_strength`.
- `_sharpness_cmd(src, t)` (clip.py:108) — builds the Laplacian-convolution sharpness probe command. Pure. Called by `_probe_frame_sharpness`.
- `_probe_frame_sharpness(src, t)` (clip.py:116) — shells the sharpness probe; fail-open to `None` on `FileNotFoundError/OSError/TimeoutExpired`. **Shells ffmpeg.** Called by `_probe_frame_strength`.
- `_probe_frame_strength(src, t)` (clip.py:126) — shells the signalstats probe, returns `(luma, contrast, sharpness)` or `None`; fail-open. **Shells ffmpeg (2 passes via sharpness call).** Called by `pick_visual_start`.
- `_scene_score_near(scene_peaks, t)` (clip.py:142) — pure tiebreak: best scene-cut score near time `t`; fail-open per malformed peak. Called by `pick_visual_start`.
- `pick_visual_start(src_path, start, end, *, scene_peaks, out_dir)` (clip.py:157) — refines the cut entry onto the strongest opening frame within `_VSTART_MAX_SHIFT_S`; caches the decision in a per-window sidecar (`vstart_<hash>.json`) so the in-lock commit pays no ffmpeg cost after the lock-free pre-warm. **Shells ffmpeg (via `_probe_frame_strength`), writes a sidecar file.** Called by `render_moment`, `render_account_cut`.
- `_clamp(v, lo, hi)` (clip.py:192) — pure integer clamp. Called by `_place`, `_zoom_h`.
- `_target_frac(content_type)` (clip.py:210) — pure: `_FACE_FRAC_MUSIC` if `content_type=="music"` else `_FACE_FRAC_TALK`. Called by `_segments_filter_complex`, `reframe_filter`.
- `_zoom_h(src_h, ch0, fh, frac, zoom_max=_ZOOM_MAX)` (clip.py:213) — pure: computes the crop extent in the scaled axis so a face of normalized height `fh` fills `frac` of output, bounded by `zoom_max`; `fh` falsy → no zoom. Called by `_already_aspect`, `_crop_box`, `_focus_crop`, `_track_crop`.
- `_place(src_w, src_h, cw, ch, fx, ay, eyeline)` (clip.py:222) — pure: clamped crop origin `(x,y)` centered on `fx`, vertically anchored so `ay` lands at `eyeline` fraction. Called by `_already_aspect`, `_crop_box`, `_focus_crop`, `_track_crop`.
- `_step_expr(bounds, vals)` (clip.py:229) — pure: builds a per-frame ffmpeg `if(lt(t,...))` hard-cut expression through `vals` at `bounds` switch times (instant reframe, not a pan). Called by `_track_crop`.
- `_track_crop(track, src_w, src_h, tw, th, ch0, frac, *, axis)` (clip.py:243) — pure: active-speaker crop filter string — one zoom for the window (median face height across segments) + smooth-pan x/y expressions between anchors. Called by `reframe_filter`.
- `_focus_crop(focus, src_w, src_h, tw, th, ch0, frac, *, symbolic_w, symbolic_full)` (clip.py:260) — pure: static subject-lock crop filter string; emits the legacy symbolic form when no zoom applies (byte-identical to pre-zoom renders). Called by `reframe_filter`.
- `reframe_filter(aspect, src_w, src_h, *, top_bias=False, focus=None, track=None, content_type=None)` (clip.py:276) — the top-level `-vf` builder: routes to `_already_aspect`, `_track_crop`, or `_focus_crop`/centered crop depending on source/target aspect ratio and whether `focus`/`track` are given; unknown source dims scale+pad instead of cropping. Pure. Called by `ffmpeg_clip_cmd`.
- `_already_aspect(tw, th, src_w, src_h, focus, frac)` (clip.py:314) — pure: source already at target aspect → scale-only passthrough, unless a small face (`fh < _GENTLE_MIN_FACE_FRAC`) triggers a bounded gentle zoom (`_GENTLE_ZOOM_MAX`). Called by `reframe_filter`.
- `ffmpeg_clip_cmd(src, dst, start, end, aspect, *, src_w=0, src_h=0, extra_vf=None, top_bias=False, focus=None, track=None, content_type=None)` (clip.py:327) — builds the single-pass ffmpeg command list (`-ss` before `-i`, `-to` as a duration). Pure command-builder. Called by `render_reframed`.
- `_crop_box(fx, fy, fh, ey, src_w, src_h, tw, th, ch0, frac, zoom_max)` (clip.py:349) — pure: shared numeric crop-sizing math for both the static focus crop and per-segment active-speaker crops, using the face-size-adaptive zoom cap. Called by `_segment_chain`.
- `_ch0_for(aspect_value, src_w, src_h)` (clip.py:362) — pure: baseline crop extent in the scaled axis for source→target, or `None` if source already matches target aspect. Called by `_segments_filter_complex`.
- `_segment_chain(idx, seg, src_w, src_h, tw, th, ch0, frac)` (clip.py:373) — pure: one concat-input's video-chain string (crop this segment's own speaker, scale, label). Called by `_segments_filter_complex`.
- `_segments_filter_complex(track, src_w, src_h, aspect_value, content_type, *, sub_token=None)` (clip.py:383) — pure: the full `-filter_complex` string joining per-segment crops via `concat`, plus optional subtitle burn. Called by `ffmpeg_segments_cmd`.
- `ffmpeg_segments_cmd(src, dst, cs, ce, aspect_value, track, *, src_w, src_h, content_type=None, sub_token=None)` (clip.py:399) — builds the multi-input, single-`filter_complex` ffmpeg command for the segment-concat render (one seeked input per active-speaker segment). Pure command-builder. Called by `render_reframed`.
- `_adaptive_zoom_max(fh, base)` (clip.py:427) — pure: returns `_ZOOM_MAX_FAR` for a far/small subject (`fh < _SMALL_FACE_FRAC`), else `base`. Called by `_crop_box`, `_focus_crop`.
- `render_reframed(src_path, dst, cs, ce, aspect_value, *, src_w, src_h, extra_vf=None, top_bias=False, focus=None, track=None, content_type=None, timeout=_FFMPEG_TIMEOUT)` (clip.py:433) — **THE fail-open render ladder** (traced in full above): segment-concat first (real 2-shot track), falls through to single-pass crop on any rejection. **Shells ffmpeg (1 or 2 subprocess.run calls).** Called by `render_moment`, `render_account_cut`.
- `_subtitles_vf(led, cfg, moment_id, cid, aspect, *, clip_start, clip_end)` (clip.py:454) — builds the burned on-screen-text `-vf` fragment (hook and/or opt-in transcript captions), or `None`; fail-open on missing text-filter support or empty ASS text (flags `hook_burn_failed=True` rather than blocking the clip). **Side effect**: writes `.ass` file via `overlay.write_ass`; logs via `get_logger`. Called by `render_moment`.
- `_render_fingerprint(src_path, cs, ce, aspect_value, src_w, src_h, ass_text, *, top_bias=False, focus=None, track=None, content_type=None)` (clip.py:507) — pure: sha256 over everything that determines the rendered bytes (source, window, aspect, dims, ass text, and — only when a zoom/dynamic crop applies — top_bias/focus/track/content_type/`_REFRAME_GEOM_V`), so a fingerprint match proves the on-disk mp4 matches the intended render. Called by `render_moment`.
- `_resolve_framing(cfg, src, cs, ce)` (clip.py:527) — **the reframe-strategy router** (traced in full above). Called by `render_moment`, `render_account_cut`.
- `_fingerprint_matches(fp_path, fp)` (clip.py:552) — pure/IO-guarded: reads the sidecar and compares `fp`; fail-open `False` on any read/parse error. Called inline by `render_moment` (not in call-graph as a separate hit but used at clip.py:619).
- `_probe_duration(path)` (clip.py:562) — probes a rendered output's duration via ffprobe (through `ingest.probe_dimensions`); `None` on any `ToolchainMissingError/OSError/ValueError`. Module-level so tests can patch without a real ffprobe. Called by `render_moment`, `compose._probe`.
- `render_moment(led, cfg, moment_id, *, aspect=Fmt.r9x16, cut_window=None, clip_id=None, born_state=ClipState.rendered)` (clip.py:571) — **THE main clip-render entrypoint.** Computes the cut window (bare path: band→fit_window→snap_window→optional pick_visual_start; stitch path: caller's `cut_window` verbatim), resolves framing via `_resolve_framing`, builds subtitle overlay via `_subtitles_vf`, computes the render fingerprint, adopts an existing matching mp4 if the fingerprint matches (idempotent skip — no ffmpeg), else calls `render_reframed` and classifies the result into a `Clip` row (`rendered`/`stitch_draft` on success, `error` with a typed reason on any failure: missing toolchain, timeout, nonzero rc/empty output, or — for stitches — a duration mismatch). **Side effects**: mkdir, ffmpeg subprocess, `.ass`/`.render.json` sidecar writes, mutates `led.clips`, sets moment state. Called by `render_aspects_for`, `crosspost._clip_for_aspect`, `stitch_render._commit_impact`, `stitch_render._prewarm_impact`, `studio.actions_approve._warm_hooked_render`, `studio.actions_approve.approve_with_hook`.
- `render_aspects_for(led, cfg, moment_id, *, aspects)` (clip.py:694) — renders one clip per requested aspect for a moment (skips retired moments); loops `render_moment` per aspect. **Side effects**: same as `render_moment`, N times. Called by `pipeline._stage_render_and_caption`, `produce._produce_one`.
- `render_account_cut(led, cfg, moment_id, *, aspect, profile, hook, out_path, top_bias=False)` (clip.py:706) — the per-account creative-variation cut: re-cuts the SAME source at the account's own length band + burns its own hook, in one ffmpeg pass written atomically (`.part` + `os.replace`); mints NO `Clip` row and advances NO moment state (the shared bare clip owns the moment anchor). Fail-open (`except Exception: return False, None`) so the caller falls back to `overlay.burn_hook_only` on the shared clip. **Side effects**: ffmpeg subprocess, atomic file write, temp-file cleanup in `finally`. Called by `crosspost.render_moment_file`, `clip.render_moment` (pipeline burn).

### `framing.py` — subject-aware reframe detection (530 lines)

**Module constants**
- `_SIDECAR_V = 5` (framing.py:15) — speaker-track/saliency sidecar schema version (v5 = + min-shot-duration merge).
- `_KF_COUNT = 5` (framing.py:16) — frames sampled for the (now largely superseded direct) keyframe count reference.
- `_KF_WIDTH = 960` (framing.py:18) — detection sampling width; a 1080p face lands at ~74px, reliably detected.
- `_MIN_CONF = 0.34` (framing.py:19) — `subject_focus` needs a face in ≥34% of sampled frames (≥2 of 5), else fail-open to centered crop.
- `_SCORE_THRESH = 0.6` (framing.py:20) — YuNet confidence floor (proven 6/6 detection on real interview footage at this threshold).
- `_MODEL = "yunet_2023mar.onnx"` (framing.py:21) — vendored YuNet face detector (opencv_zoo, 232KB), shipped in `src/fanops/data/`.
- `_DETECT_V = 1` (framing.py:64) — detect-grid sidecar schema version.
- `_DETECT_FPS = 4.0` (framing.py:65) — grid sampling rate for `detect_window`/classification (cheap, sufficient for ~1s decisions).
- `CT_MULTI = "multi-speaker-talk"`, `CT_SINGLE = "single-speaker-talk"`, `CT_MUSIC = "music"`, `CT_SILENT = "silent"`, `CT_NOPEOPLE = "no-people"` (framing.py:179-183) — the five content-type classification strings.
- `_SPEECH_MIN_WORDS = 2` (framing.py:184) — ≥2 alphabetic word tokens overlapping the window counts as real speech (mirrors `transcribe.real_transcript_signal`'s bar).
- `_ASD_FPS = 9.0` (framing.py:224) — per-frame active-speaker-detection sampling rate; finer than the 4fps classify grid because mouth-motion needs it (resolves "who's talking" to ~0.1s).
- `_ASD_HOLD_S = 0.35` (framing.py:226) — minimum dwell before the committed speaker switches (anti-flicker hysteresis; was 0.8s, then ~4s before that — now lands cuts within ~0.45s of the real turn).
- `_ASD_RATIO = 1.2` (framing.py:228) — the talker's mouth-motion must exceed the other side's by this factor to be the instantaneous speaker.
- `_ASD_SAME_TOL = 0.08` (framing.py:229) — two centroids within this normalized x are treated as "the same shot" and merged.
- `_ASD_SIDE_SPLIT = 0.5` (framing.py:230) — faces left/right of this normalized x are different speakers (the 2-shot split line).
- `_ASD_MIN_SEG_S = 1.5` (framing.py:231) — a shot shorter than this is a brief interjection, absorbed into its neighbour (prevents rapid cut-away-and-back).

**Functions**
- `_cv2()` (framing.py:23) — lazy import of `cv2`; returns `None` (never raises) if the `[framing]` extra isn't installed. Called by `detect_window`, `motion_saliency`, `speaker_track`.
- `_model_path()` (framing.py:31) — pure path to the vendored YuNet ONNX under `src/fanops/data/`. Called by `_detector`.
- `_detector(cv2)` (framing.py:36) — builds a `cv2.FaceDetectorYN` from the vendored model; `None` on any build error or missing asset (fail-open). Called by `detect_window`, `speaker_track`.
- `_wkey(start, end)` (framing.py:49) — pure: `"{start:.2f}-{end:.2f}"` sidecar cache key. Called by `_compute_track`, `detect_window`, `motion_saliency`, `speaker_track`.
- `_load_cache(path)` (framing.py:52) — reads a sidecar's `windows` dict; `{}` on stale version/corrupt JSON/non-dict. Called by `motion_saliency`, `speaker_track`.
- `_detect_faces(cv2, det, img_path)` (framing.py:67) — every face in one frame as normalized `(cx, cy, fh, ey)` (centroid, face-box height, eye-line y); fail-open `[]` on any read/detect error, `ey=cy` fallback if eye landmarks are absent. Called by `detect_window`.
- `_detect_sidecar(cfg, source_id)` (framing.py:92) — pure path builder: `<agent_io>/framing/<source_id>.detect.json`. Called by `detect_window`.
- `_load_detect_cache(path)` (framing.py:95) — like `_load_cache` but for the detect-grid sidecar (`_DETECT_V`). Called by `detect_window`.
- `detect_window(cfg, src, *, start, end)` (framing.py:104) — **THE single detection pass**: one grid extraction over `[start,end)`, caching every face's `(cx,cy,fh,ey)` per frame to `<source_id>.detect.json`; feeds `classify_window`, `subject_focus`, and (indirectly, via the same on-disk cache being consulted first) is the fast-path check for `speaker_track`/`motion_saliency`'s own grid passes. Bracketed by a per-`(framing, source_id)` `stage_lock` so two concurrent callers don't race the sidecar; cache is checked both before and after lock acquisition. Returns `None` on every fail-open path. **Shells ffmpeg** (via `keyframes.extract_frames_grid`), writes a sidecar. Called by `clip._resolve_framing`, `subject_focus`.
- `_window_has_speech(src, start, end)` (framing.py:186) — pure: `True` if the transcript has ≥`_SPEECH_MIN_WORDS` alphabetic tokens overlapping the window. Called by `classify_window`.
- `_face_count(stats)` (framing.py:201) — pure: median per-frame face count from detect stats (a liberal estimate — `speaker_track` still requires two stable positions to actually switch). Called by `classify_window`.
- `classify_window(cfg, src, *, start, end, stats)` (framing.py:211) — **pure routing function**, no ffmpeg/cv2: maps face-count + speech + vocals-isolated meta to one of the five `CT_*` strings. Called by `clip._resolve_framing`.
- `_mouth_roi(cv2, img, face)` (framing.py:234) — pure/fail-open: fixed-size grayscale crop of a YuNet face's mouth region for frame-to-frame motion comparison; `None` on any crop failure. Called by `_track_observe`.
- `_track_sidecar(cfg, source_id)` (framing.py:250) — pure path builder: `<agent_io>/framing/<source_id>.track.json`. Called by `speaker_track`.
- `_track_observe(cv2, det, frames)` (framing.py:253) — per-frame observation of each 2-shot side (L/R split), returning `{side: ((fx,fy,fh,ey), motion)}` where motion is the mouth-ROI mean-abs-diff vs the previous frame; fail-open empty dict per bad frame. Called by `_compute_track`.
- `_pctl(vals, q)` (framing.py:294) — pure: nearest-rank q-quantile on a sorted copy; used for per-segment face height (p75 — the clearest full-face detection, not the median, which an occlusion/profile-turn would drag down). Called by `_assemble_track`.
- `_merge_brief_segments(segs)` (framing.py:304) — pure: absorbs any shot shorter than `_ASD_MIN_SEG_S` into a neighbour, then re-coalesces adjacent same-position shots. Called by `_assemble_track`.
- `_assemble_track(obs, fps)` (framing.py:324) — **pure reduction**: per-frame observations → active-speaker segments `[t0,t1,fx,fy,fh,ey]` via louder-mouth instantaneous talker + hysteresis-dwell commit + segment grouping + coalescing + brief-segment merge; `None` if only one position exists (static path is identical + cheaper). Called by `_compute_track`.
- `speaker_track(cfg, src, *, start, end, src_w, src_h)` (framing.py:376) — the active-speaker segment list or `None` (fail-open signal to use static `subject_focus`); cached per `(source, window)`. **Shells ffmpeg** (its own grid pass at `_ASD_FPS`, separate from `detect_window`'s 4fps grid), writes a sidecar. Called by `clip._resolve_framing`.
- `_compute_track(cv2, det, cfg, src, start, end)` (framing.py:407) — `speaker_track`'s detection body: grid extraction → `_track_observe` → `_assemble_track`; snaps the first/last segment to `[0, dur]` so the render's time-expression covers the whole clip. Called by `speaker_track`.
- `_median_face(stats)` (framing.py:435) — pure: dominant (largest-fh) face per frame, reduced to median `(fx,fy,fh,ey)` + detection confidence over the window. Called by `subject_focus`.
- `subject_focus(cfg, src, *, start, end)` (framing.py:448) — the dominant subject as `(fx,fy,fh,ey)`, reduced from the SAME `detect_window` grid pass (no separate probe); `None` if confidence `< _MIN_CONF`. Called by `clip._resolve_framing`.
- `_saliency_centroid(cv2, frames)` (framing.py:460) — pure/fail-open: normalized centroid of inter-frame pixel change across the grid; `None` if no usable motion. Called by `motion_saliency`.
- `_saliency_sidecar(cfg, source_id)` (framing.py:486) — pure path builder: `<agent_io>/framing/<source_id>.saliency.json`. Called by `motion_saliency`.
- `motion_saliency(cfg, src, *, start, end)` (framing.py:489) — for music/silent/no-people windows with no face: the motion centroid `(fx,fy)` (never carries a face height, so it never zooms — pan-only); cached per `(source, window)`. **Shells ffmpeg** (its own grid pass). Called by `clip._resolve_framing`.

### `keyframes.py` — still-frame extraction for hook authoring + face detection (145 lines)

**Module constants**
- `_KF_TIMEOUT = 30.0` (keyframes.py:23) — bound on one `extract_keyframes` ffmpeg spawn (per-frame).
- `_GRID_TIMEOUT = 60.0` (keyframes.py:48) — bound on one `extract_frames_grid` ffmpeg spawn (whole window, single pass).

**Functions**
- `extract_keyframes(video_path, start, end, *, count=3, out_dir, width=480, timeout=_KF_TIMEOUT)` (keyframes.py:25) — returns up to `count` jpeg paths sampled evenly strictly inside `(start,end)`, one ffmpeg spawn PER frame; `[]` on non-positive window or ffmpeg absence/timeout (fail-open). **Shells ffmpeg** (N times). Called by `intro_match._frames`, `intro_match._thumb`, `moments._source_frames`, `moments._window_frames`.
- `_window_cache_key(*, source_id, start, end, fps, width)` (keyframes.py:51) — pure: sha256 content-address over the inputs that determine the grid output. Called by `extract_frames_grid`.
- `_cache_dir_for(cfg, *, source_id, window_hash)` (keyframes.py:61) — pure path builder: `<agent_io>/keyframes/<source_id>/<window_hash>/`. Called by `extract_frames_grid`.
- `_existing_cached_frames(cache_dir)` (keyframes.py:67) — pure read: sorted jpg paths already on disk (cache-hit short-circuit), `[]` if none. Called by `extract_frames_grid`.
- `extract_frames_grid(video_path, start, end, *, fps, out_dir, width=960, timeout=_GRID_TIMEOUT, source_id=None, cfg=None)` (keyframes.py:76) — **THE single-pass grid sampler feeding `framing.py`'s `detect_window`/`speaker_track`/`motion_saliency`**: one ffmpeg `-vf fps=N,scale=W:-2` pass across the whole window (vs N spawns for `extract_keyframes`). Opt-in content-addressed caching + a per-`(keyframes, window_hash)` `stage_lock` when `source_id` is given (keyed on window hash, not source_id, to avoid self-deadlocking when called from inside `framing.detect_window`'s own `(framing, source_id)` lock); legacy byte-identical path when `source_id` is omitted. Fail-open `[]` on non-positive window, ffmpeg absence/timeout/nonzero exit. **Shells ffmpeg, mkdir, disk writes.** Called by `framing._compute_track`, `framing.detect_window`, `framing.motion_saliency`.
- `_run_grid_extract(video_path, start, end, *, fps, out_dir, width, timeout)` (keyframes.py:129) — the shared ffmpeg-spawn body for both the cached and legacy paths of `extract_frames_grid`. **Shells ffmpeg.** Called by `extract_frames_grid`.

### `stitch_render.py` — the structural-hooks stitch producer (357 lines)

**Module constants**
- `_NON_BASE_STATES = (ClipState.error, ClipState.retired, ClipState.stitch_draft)` (stitch_render.py:24) — a bare clip in any of these is never a valid stitch base.
- `_IMPACT_AWAITING`, `_IMPACT_STITCHED` (stitch_render.py:25-26) — router key constants for the impact-cut strategy.
- `INTRO_STRATEGY = "intro_tease"`, `_INTRO_AWAITING` (stitch_render.py:30-31) — the intro-tease strategy key + router awaiting-state.
- `INTRO_TEASE_SECONDS = 2.0` (stitch_render.py:32) — the "wait for it" intro display duration.
- `MAX_INTRO_RENDER_ATTEMPTS = 3` (stitch_render.py:36) — a flaky intro compose is parked (errored) after this many failed in-lock commit attempts.
- `MAX_SUGGESTIONS_PER_PASS = 5` (stitch_render.py:68) — anti-spam cap on new suggestions minted per `mine_suggestions` pass.

**Functions**
- `_read_fingerprint(cfg, clip_id)` (stitch_render.py:39) — reads a base clip's pinned render fingerprint from its sidecar; `None` if absent/unreadable. Called by `_commit_intro`, `_impact_cut_candidates`, `_intro_tease_candidates`, `_precheck`, `_prewarm_intro`.
- `_intro_fail_fp(cfg, cid)` (stitch_render.py:48) — the fingerprint of the last GENUINE intro-compose failure (only stamped when the prewarm actually attempted and failed), distinguishing a real unrenderable pairing from a transient "not yet warmed" miss. Called by `_commit_intro`.
- `_clear_intro_fail(cfg, cid)` (stitch_render.py:59) — best-effort unlink of the failure marker on a warm adopt or later success. Called by `_commit_intro`, `_prewarm_intro`.
- `_impact_cut_candidates(led, cfg, log)` (stitch_render.py:71) — read-only mining: collects `(plan, moment_id)` pairs for router-reserved impact-cut moments via `impact_cut.make_stitch_plan`; per-candidate fail-open (`except Exception: log+skip`, pass continues). Called by `mine_suggestions`.
- `_intro_tease_candidates(led, cfg, log)` (stitch_render.py:94) — read-only mining: collects intro-tease candidates from moments whose matcher pairings landed (`Moment.intro_matches`); pairs ONLY the top (best-fit) match per moment (no rank-2 fallback by design); gated on `cfg.intro_tease`. Called by `mine_suggestions`.
- `_enabled(strategies, key)` (stitch_render.py:131) — pure: `True` if `strategies is None` (all) or `key in strategies`. Called by `_approved_plans`, `mine_suggestions`.
- `mine_suggestions(led, cfg, log=None, strategies=None)` (stitch_render.py:137) — **the routine pairing pass (M5)**: collects candidates across enabled strategies, dedupes against the ledger, ranks by `rank_score` (deterministic tie-break on id), emits up to `MAX_SUGGESTIONS_PER_PASS` new plans, and re-routes a moment's `hook_strategy` from `clean_awaiting_strategy:<key>` to `stitch:<key>` only once ALL its candidates exist. Ledger-only mutation (safe in-lock); renders nothing. Called by `pipeline._stage_structural_hooks`.
- `_stitch_clip_id(plan_id, aspect_value)` (stitch_render.py:168) — pure: content-addressed stitched-clip id, keyed on plan+aspect so it can never collide with the bare clip's id. Called by `_commit_impact`, `_intro_render_target`, `_prewarm_impact`.
- `_cut_in_range(params, src)` (stitch_render.py:173) — pure: `True` iff `0 <= cut_start < cut_end` and (when source duration is known) `cut_end` doesn't run past EOF. Called by `_commit_impact`.
- `_approved_plans(led, strategies=None)` (stitch_render.py:186) — pure filter: approved-but-unrendered plans restricted to the enabled strategy set. Called by `prewarm_approved_stitches`, `render_approved_stitches`.
- `approved_disabled_count(led, *, enabled)` (stitch_render.py:193) — pure count of approved plans belonging to a currently-disabled format (so a kill-switch never silently and invisibly freezes plans). Called by `pipeline._stage_structural_hooks`.
- `_intro_render_target(led, cfg, p)` (stitch_render.py:200) — resolves `(base_clip, intro_source, stitch_cid, out_path)` for an intro-tease plan, or `None` if the base clip or intro asset is gone. Called by `_commit_intro`, `_prewarm_intro`.
- `_intro_compose_fp(led, base, intro, params)` (stitch_render.py:212) — the compose fingerprint pinning a prewarmed intro composite (mirrors `clip._render_fingerprint`), calling `compose._compose_fingerprint`. Called by `_commit_intro`, `_prewarm_intro`.
- `prewarm_approved_stitches(led, cfg, log, strategies=None)` (stitch_render.py:222) — **lock-free**: renders each approved plan's mp4 + fingerprint sidecar so the in-lock commit can adopt it with no heavy render under the lock; dispatches by `strategy_key` (impact_cut → ffmpeg cut, intro_tease → MoviePy compose). Mutations to its throwaway `led` are discarded; only on-disk artifacts persist. **Side effects**: ffmpeg/MoviePy subprocess/in-process render, disk writes. Called by `produce.run_all`.
- `_prewarm_impact(led, cfg, p, render_moment, log)` (stitch_render.py:235) — calls `clip.render_moment` with the plan's cut window; fail-open (`except Exception: log`) — a failure here just means the in-lock commit renders it instead. Called by `prewarm_approved_stitches`.
- `_prewarm_intro(led, cfg, p, log)` (stitch_render.py:247) — calls `compose.prepend_intro`; stamps the fingerprint sidecar ONLY on a real composite, else writes the `.introfail.json` genuine-failure marker (distinguishing a real compose failure from "not yet attempted"). Called by `prewarm_approved_stitches`.
- `render_approved_stitches(led, cfg, strategies=None)` (stitch_render.py:279) — **the in-lock commit**: runs `_precheck` for every approved plan of an enabled format, then dispatches by strategy to `_commit_intro` or `_commit_impact`; deliberately has NO live-base-post guard (a stitch is additive, not a supersede — the bare clip ships regardless). Called by `pipeline._stage_structural_hooks`.
- `_precheck(led, cfg, p)` (stitch_render.py:300) — strategy-agnostic guard: base clip missing → `error`; base fingerprint drifted since planning → auto-`dismissed` ("base re-rendered since planned"); else returns the base `Clip`. Called by `render_approved_stitches`.
- `_commit_impact(led, cfg, p, base, render_moment)` (stitch_render.py:315) — validates the cut range (`_cut_in_range`), calls `clip.render_moment` with the stitch's cut window; sets plan state to `in_use` on success, `error` on failure. Called by `render_approved_stitches`.
- `_commit_intro(led, cfg, p, base)` (stitch_render.py:330) — adopts the prewarmed compose composite by fingerprint match (never renders MoviePy in-lock); distinguishes a genuine compose failure (burns the bounded retry cap, `MAX_INTRO_RENDER_ATTEMPTS`) from a transient not-yet-warmed miss (waits, no cap burn). Called by `render_approved_stitches`.

### `overlay.py` — burned-in subtitles / hook text (339 lines)

**Module constants**
- `_WHITE = "&H00FFFFFF"`, `_BLACK = "&H00000000"` (overlay.py:26-27) — ASS colour constants (white text, heavy black outline — legible on any footage).
- `_HOOK_FADE_MS = 200` (overlay.py:29) — fade-in/out for the opening hook card.
- `_CAPTION_MAX_WORDS = 3` (overlay.py:37) — active captions show at most 3 words per Dialogue group.
- `_CAP_FADE_IN_MS = 100`, `_CAP_FADE_OUT_MS = 60` (overlay.py:38-39) — snappy caption pop-in/out.
- `_TEXTFILTER_CACHE: bool | None = None` (overlay.py:44) — module-global memoization of the ffmpeg text-filter capability probe.
- `_HOOK_EM_RATIO = 0.45`, `_MAX_HOOK_LINES = 2` (overlay.py:79-80) — legibility-warning heuristic tuning (must track `build_ass`'s hook style).
- `_HOOK_FONTSIZE_RATIO = 0.072` (overlay.py:81), `_HOOK_FONTSIZE_FLOOR = 0.052` (overlay.py:82) — auto-fit hook font size cap/floor as fractions of frame height.
- `_HOOK_MARGIN_LR = 60` (overlay.py:83) — hook card left/right margin, must match the `HOOK` style's `MarginL/R` in `build_ass`.
- `_PROBE_TIMEOUT = 30.0`, `_FFMPEG_TIMEOUT = 600.0` (overlay.py:275-276) — bounds for the text-filter capability probe and the hook-burn re-encode.

**Functions**
- `_fmt_ts(seconds)` (overlay.py:47) — pure: formats a non-negative time as ASS `H:MM:SS.cc`. Called by `build_ass`.
- `_escape_text(text)` (overlay.py:61) — pure: normalizes newlines to ASS `\N`, strips stray braces (an ASS override-tag delimiter). Called by `build_ass`.
- `_hook_fontsize(hook, width, height)` (overlay.py:85) — pure: the largest hook font (≤ `_HOOK_FONTSIZE_RATIO` cap, ≥ `_HOOK_FONTSIZE_FLOOR`) that wraps the hook to ≤`_MAX_HOOK_LINES` lines within the usable card width. Called by `build_ass`, `hook_legibility_warnings`.
- `hook_legibility_warnings(hook, *, width, height)` (overlay.py:103) — pure/fail-open: heuristic warnings (never blocks) if the burned hook would likely overflow its top-card area or contains an unbreakably-wide word. Called by `clip._subtitles_vf`.
- `_chunk(items, size)` (overlay.py:129) — pure: splits a list into consecutive groups of at most `size`. Called by `caption_events`.
- `caption_events(seg, clip_start, clip_end, *, max_words=_CAPTION_MAX_WORDS)` (overlay.py:135) — pure: converts ONE source-time transcript segment into a list of `(start,end,text)` active-caption events, rebased/clamped to clip time; uses real word timestamps when whisper provides them, else splits text evenly across the segment's clamped window. Called by `build_ass`.
- `build_ass(segments, *, hook=None, clip_start, clip_end, width=1080, height=1920, font="Arial Unicode MS", max_words=_CAPTION_MAX_WORDS)` (overlay.py:180) — pure: builds the full `.ass` subtitle file text — `[Script Info]` (PlayRes matched to render size), `[V4+ Styles]` (CAPTION bottom-third, HOOK top-third, auto-fit font), `[Events]` (optional hook Dialogue spanning the first `min(2.5, clip_len)`s + active-caption Dialogues per `caption_events`); returns `""` if nothing to burn. Called by `clip._subtitles_vf`, `clip.render_account_cut`, `burn_hook_only`.
- `write_ass(text, path)` (overlay.py:248) — writes the `.ass` text to `path` (UTF-8), creating parent dirs. **Side effect**: disk write. Called by `clip._subtitles_vf`, `clip.render_account_cut`, `burn_hook_only`.
- `subtitles_vf(ass_path)` (overlay.py:256) — pure: the `subtitles=<escaped path>` ffmpeg `-vf` token, with backslash/quote/colon/comma escaping for filtergraph safety. Called by `clip._subtitles_vf`, `clip.render_account_cut`, `burn_hook_only`.
- `ffmpeg_has_textfilter()` (overlay.py:279) — probes `ffmpeg -filters` ONCE, caches the boolean result; never raises (absent/hung/failed probe → `False`). **Shells ffmpeg** (once per process). Called by `clip._subtitles_vf`, `clip.render_account_cut`, `burn_hook_only`.
- `burn_hook_only(base_clip_path, out_path, hook, *, width=1080, height=1920, font="Arial Unicode MS")` (overlay.py:299) — burns ONLY a hook onto an already-rendered base clip (the cheap per-account second pass); fail-open to a byte copy (`shutil.copyfile` + atomic `os.replace`) on no text filter/empty hook/ffmpeg failure; always atomic via `.part` + `os.replace`, sweeps `.ass`/`.part` temp artifacts in a `finally`. **Side effects**: ffmpeg subprocess or file copy, atomic publish, temp cleanup. Called by `crosspost.render_moment_file`, `clip.render_moment` fallback paths.

### `impact_cut.py` — deterministic impact-cut planner (87 lines)

**Module constants**
- `IMPACT_LEAD_EPS = 0.4` (impact_cut.py:24) — seconds before the impact peak the cut lands (the "wait for it" tease stops just before the payoff).
- `DURATION_TOLERANCE = 0.5` (impact_cut.py:25) — post-render duration-check tolerance (also reused by `clip.render_moment`'s stitch validity check and `compose.py`'s composite validity gates).
- `IMPACT_MIN_DURATION = 3.0` (impact_cut.py:26) — a cut shorter than this is degenerate (not a watchable clip) → no plan.
- `STRATEGY_KEY = "impact_cut"` (impact_cut.py:28) — the router/stitch strategy key.

**Functions**
- `_impact_peak(src, lo, hi)` (impact_cut.py:31) — pure: the strongest `signal_peaks` entry inside `[lo,hi]` as `(t, score, peak_dict)` — max score, tie → earliest t; skips non-numeric peaks (semi-trusted sidecar), never raises. Called by `make_stitch_plan`, `plan_impact_cut`.
- `plan_impact_cut(m, src)` (impact_cut.py:46) — pure: computes `{"cut_start": m.start, "cut_end": peak_t - IMPACT_LEAD_EPS}`, or `None` (benign, not an error) when no peak exists in the moment window or the resulting span is below `IMPACT_MIN_DURATION`. Called by `make_stitch_plan`.
- `make_stitch_plan(clip, m, src, *, base_fp)` (impact_cut.py:62) — builds the `suggested`-state `StitchPlan` for an impact-cut, content-addressed on clip id + strategy + params (idempotent re-mining), pinning `base_fp` so a later base re-render auto-dismisses the plan (the supersede rule); names the rationale text differently for an audio-energy-scored peak vs a generic scene-cut peak. Pure. Called by `stitch_render._impact_cut_candidates`.

### `compose.py` — produced-clip compositing via MoviePy (298 lines)

**Module constants**
- `_RENDER_TIMEOUT = 600.0` (compose.py:20) — advisory render bound (parity with clip.py/overlay.py; the real safety contract is that compose always runs outside any ledger flock).
- `_FONT_CANDIDATES` (compose.py:34-43) — cross-platform font-file fallback list (MoviePy 2.x `TextClip` wants a font FILE, not a family name).
- `_IMAGE_EXTS = (".jpg",".jpeg",".png",".webp",".bmp",".gif")` (compose.py:191) — extensions treated as a still-image intro asset (vs a video, trimmed with `VideoFileClip`).

**Classes**
- `TemplateSpec` (compose.py:46, `@dataclass(frozen=True)`) — fields: `title`, `intro_text`, `outro_text`, `title_sec=2.5`, `card_sec=1.5`, `transition_sec=0.5`, `brand_rgb=(20,10,40)`, `font=None`. Deliberately carries NO width/height — the renderer sizes from the base clip's own dimensions.
  - `is_empty()` (compose.py:64) — pure: `True` iff no title/intro_text/outro_text set (an empty spec is a no-op → fail-open copy). Called by `cli.cmd_compose`, `compose_clip`.

**Functions**
- `_compose_fingerprint(base_path, intro_path, params, base_w, base_h)` (compose.py:26) — pure: sha256 over everything determining the composed bytes, mirroring `clip._render_fingerprint`, so the lock-free prewarm and the in-lock commit agree on adoption. Called by `stitch_render._intro_compose_fp`.
- `_default_font()` (compose.py:68) — pure/IO: first existing path in `_FONT_CANDIDATES`, or `None`. Called by `_text_layer`.
- `_failopen(base, out_path, log, reason)` (compose.py:75) — copies the base clip to `out_path` so the caller always ends up with a usable file; logs the reason; a copy failure (base vanished) is itself logged, not hidden. **Side effect**: file copy or logged failure. Called by `compose_clip`, `prepend_intro`.
- `compose_clip(base_clip_path, out_path, spec, *, timeout=_RENDER_TIMEOUT, render=None, probe_duration=None, log=None)` (compose.py:88) — produces a composited clip (intro/outro cards + title + crossfade) or fails open to a base-clip byte-copy on: empty spec, any render exception (ImportError/API drift/render fail), missing/empty output, unprobeable duration, or an output SHORTER than the base minus `DURATION_TOLERANCE` (the "corrupt composite dropped the base body" gate). Returns `True` only for a validated real composite. **Side effects**: MoviePy render (lazy import), file write. Called by `cli.cmd_compose`.
- `_probe(path)` (compose.py:132) — thin wrapper reusing `clip._probe_duration` (mockable). Called by `prepend_intro` (default `probe_duration`); no direct call-graph hit recorded for `compose_clip`'s use since it's passed as a default parameter value, not a direct call site — but functionally the same wrapper.
- `prepend_intro(base_clip_path, intro_asset_path, out_path, *, tease_text, intro_seconds, timeout=_RENDER_TIMEOUT, render=None, probe_duration=None, log=None)` (compose.py:136) — prepends an aspect-normalized intro asset (image or trimmed video) before the base clip with `tease_text` burned over it, over a continuous looped audio bed (no silent opener); fails open to a base-clip byte-copy on: missing intro asset, any render exception, missing/empty output, or a duration outside `DURATION_TOLERANCE` of the expected `intro_seconds + base_duration`. **Side effects**: MoviePy render, file write. Called by `stitch_render._prewarm_intro`.
- `_text_layer(text, spec, w, h, *, top)` (compose.py:177) — builds a centered, stroked `TextClip` sized to the frame; raises on any MoviePy/font failure (caller fail-opens). Called by `_moviepy_prepend_render`, `_moviepy_render`.
- `_moviepy_prepend_render(base_clip_path, intro_asset_path, out_path, *, tease_text, intro_seconds, timeout)` (compose.py:193) — the real MoviePy 2.x prepend renderer: normalizes the intro to the base's aspect, composites tease text over it, concatenates before the (audio-stripped) base, lays a single looped audio bed spanning the whole composite (continuous audio — no silent opener/tail gap/seam restart), writes the output. **Side effects**: heavy in-process MoviePy render, `write_videofile`; closes all opened clip handles in `finally`. Called (as the default `render` callable) by `prepend_intro`; call-graph shows zero direct callers because it's injected as a default parameter, not called by name.
- `_moviepy_render(base_clip_path, out_path, spec, *, timeout)` (compose.py:233) — the real MoviePy 2.x compositor: builds optional intro/outro brand cards + a dynamic title overlay over the base's first `title_sec`, crossfades between segments, writes the output. **Side effects**: heavy in-process MoviePy render; closes handles in `finally`. Called (as the default `render` callable) by `compose_clip`; same zero-direct-caller note as above.

### `produce.py` — the lock-free producer entry point (132 lines)

**Classes**
- `SourceResult` (produce.py:33, `@dataclass(frozen=True)`) — `source_id: str`, `error_reason: str | None = None`. Pure result value; a producer never mutates a shared ledger — it runs the slow chain and returns this.

**Functions**
- `_enabled_strategies(cfg)` (produce.py:43) — pure: `{"impact_cut", "intro_tease"} ∩ {enabled by cfg flags}`. Duplicated (not imported) from `pipeline.py` deliberately to avoid a module-load cycle. Called by `produce.run_all` (and independently duplicated in `pipeline._stage_structural_hooks`, outside this cluster).
- `_produce_one(cfg, source_id, aspects, *, log)` (produce.py:50) — one source's producer pass on a PRIVATE throwaway `Ledger.load(cfg)`: `catalogued`→`transcribe.transcribe_source`, `transcribed`→`signals.detect_signals`, each `decided` moment of this source→`clip.render_aspects_for`. Per-stage exclusion via `stage_lock` inside the called functions. Never raises — every stage is wrapped in `try/except Exception: log+continue`, and a ledger-load failure itself is caught and reported as an `error`-level `SourceResult`. **Side effects**: transcription/signal/render artifact writes (via the called functions); the in-memory `led` mutation is discarded on return — ONLY on-disk artifacts survive. Called by `run_all`.
- `run_all(cfg, aspects, log)` (produce.py:94) — **the single producer entry point `pipeline.advance()` calls**: loads the ledger, iterates all non-third-party source ids, dispatches `_produce_one` per source either sequentially or via a `ThreadPoolExecutor` (gated by `cfg.concurrent_sources`/`cfg.concurrent_workers`), then — if any structural-hook strategy is enabled — calls `stitch_render.prewarm_approved_stitches`. Never raises: a ledger-load failure logs `error` and returns; a worker crash inside the thread pool logs `warn` and continues; the stitch prewarm call is itself wrapped in `try/except Exception: log warn`. Called by `pipeline.advance`.

## Reframe/render decision-logic trace

**Content-type classification determines the reframe strategy, which determines the render rung:**

| `classify_window` result | `_resolve_framing` route | Render rung used |
|---|---|---|
| `CT_MULTI` (≥2 faces + speech) AND `speaker_track` returns ≥2 segments | `(None, track, "multi-speaker-talk")` | **Rung 1**: `ffmpeg_segments_cmd` (segment-concat, per-speaker static crop, hard cuts) |
| `CT_MULTI` but `speaker_track` returns `None`/empty (not a real 2-shot) | reclassified `CT_SINGLE` → `subject_focus` | Rung 2: `ffmpeg_clip_cmd` with `focus` (static subject-lock zoom) |
| `CT_SINGLE` / `CT_MUSIC` / `CT_SILENT` with `subject_focus` confidence ≥ `_MIN_CONF` | `(focus_4tuple, None, ct)` | Rung 2: `ffmpeg_clip_cmd` with `focus` |
| `CT_MUSIC` / `CT_SILENT` / `CT_NOPEOPLE` with a usable `motion_saliency` centroid | `(sal_2tuple, None, None)` | Rung 2: `ffmpeg_clip_cmd` with `focus` (2-tuple → no zoom, pan-only via `_place`) |
| Everything else (no `[framing]` extra, `cfg.smart_framing` off, or every detector call failed) | `(None, None, None)` | Rung 2: `ffmpeg_clip_cmd`, centered crop (today's original default) |

**Fail-open guarantee, cited exactly:**
- `render_reframed` (clip.py:433-452): Rung 1 only fires `if track and len(track) > 1`. If it fires and `subprocess.run` returns `rc==0` and `dst` exists with `size>0`, the function returns immediately. Any other outcome (nonzero rc, missing/empty output, or Rung 1 skipped entirely) falls through unconditionally to Rung 2 — `ffmpeg_clip_cmd` is always the terminal safety net; there is no path where a segment-graph rejection propagates as an error.
- Every detection function in `framing.py` (`detect_window`, `classify_window`, `speaker_track`, `subject_focus`, `motion_saliency`) is independently wrapped so any exception, absent `cv2`, absent detector, absent frames, or low confidence returns `None`/a benign default rather than raising — confirmed by the `except Exception: return None`/`result = None` patterns at framing.py:28, 46, 88, 153, 247, 289, 397, 475, 515.
- `clip._resolve_framing` itself has no try/except because every callee it invokes already guarantees a non-raising `None`/tuple result — the fail-open contract is pushed down to the leaf detectors, not re-implemented at the router.
- At the outermost layer, `render_moment` (clip.py:571-692) converts even a total `render_reframed` failure (`FileNotFoundError`/`OSError`/`TimeoutExpired`, or a returned nonzero-rc/empty-output result) into a typed `ClipState.error` row rather than letting an exception escape into the pipeline transaction — this is the final backstop above the two-rung ladder.

**Key numeric constants cited exactly (file:line, variable name, value):**
- `clip.py:200` `_FACE_FRAC_TALK = 0.42`
- `clip.py:202` `_FACE_FRAC_MUSIC = 0.26`
- `clip.py:203` `_EYELINE_FRAC = 0.40`
- `clip.py:204` `_ZOOM_MAX = 1.6`
- `clip.py:205` `_ZOOM_MAX_TRACK = 1.7`
- `clip.py:207` `_GENTLE_MIN_FACE_FRAC = 0.12`
- `clip.py:208` `_GENTLE_ZOOM_MAX = 1.15`
- `clip.py:422` `_SMALL_FACE_FRAC = 0.18`
- `clip.py:424` `_ZOOM_MAX_FAR = 1.25`
- `clip.py:504` `_REFRAME_GEOM_V = 4`
- `framing.py:19` `_MIN_CONF = 0.34`
- `framing.py:20` `_SCORE_THRESH = 0.6`
- `framing.py:65` `_DETECT_FPS = 4.0`
- `framing.py:184` `_SPEECH_MIN_WORDS = 2`
- `framing.py:224` `_ASD_FPS = 9.0`
- `framing.py:226` `_ASD_HOLD_S = 0.35`
- `framing.py:228` `_ASD_RATIO = 1.2`
- `framing.py:229` `_ASD_SAME_TOL = 0.08`
- `framing.py:230` `_ASD_SIDE_SPLIT = 0.5`
- `framing.py:231` `_ASD_MIN_SEG_S = 1.5`
- `impact_cut.py:24` `IMPACT_LEAD_EPS = 0.4`
- `impact_cut.py:25` `DURATION_TOLERANCE = 0.5`
- `impact_cut.py:26` `IMPACT_MIN_DURATION = 3.0`

## Anomalies found

**Dead code / zero-direct-caller candidates (per call_graph.json):**
- `src/fanops/compose.py:193` `_moviepy_prepend_render` — `called_by_in_repo: []`. NOT actually dead: it's the default value of `prepend_intro`'s `render=` parameter (`renderer = render or _moviepy_prepend_render`, compose.py:152), so the AST-based call graph misses it because it's referenced, not called by name. Confirmed live via manual read.
- `src/fanops/compose.py:233` `_moviepy_render` — same pattern: default value of `compose_clip`'s `render=` parameter (compose.py:101). Not dead — a call-graph limitation, not an orphan.
- `src/fanops/compose.py:132` `_probe` — `called_by_in_repo: []` recorded for `compose_clip`, but it IS the default `probe_duration=` value for both `compose_clip` (compose.py:117 `probe = probe_duration or _probe`) and `prepend_intro` (compose.py:153). Same default-parameter blind spot in the graph, not a real dead-code finding.

**No genuinely dead functions found in this cluster** — every function in all 8 files traces to at least one real caller (direct or via a default-parameter injection point).

**Fail-open exception handlers (all intentional and documented in-line, cited for completeness — not defects):**
- `src/fanops/clip.py:752` `except Exception: return False, None` in `render_account_cut` — deliberate: "a clip is never blocked on its variant"; caller falls back to `overlay.burn_hook_only` on the shared clip.
- `src/fanops/framing.py:28,46,88,153,247,289,397,475,515` — nine `except Exception` sites, each documented inline as "fail-open by contract" degrading to `None`/`[]`/centered-crop. All consistent with the module's stated NEVER-raises contract.
- `src/fanops/stitch_render.py:87,125` — per-candidate fail-open in `_impact_cut_candidates`/`_intro_tease_candidates`: "a poisoned pair must not wedge the loop" — logs and continues the mining pass.
- `src/fanops/stitch_render.py:243` `_prewarm_impact` — fail-open: "the commit pass renders it in-lock instead" (a documented two-tier retry, not a silent loss).
- `src/fanops/stitch_render.py:273` `_prewarm_intro` — "belt-and-braces" catch around an already-fail-open `prepend_intro`; writes the genuine-failure marker even on this rare outer-catch path.
- `src/fanops/compose.py:104,157` — the two top-level `compose_clip`/`prepend_intro` render-exception catches, both explicitly logging the reason before fail-opening to a base-clip byte-copy — never a silent swallow.
- `src/fanops/produce.py:69,80,89,107,118,130` — six sites, every one logging via `log("produce", ..., "error"/"warn", err=...)` before continuing; none silently swallows.

**No TODO/FIXME/XXX markers** found in any of the 8 files (grep confirmed zero hits).

**No bare `except:`** anywhere in the 8 files — every handler is typed `except Exception` or narrower (e.g. `except (FileNotFoundError, OSError, subprocess.TimeoutExpired)`), consistent with the discipline observed in C2/C4.

**Two independently-timed grid extraction passes per multi-speaker window** (not a bug, but a real per-clip cost worth flagging): `framing.detect_window` runs its own grid pass at `_DETECT_FPS=4.0` to classify the window, and — only when classification lands on `CT_MULTI` — `framing.speaker_track` runs a SECOND, independent grid pass at `_ASD_FPS=9.0` (finer, because mouth-motion detection needs it) via its own call into `keyframes.extract_frames_grid`. The two passes are NOT shared even though they sample the same window, because they need different fps and `_track_observe` needs raw pixels (mouth-ROI motion) that the JSON-cached `detect_window` stats don't carry. This is explicitly acknowledged in `framing._compute_track`'s docstring ("The active-speaker decision needs PIXELS... only multi-speaker windows pay it") — a deliberate, bounded, and self-documented cost rather than an oversight, but it does mean every real 2-shot clip pays for two ffmpeg grid extractions instead of one.

**Silent-except style difference from C2/C4**: unlike `vocals._demucs_env`'s narrow `except Exception: pass` (flagged in C2 as slightly overbroad but low-risk) or `persona_directives.persona_facts`'s untraced `except Exception: store = None` (flagged in C4 as the one un-logged swallow), every broad-except in this cluster (framing.py's nine sites, stitch_render.py's four, produce.py's six) either returns a well-understood fail-open sentinel with a code comment explaining the contract, or explicitly logs via `log(...)`/`get_logger(...)` before swallowing. No un-logged silent swallow was found in C3 — this cluster is more disciplined on that axis than either C2 or C4's single flagged instance.
