"""Render a Moment into platform-ready clips. Frame-accurate ffmpeg cut: -ss BEFORE -i
(fast seek) + -to AFTER -i (output-relative, version-stable — the v1 bug had -to before -i).
Reframe is chosen from the PROBED source dimensions so vertical/odd sources don't break.
render_aspects_for renders one clip per distinct aspect the active platforms need."""
from __future__ import annotations
import json, os, subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, MomentState, ClipState, Fmt
from fanops.ids import child_id
from fanops import overlay, framing
from fanops.log import get_logger
from fanops.errors import ToolchainMissingError
from fanops.render_fingerprint import (  # noqa: F401 — re-export for tests and legacy clipmod callers
    _REFRAME_GEOM_V,
    _fingerprint_matches,
    _render_fingerprint,
    _render_fingerprint_payload,
    fingerprint_of_payload,
    fingerprint_payload_bytes,
)
from fanops.reframe_vf import (  # noqa: F401 — re-export for tests and legacy clipmod callers
    _TARGETS,
    _SAFE_MARGIN_FRAC,
    _SMALL_FACE_FRAC,
    _ZOOM_MAX,
    _ZOOM_MAX_FAR,
    _ZOOM_MAX_TRACK,
    _EYELINE_FRAC,
    _FACE_FRAC_MUSIC,
    _FACE_FRAC_TALK,
    _GENTLE_MIN_FACE_FRAC,
    _GENTLE_ZOOM_MAX,
    _adaptive_zoom_max,
    _already_aspect,
    _ch0_for,
    _clamp,
    _crop_box,
    _focus_crop,
    _place,
    _safe_dims,
    _safe_origin,
    _segment_chain,
    _segments_filter_complex,
    _stack_filter_complex,
    _step_expr,
    _target_frac,
    _track_crop,
    _zoom_h,
    reframe_filter,
)
from fanops.clip_ffmpeg import (  # noqa: F401 — re-export for tests and legacy clipmod callers
    _FFMPEG_TIMEOUT,
    _supercut_span_entries,
    ffmpeg_clip_cmd,
    ffmpeg_segments_cmd,
    ffmpeg_stack_cmd,
    ffmpeg_supercut_cmd,
    render_reframed,
    render_supercut_reframed,
)
from fanops.window_math import (  # noqa: F401 — re-export for tests and legacy clipmod callers
    _SNAP_MAX_SHIFT_S,
    _nearest,
    _trusted_transcript,
    fit_window,
    realized_clip_seconds,
    snap_window,
)
from fanops.visual_start import (  # noqa: F401 — re-export for tests and legacy clipmod callers
    _VSTART_CANDIDATES,
    _VSTART_MAX_SHIFT_S,
    _VSTART_MIN_MOVE_S,
    _VSTART_PROBE_TIMEOUT,
    _VSTART_V,
    _probe_frame_strength,
    _vstart_candidate_times,
    pick_visual_start,
)

