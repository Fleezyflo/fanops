"""ffmpeg command builders and atomic render shells for clip production.

Builds argv lists for single-pass crop, per-segment concat, supercut concat, and vertical
stack renders; runs subprocess with temp-file atomic publish. Geometry lives in reframe_vf."""
from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from fanops import framing
from fanops.config import Config
from fanops.reframe_vf import (
    _segments_filter_complex,
    _stack_filter_complex,
    reframe_filter,
)

# Hard bound on one ffmpeg render (the llm.py timeout idiom). render_moment runs INSIDE
# advance()'s ledger transaction, so an UNBOUNDED hang on a corrupt input held the flock against
# every other pass and Studio write. 10min covers a multi-minute 1080p re-encode with headroom.
_FFMPEG_TIMEOUT = 600.0


def ffmpeg_clip_cmd(src: str, dst: str, start: float, end: float, aspect: str,
                    *, src_w: int = 0, src_h: int = 0, extra_vf: str | None = None,
                    top_bias: bool = False, focus: tuple | None = None,
                    track: list | None = None, content_type: str | None = None) -> list[str]:
    # -ss before -i (fast seek) makes output-position -to a DURATION measured from the seek
    # point, so it must be (end - start), not the absolute end. Verified on ffmpeg 8.0.1:
    # `-ss 1.5 -to 6.5` yields a 6.5s clip; passing 8.0 here would yield 8.0s (the F39 bug).
    # extra_vf (e.g. the burned-subtitles `subtitles=...` token) is chained AFTER the reframe
    # with a comma so it operates on the already-reframed frame; default None == old behavior.
    vf = reframe_filter(aspect, src_w, src_h, top_bias=top_bias, focus=focus, track=track, content_type=content_type)
    if extra_vf:
        vf = f"{vf},{extra_vf}"
    return ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", src, "-to", f"{end - start:.3f}",
            "-vf", vf,
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]


# ---- Per-segment active-speaker render (the fix for "random sizes" in 2-shots) ----
# A single ffmpeg `crop` evaluates w/h ONCE per stream, so a 2-shot got ONE zoom for two speakers whose source
# face sizes differ >2x -> one of them was always wrong-sized (the operator's "cutting to random sizes"). The
# fix renders each active-speaker SEGMENT as its OWN correctly-sized crop and joins them with the concat filter
# in a SINGLE pass: N seeked inputs -> per-segment crop chains -> concat (sample-accurate, no container seams)
# -> optional subtitle burn -> one encode. Each speaker now lands at a consistent on-screen face size.
def ffmpeg_segments_cmd(src: str, dst: str, cs: float, ce: float, aspect_value: str, track: list,
                        *, src_w: int, src_h: int, content_type: str | None = None,
                        sub_token: str | None = None) -> list[str]:
    """ffmpeg command for the per-segment concat render: one seeked input per segment (`-ss`/`-t` before each
    `-i` = fast + accurate), a single -filter_complex (per-segment crop -> concat -> subtitles), one encode.
    Segment times are RELATIVE to the clip; each input window is (cs+t0) for (t1-t0) seconds.

    `-fps_mode cfr` is REQUIRED, not cosmetic: the concat filter offsets each joined segment's PTS by that
    segment's duration rounded UP to a whole frame interval, leaving a 1-frame gap at every join. Without a
    constant-rate resample that stretches the output ~1 frame per join — inflating the duration, dropping
    avg_frame_rate below the source (measured 29.835 vs 29.97 on a 3-segment clip), and drifting the burned
    .ass subtitles against the video (the _segments_filter_complex 'timeline aligns' claim only holds once the
    gaps are filled). cfr resamples to a continuous grid: avg_frame_rate == r_frame_rate, subtitles realign.
    This is an ffmpeg flag, NOT a fingerprint input, so it changes NO render fingerprint (no re-render churn)."""
    cmd = ["ffmpeg", "-y"]
    for seg in track:
        seg_cs = cs + float(seg[0]); seg_dur = float(seg[1]) - float(seg[0])
        cmd += ["-ss", f"{seg_cs:.3f}", "-t", f"{seg_dur:.3f}", "-i", src]
    fc = _segments_filter_complex(track, src_w, src_h, aspect_value, content_type, sub_token=sub_token)
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
            "-fps_mode", "cfr",                       # fill concat's per-join PTS gaps -> CFR, no subtitle drift
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]
    return cmd