def _build_ass_text(led: Ledger, cfg: Config, moment_id: str, cid: str, aspect: Fmt,
                    *, clip_start: float, clip_end: float,
                    supercut_spans: list[tuple[float, float]] | None = None) -> tuple[str | None, bool]:
    """PURE derivation of this clip's burn-in .ass TEXT: everything _subtitles_vf does EXCEPT the write.
    Returns (ass_text_or_None, hook_burn_failed) — the same two values, same meanings, same order.

    Split out because `ass` is a FINGERPRINT FIELD (see _render_fingerprint). The only other way to
    derive it read-only would be to re-implement this rule inside a second module, free to drift from
    the renderer — and the fingerprint would then attest to text the renderer never produced. Same probe
    order (ffmpeg_has_textfilter BEFORE any build), same log lines, same hook_burn_failed semantics.

    READ-ONLY BY CONSTRUCTION: calls overlay.build_ass / build_supercut_ass, never overlay.write_ass."""
    m = led.moments[moment_id]
    hook = ((m.hook or "").strip() or None)
    segments: list = []                                # transcript captions never burned (hook-only since PR 994)
    if not hook and not segments:                        # no hook -> clean clip (transcript never burned)
        return None, False                               # nothing wanted -> not a failure
    if not overlay.ffmpeg_has_textfilter():
        # Text was asked for but the toolchain can't burn it. Don't block the clip — log once and
        # render plain. (One line per clip; ffmpeg_has_textfilter caches, so the probe runs once.)
        get_logger(cfg)("clip", cid, "subs_skipped",
                        reason="ffmpeg lacks the text filter — rendering without subtitles/hook")
        return None, True                                # WANTED but the toolchain can't burn it -> F9 flag
    tw, th = _TARGETS[aspect.value]
    if hook:                                             # P1 T2: fail-open legibility guard — warn once, never block
        warns = overlay.hook_legibility_warnings(hook, width=tw, height=th)
        if warns:
            get_logger(cfg)("clip", cid, "hook_legibility", warning="; ".join(warns))
    ass_text = None
    if supercut_spans:
        try:
            ass_text = overlay.build_supercut_ass(segments, spans=supercut_spans, hook=hook,
                                                    width=tw, height=th, font=cfg.subtitle_font)
        except Exception as exc:
            get_logger(cfg)("clip", cid, "supercut_subs_rebase_failed", reason=str(exc)[:180])
            ass_text = None
        if (not ass_text or not ass_text.strip()) and hook:
            ass_text = overlay.build_ass([], hook=hook, clip_start=0.0,
                                         clip_end=sum(float(e) - float(s) for s, e in supercut_spans),
                                         width=tw, height=th, font=cfg.subtitle_font)
    else:
        ass_text = overlay.build_ass(segments, hook=hook, clip_start=clip_start, clip_end=clip_end,
                                     width=tw, height=th, font=cfg.subtitle_font)
    if not ass_text or not ass_text.strip():
        return None, True                                # WANTED but produced no burnable text -> F9 flag
    return ass_text, False


def _subtitles_vf(led: Ledger, cfg: Config, moment_id: str, cid: str, aspect: Fmt,
                  *, clip_start: float, clip_end: float, supercut_spans: list[tuple[float, float]] | None = None):
    """Build the burned-on-screen-text `-vf` fragment for this clip, or return None (reframe only).
    FAIL-OPEN by contract: a clip is NEVER blocked on its text. Two independent layers:
      • the RETENTION HOOK (m.hook) — the default on-screen text, a curiosity-gap line that drives
        watch-through (NOT a transcript). Burned whenever the moment has a hook. SUPPRESSED here when
        a per-account burn_hook_only pass burns a per-surface hook, and
        burning the moment hook too would STACK two hooks on one clip.
      • the TRANSCRIPT captions — never burned (hook-only overlay since PR 994). The retention hook
        still burns regardless.
    Returns (vf_fragment_or_None, hook_burn_failed). hook_burn_failed is True when on-screen text WAS
    wanted (a hook) but could NOT be burned — ffmpeg lacks the text filter, or
    build_ass yielded empty — so render_moment flags the clip (F9) instead of shipping a fine-looking
    clip that silently lost its text. False when there was nothing to burn (clean clip) or it burned.

    BUILD -> WRITE -> vf. The derivation is _build_ass_text (pure); this adds the one side effect."""
    ass_text, hook_burn_failed = _build_ass_text(led, cfg, moment_id, cid, aspect, clip_start=clip_start,
                                                 clip_end=clip_end, supercut_spans=supercut_spans)
    if ass_text is None:
        return None, hook_burn_failed
    ass_path = cfg.clips / f"{cid}.ass"
    overlay.write_ass(ass_text, ass_path)
    return overlay.subtitles_vf(ass_path), False

def _resolve_framing(cfg: Config, src, cs: float, ce: float):
    """Pick the reframe strategy for this window: (focus, track, content_type). Classify the window once,
    then route — active-speaker TRACK only for real multi-speaker talk; subject-lock FOCUS (zoomed) for a
    single/music/silent subject; motion-SALIENCY (a no-zoom 2-tuple) for music/silent/no-people with no face;
    else centered (None,None,None). Gated entirely by cfg.smart_framing so OFF is byte-identical to today.
    When smart_framing is ON, cv2 (the [framing] extra) is REQUIRED and the YuNet detector is CONSTRUCTED
    ONCE here (framing._framing_runtime_or_raise): a missing/too-old/corrupt OpenCV or a create() failure
    raises ToolchainMissingError BEFORE any centered output, so the operator never silently ships blind-centred
    clips they believe were subject-tracked. That one detector is threaded (`_rt`) into every detection call
    below — no function re-constructs it. The per-strategy calls stay fail-open ONLY for a genuine detection
    MISS after successful construction (no face found -> centered); a broken prerequisite never reaches them."""
    if not cfg.smart_framing:
        return None, None, None
    # The routing moved to framing._resolve: SAME calls, SAME order, SAME 3-tuple — plus the diagnostics
    # (which strategy ran, which failed, and WHY) this function used to throw away. capture_failures
    # DEFAULTS TO FALSE, so every exception still propagates byte-for-byte and render_moment /
    # render_account_cut keep their handlers verbatim. A flipped default would silently convert
    # production fail-loud into fail-open; test_framing_outcomes pins it.
    return framing._resolve(cfg, src, cs, ce).as_tuple()

# M4 (impact-cut): a stitched render's validity is DURATION-checked, not size-checked — a short/empty
# container that passes "size > 0" must still fail. Probe the rendered output's duration via ffprobe;
# None on any failure (the caller treats an unprobeable stitch as invalid -> error + bare-clip fallback).
# Module-level so tests can patch it without a real ffprobe (mirrors the subprocess.run patch pattern).
def _probe_duration(path: str) -> float | None:
    from fanops.ingest import probe_dimensions          # local: avoid an import cycle at module load
    from fanops.errors import ToolchainMissingError
    try:
        _, _, dur = probe_dimensions(Path(path))
        return dur or None
    except (ToolchainMissingError, OSError, ValueError):
        return None

def _moment_top_bias(m, cfg: Config) -> bool:
    if m is not None and m.framing == "top": return True
    if m is not None and m.framing == "center": return False
    return cfg.aware_reframe

def _refuse_if_migrating(cfg: Config, clip_id: str) -> None:
    """The reframe-migration guard on the SHARED render entry points.

    While `fanops reframe --apply` holds `00_control/reframe.lock`, no other process may render: the
    migration backs up, re-renders and atomically replaces `{cid}.mp4` + `{cid}.render.json`, and a daemon
    or Studio render landing on the same clip mid-flight could overwrite a migrated clip with a centred one
    (or be overwritten itself, leaving a backup that no longer matches anything).

    It RAISES (MigrationLockHeld). It does not fail open and it does not degrade to a centred crop — both
    would produce exactly the silent, blind-centred output this whole migration exists to eliminate.
    Stopping the services is an operational gate; THIS is the invariant. Outside a migration the lockfile
    does not exist, this is one `Path.exists()`, and every existing exception semantic is unchanged.

    Imported lazily: `reframe_apply` imports this module, so a top-level import would be circular."""
    from fanops.reframe_apply import assert_render_allowed
    assert_render_allowed(cfg, clip_id)