def _supercut_span_entries(cfg: Config, src, spans: list[tuple[float, float]]):
    """Per-span framing for supercut concat inputs (one ABSOLUTE seek per span). Fail-open centered."""
    from fanops.clip import _resolve_framing  # local: clip imports us; framing resolution stays in clip
    entries = []; ct_out = None
    for s, e in spans:
        dur = float(e) - float(s)
        focus, track, ct = _resolve_framing(cfg, src, float(s), float(e))
        if ct and ct_out is None:
            ct_out = ct
        if focus is not None:
            fx, fy = focus[0], focus[1]
            fh = focus[2] if len(focus) > 2 else None
            ey = focus[3] if len(focus) > 3 else None
            fw = focus[4] if len(focus) > 4 else None
        elif track:
            fx, fy = track[0][2], track[0][3]
            fh = track[0][4] if len(track[0]) > 4 else None
            ey = track[0][5] if len(track[0]) > 5 else None
            fw = track[0][6] if len(track[0]) > 6 else None
        else:
            fx, fy, fh, ey, fw = 0.5, 0.5, None, None, None
        entries.append((0.0, dur, fx, fy, fh, ey, fw))   # E1b: carry fw so the safe-area + fingerprint see it
    return entries, ct_out


def ffmpeg_supercut_cmd(src: str, dst: str, spans: list[tuple[float, float]], aspect_value: str,
                        *, src_w: int = 0, src_h: int = 0, span_entries: list | None = None,
                        content_type: str | None = None, sub_token: str | None = None) -> list[str]:
    """S3 supercut: one ABSOLUTE seeked input per span (`-ss s -t (e-s) -i src`), each span its own
    crop chain, joined by the concat-filter STRING `_segments_filter_complex` builds. NET-NEW seek loop;
    only the concat string reuses."""
    cmd = ["ffmpeg", "-y"]
    for s, e in spans:
        cmd += ["-ss", f"{float(s):.3f}", "-t", f"{float(e) - float(s):.3f}", "-i", src]
    if span_entries is None:
        span_entries = [(0.0, float(e) - float(s), 0.5, 0.5, None, None) for s, e in spans]
    fc = _segments_filter_complex(span_entries, src_w, src_h, aspect_value, content_type, sub_token=sub_token)
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]
    return cmd


def render_supercut_reframed(src_path: str, dst: str, spans: list[tuple[float, float]], aspect_value: str, *,
                             src_w: int, src_h: int, span_entries: list | None = None,
                             content_type: str | None = None, extra_vf: str | None = None,
                             timeout: float = _FFMPEG_TIMEOUT):
    """Atomic supercut render (MOL-178). Returns subprocess result; fail-open caller falls back."""
    tmp = str(dst) + ".part.mp4"
    try:
        cmd = ffmpeg_supercut_cmd(src_path, tmp, spans, aspect_value, src_w=src_w, src_h=src_h,
                                  span_entries=span_entries, content_type=content_type, sub_token=extra_vf)
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and Path(tmp).exists() and Path(tmp).stat().st_size > 0:
            os.replace(tmp, dst)
        return r
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ---- Stable render strategy: a LOCKED-OFF camera per shot (no per-frame motion = no jitter) ----
# A per-frame crop that CHASES the subject reads as a jittery hand-held cam — it tracks every detection wobble
# and the zoom "breathes" with per-frame face-height noise (the operator's "jittery cameraman" complaint).
# Seated podcast/interview footage wants the opposite: ONE fixed, correctly-sized crop held PERFECTLY STILL per
# shot, hard-cutting between speakers — a locked-off virtual camera, how real clippers cut it. So the render is a
# STATIC crop per active-speaker SEGMENT (the ffmpeg crop is constant within a segment -> zero camera motion),
# or a single static subject-lock for a one-person clip. Per-shot sizing fixes the cross-speaker "random sizes";
# the cut timing is the responsive ASD track. No per-frame motion = no jitter, by construction.

# ---- S2 / D1-A: vertical STACK for a genuine wide two-shot (retain BOTH hosts, no empty gap) ----
# A wide two-shot's hosts sit ~0.6+ apart in x; a single upright 9:16 crop is only ~0.316 of the source width,
# so it can hold at most ONE host and the blind centre lands on the empty table between them (the D1-A defect).
# framing._resolve instead emits a subject-derived STACK (content_type=RENDER_STACK_PAIR, focus = the two host
# anchors) and this renders it: each host cropped into its OWN half of the frame and vstacked. Both hosts are
# retained and reasonably large, and the dead centre is REMOVED — spec F6 permits zoom-in only to remove dead
# space, and a gentle zoom cap (_GENTLE_ZOOM_MAX) honours the operator's minimal-zoom directive.
def ffmpeg_stack_cmd(src: str, dst: str, cs: float, ce: float, aspect_value: str, focus: tuple,
                     *, src_w: int, src_h: int, content_type: str | None = None,
                     sub_token: str | None = None) -> list[str]:
    """ffmpeg command for the vertical-stack render: ONE seeked input split into two host crops + a vstack, one
    encode. `-ss` before `-i` (fast seek) makes `-to` a DURATION (== ce-cs), mirroring ffmpeg_clip_cmd. The
    original audio is mapped through (`0:a?` — optional, so a video-only source never fails the map)."""
    fc = _stack_filter_complex(focus, src_w, src_h, aspect_value, sub_token=sub_token)
    return ["ffmpeg", "-y", "-ss", f"{cs:.3f}", "-i", src, "-to", f"{ce - cs:.3f}",
            "-filter_complex", fc, "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", dst]


def render_reframed(src_path: str, dst: str, cs: float, ce: float, aspect_value: str, *,
                    src_w: int, src_h: int, extra_vf: str | None = None, top_bias: bool = False,
                    focus: tuple | None = None, track: list | None = None,
                    content_type: str | None = None, timeout: float = _FFMPEG_TIMEOUT):
    """Render the reframed clip to `dst` as a STABLE shot (no per-frame camera motion), fail-open ladder:
      1. segment-concat (a real 2-shot track) — each speaker its OWN static, correctly-sized crop, hard cuts;
      2. single-pass ffmpeg crop (single subject / centered) — one static subject-lock (or centered) crop.
    Both are LOCKED-OFF per shot (the crop is constant within a segment) so there is zero jitter. Returns the
    subprocess result for the caller's existing handling; FileNotFoundError/OSError/TimeoutExpired propagate
    exactly like the single-pass `subprocess.run` they replace.

    ATOMIC WRITE (MOL-78): ffmpeg renders to a `<dst>.part.mp4` temp SIBLING of `dst`, and the finished
    output is `os.replace`d onto `dst` ONLY after success (rc==0 + temp exists + size>0). So `dst` is never
    a torn file mid-write — a concurrent reader (preview fallback, ffprobe, upload) sees the OLD `dst` or
    nothing, never a half-muxed byte stream; on failure/timeout `dst` is left untouched (absent, or its
    prior good file), so the caller's rc/exists/size checks on `dst` still hold verbatim (an unreplaced
    failure leaves `dst` missing, matching the existing `not dst.exists()` error branch). The temp MUST keep
    a muxer-inferable `.mp4` suffix: `ffmpeg_clip_cmd`/`ffmpeg_segments_cmd` pass no `-f mp4`, so ffmpeg
    picks the container from the OUTPUT EXTENSION alone — a bare `.part` temp fails "Error initializing the
    muxer" and produces NO file (rc!=0), so os.replace would never run and `dst` would never be created
    (the MOL-78 CI E2E failure; the unit tests missed it because they stubbed ffmpeg). This also heals
    render_account_cut, which passes its OWN `<out>.part` as `dst` here: we render to `<dst>.part.mp4`
    (muxes fine) and publish to `dst` whatever ITS extension. The temp is swept on EVERY exit path —
    success, fail-through, or a raised exception — in the finally. Mirrors render_account_cut's proven
    atomic+os.replace pattern in clip.py (and overlay.burn_hook_only)."""
    tmp = str(dst) + ".part.mp4"                              # keep a muxer-inferable .mp4 suffix (see ATOMIC WRITE)
    try:
        if content_type == framing.RENDER_STACK_PAIR and focus and len(focus) >= 10:
            # S2/D1-A: a genuine wide two-shot -> both hosts vertically stacked (no empty gap). One input,
            # two host crops, vstack, one encode. Fail-open: a working ffmpeg that rejects the stack graph
            # falls through to the centred single-pass (the acceptance floor — never worse than the blind centre).
            stack_cmd = ffmpeg_stack_cmd(src_path, tmp, cs, ce, aspect_value, focus,
                                         src_w=src_w, src_h=src_h, content_type=content_type, sub_token=extra_vf)
            r = subprocess.run(stack_cmd, check=False, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and Path(tmp).exists() and Path(tmp).stat().st_size > 0:
                os.replace(tmp, dst)                          # atomic publish — never a half-written clip at dst
                return r
            focus, track, content_type = None, None, None    # stack graph rejected -> centre (fail-open)
        if track and len(track) > 1:
            seg_cmd = ffmpeg_segments_cmd(src_path, tmp, cs, ce, aspect_value, track,
                                          src_w=src_w, src_h=src_h, content_type=content_type, sub_token=extra_vf)
            r = subprocess.run(seg_cmd, check=False, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and Path(tmp).exists() and Path(tmp).stat().st_size > 0:
                os.replace(tmp, dst)                          # atomic publish — never a half-written clip at dst
                return r
            # a working ffmpeg rejected the segment graph -> fall through to the single-pass crop (fail-open);
            # the .part is overwritten by the single-pass output below, and swept in finally on any failure.
        cmd = ffmpeg_clip_cmd(src_path, tmp, cs, ce, aspect_value, src_w=src_w, src_h=src_h, extra_vf=extra_vf,
                              top_bias=top_bias, focus=focus, track=track, content_type=content_type)
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and Path(tmp).exists() and Path(tmp).stat().st_size > 0:
            os.replace(tmp, dst)                              # atomic publish (single-pass)
        # rc!=0 / empty / missing temp -> leave dst UNTOUCHED; the caller's rc+exists+size checks on dst fire.
        return r
    finally:
        # sweep the .part on EVERY exit path (success os.replace consumes it; failure/timeout/exception leave
        # it). suppress(OSError) so a sweep hiccup never MASKS a propagating TimeoutExpired/OSError from above.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