def render_moment(led: Ledger, cfg: Config, moment_id: str, *,
                  aspect: Fmt = Fmt.r9x16, cut_window: tuple[float, float] | None = None,
                  clip_id: str | None = None, born_state: ClipState = ClipState.rendered) -> tuple[Ledger, Clip]:
    # M4 (impact-cut): when `cut_window` is given, render a STITCH — a new clip with the caller's distinct
    # `clip_id` (never the content-addressed bare cid, so it can't overwrite the bare clip — the supersede
    # rule), the peak-derived window verbatim (no band/snap/visual refine — the cut is already decided),
    # and `born_state` (stitch_draft, structurally unpostable). Its duration is validity-checked post-render.
    # The DEFAULT path (cut_window is None) is byte-identical to before. is_stitch guards every new branch.
    is_stitch = cut_window is not None
    m = led.moments[moment_id]
    src = led.sources[m.parent_id]
    cid = clip_id if is_stitch else child_id("clip", moment_id, aspect.value)  # content-addressed by aspect (bare)
    _refuse_if_migrating(cfg, cid)                          # reframe migration in flight -> raise, never race it
    cfg.clips.mkdir(parents=True, exist_ok=True)
    dst = cfg.clips / f"{cid}.mp4"
    first_frame_kind = None
    spans = list(m.segments) if (m.segments and not is_stitch) else None
    if is_stitch:
        cs, ce = float(cut_window[0]), float(cut_window[1])    # the impact-cut window, verbatim
    elif spans:
        # S3 supercut (MOL-178): bypass fit_window/snap/visual_start; absolute-span concat; postable tail.
        sc_cut_seconds = round(sum(float(e) - float(s) for s, e in spans), 3)
        span_entries, span_ct = _supercut_span_entries(cfg, src, spans)
        env_cs, env_ce = float(m.start), float(m.end)
        from fanops.stage_lock import stage_lock
        with stage_lock(cfg, stage="render", key=cid):
            extra_vf, hook_burn_failed = _subtitles_vf(led, cfg, moment_id, cid, aspect,
                                                       clip_start=env_cs, clip_end=env_ce, supercut_spans=spans)
            ass_path = cfg.clips / f"{cid}.ass"
            ass_text = ass_path.read_text(encoding="utf-8") if (extra_vf and ass_path.exists()) else ""
            fp = _render_fingerprint(src.source_path, env_cs, env_ce, aspect.value, src.width or 0, src.height or 0,
                                     ass_text, supercut_segments=spans, supercut_span_entries=span_entries)
            fp_path = cfg.clips / f"{cid}.render.json"
            if dst.exists() and dst.stat().st_size > 0 and _fingerprint_matches(fp_path, fp):
                clip = Clip(id=cid, parent_id=moment_id, state=born_state, path=str(dst), aspect=aspect,
                            first_frame_kind=None, cut_seconds=sc_cut_seconds, hook_burn_failed=hook_burn_failed)
                led.clips[cid] = clip
                led.set_moment_state(moment_id, MomentState.clipped)
                return led, clip
            sc_ok = False
            try:
                r = render_supercut_reframed(src.source_path, str(dst), spans, aspect.value,
                                             src_w=src.width or 0, src_h=src.height or 0,
                                             span_entries=span_entries, content_type=span_ct, extra_vf=extra_vf,
                                             timeout=_FFMPEG_TIMEOUT)
                sc_ok = r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
                get_logger(cfg)("clip", cid, "supercut_fail_open",
                                reason=f"{type(exc).__name__}: supercut render failed — falling back to envelope")
                r = None
            if sc_ok:
                clip = Clip(id=cid, parent_id=moment_id, state=born_state, path=str(dst), aspect=aspect,
                            first_frame_kind=None, cut_seconds=sc_cut_seconds, hook_burn_failed=hook_burn_failed)
                led.clips[cid] = clip
                led.set_moment_state(moment_id, MomentState.clipped)
                try:
                    fp_path.write_text(json.dumps({"fp": fp}))
                    try:
                        from fanops.artifacts import stamp_stage
                        stamp_stage(cfg, src.id, "clip", artifact=f"clips/{cid}.render.json", schema=1, sha256=src.sha256)
                    except (OSError, ValueError): pass
                except OSError: pass
                return led, clip
            rc = getattr(r, "returncode", "?") if r is not None else "?"
            get_logger(cfg)("clip", cid, "supercut_fail_open",
                            reason=f"supercut rc={rc} — falling back to envelope cut")
        # FAIL-OPEN: today's single-window path over the envelope (fit_window below).
        spans = None
    if not is_stitch and not spans:
        dur = src.duration or 0.0
        hi = dur if dur > 0 else float("inf")
        cs, ce = fit_window(m.start, m.end, dur, lo=0.0, hi=hi)
        # pick is the cut — no snap, no visual-start
    elif is_stitch:
        pass                                                   # cs/ce already set from cut_window
    cut_seconds = round(ce - cs, 3)                            # P1 provenance (observational; length not varied)
    # Smart framing (default-on, fail-open): the subject's normalized centroid over THIS window slides the
    # crop onto the speaker/action instead of the blind top/center guess. None (no [framing] extra / no
    # detection) -> today's centered crop. Resolved here (window final) + cached, so the in-lock commit
    # re-probes nothing — and it feeds BOTH the fingerprint and the render so a fp-match can't reuse a stale crop.
    # Content-adaptive: classify the window (multi-speaker / single / music / silent / no-people) and route to
    # the right crop — active-speaker TRACK only for real talk 2-shots, subject-lock FOCUS (zoomed) for a
    # single/music/silent subject, motion SALIENCY for no-face music/silent, else centered. content_type tunes
    # the zoom (music wider). All fail-open -> centered. Resolved here (window final) + cached so the in-lock
    # commit re-probes nothing, and it feeds BOTH the fingerprint and the render (no stale-crop reuse).
    focus, track, content_type = _resolve_framing(cfg, src, cs, ce)
    from fanops.stage_lock import stage_lock
    with stage_lock(cfg, stage="render", key=cid):
        extra_vf, hook_burn_failed = _subtitles_vf(led, cfg, moment_id, cid, aspect, clip_start=cs, clip_end=ce)
        # Phase D idempotent skip: if cid.mp4 already exists AND its fingerprint matches this exact intended
        # render (a pre-warm pass produced it), adopt it and SKIP ffmpeg — record the clip + advance the
        # moment. A changed hook/window yields a different fingerprint -> re-render (no stale clip reuse).
        ass_path = cfg.clips / f"{cid}.ass"
        ass_text = ass_path.read_text(encoding="utf-8") if (extra_vf and ass_path.exists()) else ""
        fp = _render_fingerprint(src.source_path, cs, ce, aspect.value, src.width or 0, src.height or 0,
                                 ass_text, top_bias=_moment_top_bias(m, cfg), focus=focus, track=track, content_type=content_type)
        fp_path = cfg.clips / f"{cid}.render.json"
        if dst.exists() and dst.stat().st_size > 0 and _fingerprint_matches(fp_path, fp):
            # An fp-match means a prior render of THIS exact window already passed (the fp is stamped only
            # after a successful render + a passing duration check for stitches), so adopt it without re-probing.
            clip = Clip(id=cid, parent_id=moment_id, state=born_state, path=str(dst), aspect=aspect,
                        first_frame_kind=first_frame_kind, cut_seconds=cut_seconds,
                        hook_burn_failed=hook_burn_failed)
            led.clips[cid] = clip
            if not is_stitch:                                     # a stitch never advances the moment (the bare clip owns it)
                led.set_moment_state(moment_id, MomentState.clipped)
            return led, clip
        try:
            r = render_reframed(src.source_path, str(dst), cs, ce, aspect.value,
                                src_w=src.width or 0, src_h=src.height or 0, extra_vf=extra_vf,
                                top_bias=_moment_top_bias(m, cfg), focus=focus, track=track,
                                content_type=content_type, timeout=_FFMPEG_TIMEOUT)
        except (FileNotFoundError, OSError) as e:
            # ffmpeg ABSENT from PATH (or otherwise unspawnable): subprocess.run raises BEFORE the
            # process starts, so check=False (which only suppresses a nonzero RETURNCODE) does not
            # cover it. Treat it exactly like the nonzero-rc branch — record ClipState.error and
            # leave the moment at `decided` so a re-run retries when ffmpeg returns. Otherwise the
            # raise escapes to the pipeline's per-moment quarantine, parking the moment in the
            # TERMINAL MomentState.error (never re-rendered) — a transient PATH glitch would wedge
            # it permanently, contradicting this module's fail-safe philosophy.
            clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                        aspect=aspect, error_reason=f"toolchain missing: ffmpeg ({type(e).__name__})")
            led.clips[cid] = clip
            return led, clip
        except subprocess.TimeoutExpired:
            # ffmpeg HUNG (corrupt input, stuck filesystem) and was killed at the bound. Same
            # fail-safe shape as the branches above/below: ClipState.error, moment stays `decided`
            # so a re-run retries — an unbounded hang here held the ledger flock forever.
            clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                        aspect=aspect, error_reason=f"ffmpeg timed out after {_FFMPEG_TIMEOUT:.0f}s")
            led.clips[cid] = clip
            return led, clip
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            # ffmpeg RAN and failed OR produced a 0-byte output (truncated mux at rc=0): record the clip as
            # errored (a dangling/empty path would otherwise masquerade as 'rendered' and blow up later in
            # crosspost/media-upload). The st_size>0 guard mirrors the segment-concat (:447) + warm-skip (:619)
            # checks. Leave the moment un-clipped so a re-run retries. Mirrors transcribe.py's pattern.
            clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst),
                        aspect=aspect, error_reason=f"ffmpeg rc={r.returncode} out={dst.stat().st_size if dst.exists() else 'missing'}B: {(r.stderr or '')[:180]}")
            led.clips[cid] = clip
            return led, clip
        if is_stitch:
            # Output validity is DURATION-checked, not size-checked (PRD): a short/empty container that
            # passes "size > 0" must fail. expected = cut_end - cut_start; a render outside DURATION_TOLERANCE
            # is errored (bare clip already shipped upstream — fail-open + fail-visible), no skip-stamp so a
            # re-render retries. The moment is left alone (the bare clip owns its state).
            from fanops.impact_cut import DURATION_TOLERANCE
            expected = round(ce - cs, 3)
            actual = _probe_duration(str(dst))
            if actual is None or abs(actual - expected) > DURATION_TOLERANCE:
                clip = Clip(id=cid, parent_id=moment_id, state=ClipState.error, path=str(dst), aspect=aspect,
                            error_reason=f"duration {actual} vs {expected}")
                led.clips[cid] = clip
                return led, clip
        clip = Clip(id=cid, parent_id=moment_id, state=born_state, path=str(dst), aspect=aspect,
                    first_frame_kind=first_frame_kind, cut_seconds=cut_seconds,
                    hook_burn_failed=hook_burn_failed)
        # Overwrite any prior clip at this content-addressed id (e.g. a previous error-state
        # render) so a re-render self-heals; setdefault would pin the stale clip. id is unique
        # per (moment, aspect), so the latest successful render is authoritative.
        led.clips[cid] = clip
        if not is_stitch:                                         # a stitch never advances the moment (the bare clip owns it)
            led.set_moment_state(moment_id, MomentState.clipped)
        # Stamp the render fingerprint (Phase D) so a later pass — or the in-lock commit after a lock-free
        # pre-warm — can skip re-rendering an identical clip. Best-effort: a write failure just costs a
        # re-render, never a crash. Written ONLY on success, so a failed render never leaves a skip stamp.
        try:
            fp_path.write_text(json.dumps({"fp": fp}))
            try:
                from fanops.artifacts import stamp_stage
                stamp_stage(cfg, src.id, "clip", artifact=f"clips/{cid}.render.json", schema=1, sha256=src.sha256)
            except (OSError, ValueError): pass
        except OSError:
            pass
        return led, clip

def render_aspects_for(led: Ledger, cfg: Config, moment_id: str, *,
                       aspects: set[Fmt]) -> tuple[Ledger, list[Clip]]:
    m = led.moments[moment_id]
    if led.is_suppressed(m):   # the OWNER; `m` IS led.moments[moment_id], so the old second check was self-redundant
        return led, []
    out: list[Clip] = []
    for asp in sorted(aspects, key=lambda a: a.value):
        led, clip = render_moment(led, cfg, moment_id, aspect=asp)
        out.append(clip)
    return led, out


def render_account_cut(led: Ledger, cfg: Config, moment_id: str, *, aspect: Fmt, profile: str,
                       hook: str, out_path: str, top_bias: bool = False) -> tuple[bool, float | None]:
    """M2: an override account's OWN per-account CUT. Same picked window as the shared clip (EOF
    clamp only — no length floor) and burn `hook` (top-third) in ONE ffmpeg pass, written
    ATOMICALLY to out_path. Returns (True, realized_seconds=ce-cs) on success, (False, None) FAIL-OPEN (any
    ffmpeg/parse failure) — the caller then falls back to burn_hook_only on the shared clip, so the
    Render.path file always exists (P3: the realized seconds is recorded on Render.cut_seconds). Unlike
    render_moment this writes to an ARBITRARY path with a SPECIFIC hook, mints NO Clip, and advances
    NO moment (the shared Clip owns the moment anchor — §4 of the per-account plan). Mirrors render_moment's
    window math (fit_window only) so the per-account cut is the same pick the
    shared clip uses. The hook .ass is 0-based (build_ass(clip_start=0) — the -ss output is 0-based)."""
    # OUTSIDE the try, deliberately: the broad `except Exception -> (False, None)` fail-open below would
    # otherwise SWALLOW the migration refusal and silently fall back to burn_hook_only over a clip the
    # migration is mid-way through replacing. This guard is the one thing here that must not fail open.
    _refuse_if_migrating(cfg, f"account_cut:{moment_id}")
    ass_path = None
    tmp = str(out_path) + ".part"
    try:
        m = led.moments[moment_id]
        src = led.sources[m.parent_id]
        tw, th = _TARGETS[aspect.value]
        extra_vf = None
        r = None
        if m.segments:
            spans = list(m.segments)
            realized = sum(float(e) - float(s) for s, e in spans)
            span_entries, span_ct = _supercut_span_entries(cfg, src, spans)
            if (hook or "").strip() and overlay.ffmpeg_has_textfilter():
                ass_text = overlay.build_supercut_ass([], spans=spans, hook=hook, width=tw, height=th,
                                                      font=cfg.subtitle_font)
                if ass_text and ass_text.strip():
                    ass_path = str(Path(out_path).with_suffix(".ass"))
                    overlay.write_ass(ass_text, ass_path)
                    extra_vf = overlay.subtitles_vf(ass_path)
            def _run_render():
                return render_supercut_reframed(src.source_path, tmp, spans, aspect.value,
                                                src_w=src.width or 0, src_h=src.height or 0,
                                                span_entries=span_entries, content_type=span_ct, extra_vf=extra_vf,
                                                timeout=_FFMPEG_TIMEOUT)
        else:
            dur = src.duration or 0.0
            hi = dur if dur > 0 else float("inf")
            cs, ce = fit_window(m.start, m.end, dur, lo=0.0, hi=hi)
            # pick is the cut — no snap, no visual-start
            realized = ce - cs                                    # P3: the account cut's REALIZED window length
            focus, track, content_type = _resolve_framing(cfg, src, cs, ce)   # content-adaptive crop (fail-open -> centered)
            if (hook or "").strip() and overlay.ffmpeg_has_textfilter():
                # hook-only .ass, 0-based over the cut output's first min(2.5, len) seconds (build_ass uses
                # clip_start/clip_end only for clip_len; the HOOK event is emitted at t=0 regardless).
                ass_text = overlay.build_ass([], hook=hook, clip_start=0.0, clip_end=ce - cs,
                                             width=tw, height=th, font=cfg.subtitle_font)
                if ass_text and ass_text.strip():
                    ass_path = str(Path(out_path).with_suffix(".ass"))
                    overlay.write_ass(ass_text, ass_path)
                    extra_vf = overlay.subtitles_vf(ass_path)
            def _run_render():
                return render_reframed(src.source_path, tmp, cs, ce, aspect.value,
                                       src_w=src.width or 0, src_h=src.height or 0, extra_vf=extra_vf,
                                       top_bias=top_bias, focus=focus, track=track,
                                       content_type=content_type, timeout=_FFMPEG_TIMEOUT)
        from fanops.stage_lock import stage_lock
        with stage_lock(cfg, stage="render", key=Path(out_path).stem):
            try:
                r = _run_render()
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                return False, None
            if r.returncode != 0 or not Path(tmp).exists():
                return False, None                                # ffmpeg failed -> fail-open (tmp swept in finally)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, out_path)                             # atomic publish — never a half-written per-account file
        return True, realized
    except ToolchainMissingError:
        # A BROKEN PREREQUISITE (smart_framing ON + cv2/detector unavailable) is NOT a per-variant fail-open
        # case — it must propagate LOUDLY so the render refuses instead of silently emitting a centered cut.
        # (The caller does NOT fall back to burn_hook_only on this; the pipeline halts on the missing toolchain.)
        raise
    except Exception as exc:
        get_logger(cfg)("clip", moment_id, "account_cut_failed", err=str(exc)[:120])
        return False, None                                    # fail-open by contract: a clip is never blocked on its variant
    finally:
        # sweep BOTH render artifacts on EVERY exit path (success, fail-open return, or a raise before the
        # subprocess) — the .ass is never an output, and the .part is consumed by os.replace on success (its
        # unlink then no-ops) but survives every failure. Mirrors overlay.burn_hook_only's atomic-temp finally.
        for _p in (ass_path, tmp):
            if _p:
                try: os.unlink(_p)
                except OSError: pass
