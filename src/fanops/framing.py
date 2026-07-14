# src/fanops/framing.py
"""Subject-aware reframe: find WHERE the subject sits in the source frame so the 9:16 crop can follow it
instead of guessing top/center. `subject_focus` samples a few frames across a cut window (reusing the
keyframes extractor — the same EYES the hook author uses), detects the dominant face per frame via OpenCV,
and returns the MEDIAN normalized centroid (fx, fy) in [0,1]; clip.reframe_filter turns that into a crop
offset. OpenCV is an OPTIONAL extra (`pip install -e '.[framing]'`) imported LAZILY and FAIL-OPEN exactly
like fanops.compose / vocals: absent cv2, no detection, a timeout, or any error -> None, and the caller
crops centered (today's behavior). Detection is DETERMINISTIC per (source, window) so the result is cached
to a per-source sidecar, mirroring signals.detect_signals — the in-lock commit re-probes nothing."""
from __future__ import annotations
import json, os
from pathlib import Path
from statistics import median
from fanops.errors import ToolchainMissingError
from fanops.transcribe import window_has_trusted_speech as _window_has_speech

_SIDECAR_V = 5               # track-sidecar schema (v5: + min-shot-duration merge — no rapid cut-away-and-back)
_KF_COUNT = 5                # frames sampled across the window — enough for a stable median, cheap to probe
_KF_WIDTH = 960              # detection sampling width: Haar/YuNet need real pixels — a 480px face (~37px on a
                             # 1080p source) is undetectable; 960 lands a 1080p face at ~74px, reliably found.
_MIN_CONF = 0.34             # need a face in >=34% of sampled frames (>=2 of 5) — else fall back to center crop
_SCORE_THRESH = 0.6         # YuNet confidence floor (proven 6/6 detection on real interview footage at 0.6)
_MODEL = "yunet_2023mar.onnx"   # vendored YuNet face detector (opencv_zoo, 232KB) — see src/fanops/data/

def _cv2():
    """The OpenCV module, or None when the [framing] extra isn't installed (caller -> center crop)."""
    try:
        import cv2                                            # noqa: PLC0415 — lazy by design (optional extra)
        return cv2
    except Exception:
        return None

def _model_path() -> Path:
    """Path to the vendored YuNet ONNX. Shipped with the package (src/fanops/data) so detection is offline,
    deterministic, and free — no first-use download (mirrors the project's on-machine, no-API ethos)."""
    return Path(__file__).resolve().parent / "data" / _MODEL

def _detector(cv2):
    """A YuNet (CNN) face detector from the vendored model, or None when it can't be built. YuNet REPLACES
    the legacy Haar cascade, which under-detected the angled / small / two-shot faces typical of interview
    footage (proven: 0-2/6 frames vs YuNet's 6/6 on the same windows). Fail-open: any miss -> None -> the
    caller gets [] -> center crop."""
    try:
        mp = _model_path()
        if not mp.exists():
            return None                                       # model asset missing -> no detection (center crop)
        return cv2.FaceDetectorYN.create(str(mp), "", (320, 320), _SCORE_THRESH)
    except Exception:
        return None                                           # old cv2 without FaceDetectorYN, or any build error

def require_cv2(cfg) -> None:
    """HARD gate for the smart-framing render path: raise ToolchainMissingError when OpenCV (the [framing]
    extra) is absent, or present-but-too-old to build the YuNet detector — so `fanops run` refuses LOUDLY
    (cli.main -> one line + exit 2) instead of silently centre-cropping every clip while the operator believes
    subject-tracking happened. Called ONLY when cfg.smart_framing is ON (clip._resolve_framing); the OFF path
    and the hermetic unit stubs never reach it. Distinct from the fail-open _cv2()/_detector() seams, which
    stay None-returning for defence-in-depth. NEVER degrades — it refuses. (No bare except here: it must
    propagate; test_swallow_ratchet.py polices new silent handlers and this has none.)
    `cfg` is currently unused in the body but is RETAINED deliberately: callers pass a Config
    (clip._resolve_framing and the e2e tests call `require_cv2(Config(...))`), and a future per-config
    framing policy (e.g. per-account) may consult it. Documented compatibility, not vague reservation."""
    cv2 = _cv2()
    if cv2 is None:
        raise ToolchainMissingError(
            "smart framing is ON but OpenCV (cv2) is not installed — "
            "run: pip install -e '.[framing]'  (or set FANOPS_SMART_FRAMING=0 to centre-crop)")
    if _detector(cv2) is None:
        raise ToolchainMissingError(
            "smart framing is ON but the YuNet face detector could not be built "
            "(OpenCV too old, or the vendored model is missing) — "
            "reinstall the [framing] extra, or set FANOPS_SMART_FRAMING=0 to centre-crop")

def _wkey(start: float, end: float) -> str:
    return f"{round(start, 2)}-{round(end, 2)}"

def _load_cache(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        if d.get("v") != _SIDECAR_V: return {}               # stale detector version -> recompute
        w = d.get("windows")
        return w if isinstance(w, dict) else {}              # a non-dict "windows" (corrupt) -> recompute; else the caller's `key in cache` raises TypeError (breaks NEVER-raises)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}                                             # corrupt sidecar -> recompute (overwrites)

# ---- Single detection pass (T2): ONE grid of frames per (source,window), every face's normalized box +
# eye-line, persisted so classify_window / subject_focus / speaker_track / motion_saliency all read the SAME
# stats — not four ffmpeg passes. The grid sidecar is versioned independently of the focus/track sidecar. ----
_DETECT_V = 2               # bump to invalidate cached grid stats when the detection SHAPE changes
_DETECT_FPS = 4.0           # grid sampling rate: 4 frames/s is fine for ~1s active-speaker decisions, cheap in one pass

def _detect_faces(cv2, det, img_path: str) -> list[tuple[float, float, float, float, float]]:
    """Every face in one frame as (cx, cy, fh, ey, score) normalized to [0,1]: center x/y, face-box
    HEIGHT (drives zoom-to-consistent-size), EYE-LINE y (drives eyeline composition), and YuNet
    CONFIDENCE SCORE (used by _pick_dominant_face to filter phantom wall-art detections). YuNet rows are
    [x,y,w,h, rEye(4,5), lEye(6,7), nose(8,9), rMouth(10,11), lMouth(12,13), score]. Fail-open: an
    unreadable frame / missing landmark -> [] or ey=cy, never raises."""
    out: list[tuple[float, float, float, float, float]] = []
    try:
        img = cv2.imread(img_path)
        if img is None: return out
        h, w = img.shape[:2]
        if not (w and h): return out
        det.setInputSize((w, h))
        _n, faces = det.detect(img)
        if faces is None: return out
        for f in faces:
            cx = min(1.0, max(0.0, (float(f[0]) + float(f[2]) / 2) / w))
            cy = min(1.0, max(0.0, (float(f[1]) + float(f[3]) / 2) / h))
            fh = min(1.0, max(0.0, float(f[3]) / h))
            try: ey = min(1.0, max(0.0, ((float(f[5]) + float(f[7])) / 2) / h))   # eye-line from rEye/lEye y
            except (IndexError, ValueError, TypeError): ey = cy                    # no landmark -> face center
            try: sc = round(min(1.0, max(0.0, float(f[14]))), 4)                  # YuNet score at index 14
            except (IndexError, ValueError, TypeError): sc = 0.0                  # missing score -> 0 (fail-open)
            out.append((round(cx, 4), round(cy, 4), round(fh, 4), round(ey, 4), sc))
    except Exception:
        return out                                            # a single bad frame never sinks the window
    return out

def _detect_sidecar(cfg, source_id: str) -> Path:
    return cfg.agent_io / "framing" / f"{source_id}.detect.json"

def _load_detect_cache(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        if d.get("v") != _DETECT_V: return {}                 # stale detection shape -> recompute
        w = d.get("windows")
        return w if isinstance(w, dict) else {}               # a non-dict "windows" (corrupt) -> recompute; else the caller's `key in cache` raises TypeError
    except (OSError, json.JSONDecodeError, TypeError):
        return {}

def detect_window(cfg, src, *, start: float, end: float) -> dict | None:
    """ONE grid pass over [start,end) -> {fps, frames:[[ [cx,cy,fh,ey], ... per face ], ... per frame ]},
    cached per (source, window) in a `<source_id>.detect.json` sidecar. This is the SINGLE detection that
    feeds classify_window + subject_focus + speaker_track + motion_saliency. Returns None on every fail-open
    path (no [framing] extra, no detector, no frames, any error) -> callers fall back to the centered crop.
    NEVER raises.

    M2 — bracketed by a per-(framing, source_id) stage_lock so two concurrent callers don't both
    spawn the OpenCV detection pass and racingly clobber the sidecar (last-writer-wins lost work).
    The first acquirer fills the cache; the second enters the critical section, finds the cached
    window, returns. Cache is checked BEFORE the lock as a fast path (no contention on a warm
    sidecar) AND AFTER acquisition as the race-closing re-check. Atomic sidecar write (tmp +
    os.replace) so a torn write never poisons a concurrent reader."""
    if not (end > start):
        return None
    source_id = getattr(src, "id", "nosrc")
    path = _detect_sidecar(cfg, source_id)
    key = _wkey(start, end)
    # Fast path: a warm sidecar with this window cached -> return without acquiring the lock.
    cache = _load_detect_cache(path)
    if key in cache:
        return cache[key]
    cv2 = _cv2()
    if cv2 is None:
        return None
    det = _detector(cv2)
    if det is None:
        return None
    # Slow path: per-(framing, source_id) lock so the detection runs ONCE for this source. Re-check
    # the sidecar inside the lock — the first acquirer wrote it during its critical section.
    from fanops.stage_lock import stage_lock
    with stage_lock(cfg, stage="framing", key=source_id):
        cache = _load_detect_cache(path)
        if key in cache:
            return cache[key]
        from fanops import keyframes
        tmp = cfg.agent_io / "framing" / "tmp" / f"{source_id}_grid_{key}"
        # M2: thread source_id+cfg so the grid pass hits the content-addressed cache. detect_window,
        # speaker_track, and motion_saliency all call this on the same source — without the cache
        # they re-extract the same window 3× per pass; with it, the second + third callers find
        # the frames on disk and short-circuit.
        frames = keyframes.extract_frames_grid(getattr(src, "source_path", ""), start, end,
                                               fps=_DETECT_FPS, out_dir=tmp, width=_KF_WIDTH,
                                               source_id=source_id, cfg=cfg)
        stats = None
        try:
            if frames:
                stats = {"fps": _DETECT_FPS,
                         "frames": [[list(t) for t in _detect_faces(cv2, det, fp)] for fp in frames]}
        except Exception:
            stats = None                                      # fail-open by contract
        if stats is None:
            return None
        cache[key] = stats
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write — tmp + os.replace. A reader opening the JSON mid-write (race outside
            # this lock, e.g. a parallel speaker_track sidecar reader) never sees a truncated file.
            tmpf = path.with_suffix(path.suffix + ".tmp")
            tmpf.write_text(json.dumps({"v": _DETECT_V, "windows": cache}))
            os.replace(str(tmpf), str(path))
            try:
                from fanops.artifacts import stamp_stage
                rel = str(path.relative_to(cfg.agent_io))
                stamp_stage(cfg, source_id, "framing", artifact=rel, schema=_DETECT_V,
                            sha256=getattr(src, "sha256", None))
            except (OSError, ValueError): pass
        except OSError:
            return stats                                      # cache write failure just re-probes next time
        return stats

# ---- Content-type classification (T3): route each cut WINDOW to a reframe strategy. The two reliable
# signals are transcript-over-window (speech) and grid face-count; music-vs-silent is weakly separable and
# only changes ZOOM tightness (both lock the subject, never flicker), so it's derived from a clean fact
# (a demucs vocal stem) and fails safe. Active-speaker switching is gated to multi-speaker-talk ONLY. ----
CT_MULTI = "multi-speaker-talk"     # >=2 faces + speech -> active-speaker pan (the ONLY switching strategy)
CT_SINGLE = "single-speaker-talk"   # 1 face + speech -> subject lock + zoom + eyeline
CT_MUSIC = "music"                  # face + vocal/audio, no speech -> wider lock (stage/body context), no flicker
CT_SILENT = "silent"               # face, no speech, no vocal stem -> subject lock, no flicker
CT_NOPEOPLE = "no-people"          # no face -> safe center / motion-saliency follow

_PHANTOM_QUALITY_RATIO = 0.3  # min (score×fh) of a secondary face relative to the dominant to count as real

def _pick_dominant_face(faces: list) -> list | None:
    """The single most-prominent face from a list: YuNet confidence score desc, face-height (area proxy)
    desc as tie-break. Face entries are [cx,cy,fh,ey] (legacy) or [cx,cy,fh,ey,score] (current).
    Returns None for an empty list. Never raises."""
    if not faces: return None
    return max(faces, key=lambda f: (f[4] if len(f) > 4 else 0.0, f[2]))

def _face_count(stats: dict | None) -> int:
    """The MODAL number of REAL faces per sampled frame (the steady people-count), 0 when stats absent/empty.
    Phantom detections — wall art / posters whose score×fh falls below _PHANTOM_QUALITY_RATIO of the dominant
    face — are excluded so a decoy beside one real speaker doesn't force MULTI mode."""
    frames = (stats or {}).get("frames") or []
    if not frames: return 0
    def _real_n(fr):
        if not fr: return 0
        dom = _pick_dominant_face(fr)
        dom_q = (dom[4] if len(dom) > 4 else 1.0) * dom[2]
        if dom_q <= 0: return len(fr)                         # no quality info (legacy, no score) -> count all (fail-open)
        return sum(1 for f in fr if (f[4] if len(f) > 4 else 1.0) * f[2] >= dom_q * _PHANTOM_QUALITY_RATIO)
    counts = sorted(_real_n(fr) for fr in frames)
    return counts[len(counts) // 2]                           # median per-frame real-face count

def classify_window(cfg, src, *, start: float, end: float, stats: dict | None) -> str:
    """Pure routing over the cached detect stats + trusted transcript: one of the five CT_* strings. No
    ffmpeg, no cv2. faces==0 -> no-people; faces>=2 + trusted speech -> multi-speaker-talk; 1 face +
    trusted speech -> single; face + no trusted speech -> music (a demucs vocal stem present) else silent.
    Stats None -> no-people (the caller fails open to the centered crop regardless)."""
    faces = _face_count(stats)
    if faces <= 0:
        return CT_NOPEOPLE
    if _window_has_speech(src, start, end):
        return CT_MULTI if faces >= 2 else CT_SINGLE
    vocals = bool((getattr(src, "meta", None) or {}).get("vocals_isolated"))
    return CT_MUSIC if vocals else CT_SILENT

_ASD_FPS = 9.0             # per-FRAME active-speaker sampling rate (one grid pass): 9fps resolves who's talking to
                           # ~0.1s and gives mouth-motion enough samples — the 4fps grid was the "slow to recognise" lag
_ASD_HOLD_S = 0.35         # min DWELL before the committed speaker switches — anti-flicker hysteresis. 0.35s (was 0.8s,
                           # and ~4s before that) lands the cut within ~0.45s of the real turn — responsive, not laggy
_ASD_RATIO = 1.2           # the talker's mouth must out-move the other by this factor to be the instantaneous speaker
_ASD_SAME_TOL = 0.08       # two centroids within this normalized x are "the same shot" -> merge (no needless cut)
_ASD_SIDE_SPLIT = 0.5      # faces left/right of this normalized x are different speakers (the 2-shot split)
_ASD_MIN_SEG_S = 1.5       # a shot shorter than this is a brief INTERJECTION, not a turn — absorb it into its
                           # neighbour so we don't cut away-and-back (rapid cuts are themselves a kind of jitter)

def _mouth_roi(cv2, img, face):
    """A fixed-size grayscale crop of the mouth region of one YuNet face, for frame-to-frame motion. YuNet
    landmarks are [x,y,w,h, rEye(4,5), lEye(6,7), nose(8,9), rMouth(10,11), lMouth(12,13), score]; the mouth
    box spans the two corners, extended vertically so lip open/close shows. None when it can't be cropped."""
    try:
        h, w = img.shape[:2]
        mrx, mry, mlx, mly = float(face[10]), float(face[11]), float(face[12]), float(face[13])
        cx, cy = (mrx + mlx) / 2, (mry + mly) / 2
        mw = max(8.0, abs(mlx - mrx)); mh = mw * 0.8
        x0, x1 = int(cx - mw * 0.7), int(cx + mw * 0.7); y0, y1 = int(cy - mh * 0.7), int(cy + mh * 0.7)
        x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(w, x1), min(h, y1)
        if x1 - x0 < 4 or y1 - y0 < 4: return None
        return cv2.resize(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), (48, 32))
    except Exception:
        return None                                           # one unreadable frame never sinks the bin (fail-open)

def _track_sidecar(cfg, source_id: str) -> Path:
    return cfg.agent_io / "framing" / f"{source_id}.track.json"

def _track_observe(cv2, det, frames: list[str]) -> list[dict]:
    """PER FRAME of the grid, observe each 2-shot side (L/R of _ASD_SIDE_SPLIT): {side: ((fx,fy,fh,ey), motion)}
    where motion = mean abs diff of that side's mouth ROI vs its PREVIOUS frame (lip movement -> high). The
    dominant (largest box) face per side wins the frame. A frame that can't be read contributes an empty dict
    (fail-open). This is the pixel layer the pure _assemble_track reduces — kept separate so the hysteresis/
    segment logic is testable without cv2."""
    import numpy as np
    prev_roi = {"L": None, "R": None}
    obs: list[dict] = []
    for fp in frames:
        per: dict = {}
        try:
            img = cv2.imread(fp)
            if img is None: obs.append(per); continue
            h, w = img.shape[:2]
            if not (w and h): obs.append(per); continue
            det.setInputSize((w, h))
            _n, faces = det.detect(img)
            if faces is not None:
                bysd: dict = {"L": [], "R": []}
                for f in faces:
                    cx = (float(f[0]) + float(f[2]) / 2) / w
                    bysd["L" if cx < _ASD_SIDE_SPLIT else "R"].append(f)
                for side in ("L", "R"):
                    if not bysd[side]: continue
                    f = max(bysd[side], key=lambda x: (float(x[14]) if len(x) > 14 else 0.0, float(x[2]) * float(x[3])))  # score desc, area tie-break
                    cx = min(1.0, max(0.0, (float(f[0]) + float(f[2]) / 2) / w))
                    cy = min(1.0, max(0.0, (float(f[1]) + float(f[3]) / 2) / h))
                    fh = min(1.0, max(0.0, float(f[3]) / h))
                    try: ey = min(1.0, max(0.0, ((float(f[5]) + float(f[7])) / 2) / h))
                    except (IndexError, ValueError, TypeError): ey = cy
                    roi = _mouth_roi(cv2, img, f); motion = 0.0
                    if roi is not None and prev_roi[side] is not None and prev_roi[side].shape == roi.shape:
                        motion = float(np.mean(np.abs(roi.astype(int) - prev_roi[side].astype(int))))
                    if roi is not None: prev_roi[side] = roi
                    per[side] = ((round(cx, 4), round(cy, 4), round(fh, 4), round(ey, 4)), motion)
        except Exception:
            per = {}                                          # a single bad frame never sinks the window (fail-open)
        obs.append(per)
    return obs

def _pctl(vals: list[float], q: float) -> float:
    """The q-quantile (0..1) by nearest-rank on a sorted copy — robust for the tiny per-segment samples
    where statistics.quantiles is overkill. Used for per-segment FACE HEIGHT: a speaker's true face size is
    the CLEAREST full-face detection, not the median (which an intermittent pop-filter occlusion or a profile
    turn drags DOWN, the root of the '2-shot renders at random/wrong sizes' defect). Position stays median."""
    s = sorted(vals)
    if not s:
        return 0.0
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]

def _merge_brief_segments(segs):
    """Absorb any shot shorter than _ASD_MIN_SEG_S into a neighbour so a brief interjection never triggers a
    cut-away-and-back (rapid cuts read as jitter). A brief non-first shot extends the PREVIOUS shot over it; a
    brief first shot is swallowed by the next. Re-coalesces adjacent same-position shots after."""
    if not segs:
        return segs
    out: list = []
    for seg in segs:
        if out and (seg[1] - seg[0]) < _ASD_MIN_SEG_S:
            out[-1][1] = seg[1]                               # extend previous shot to cover the brief one (no cut)
        else:
            out.append(list(seg))
    if len(out) > 1 and (out[0][1] - out[0][0]) < _ASD_MIN_SEG_S:
        out[1][0] = out[0][0]; out = out[1:]                  # a brief FIRST shot -> the next shot starts at 0
    coal = [out[0]]                                            # re-coalesce same-position shots the absorb created
    for seg in out[1:]:
        if abs(seg[2] - coal[-1][2]) <= _ASD_SAME_TOL: coal[-1][1] = seg[1]
        else: coal.append(seg)
    return coal

def _assemble_track(obs: list[dict], fps: float):
    """PURE reduction of per-frame observations -> active-speaker segments [t0,t1,fx,fy,fh,ey] (relative s),
    or None when there's only one position (the static path is identical + cheaper). Per frame the louder
    mouth (by _ASD_RATIO) is the instantaneous talker; a HYSTERESIS dwell (_ASD_HOLD_S) must elapse before
    the committed speaker actually switches, so a one-frame blip never cuts. Times come from the frame index
    / fps."""
    if not obs or fps <= 0:
        return None
    hold = max(1, round(_ASD_HOLD_S * fps))
    talker = []                                               # per-frame instantaneous talker side or None
    for per in obs:
        if "L" in per and "R" in per:
            lm, rm = per["L"][1], per["R"][1]
            if lm >= _ASD_RATIO * max(rm, 1e-6): talker.append("L")
            elif rm >= _ASD_RATIO * max(lm, 1e-6): talker.append("R")
            else: talker.append(None)                          # too close to call -> hold
        elif len(per) == 1:
            talker.append(next(iter(per)))                     # only one face visible -> that's who's on screen
        else:
            talker.append(None)
    committed = []; cur = None; run_side = None; run = 0       # hysteresis commit
    for t in talker:
        if t is not None and t != cur:
            if t == run_side: run += 1
            else: run_side, run = t, 1
            if cur is None or run >= hold:
                cur, run_side, run = t, None, 0
        elif t == cur:
            run_side, run = None, 0
        committed.append(cur)
    segments = []; i = 0; n = len(committed)                   # group consecutive committed runs -> segments
    while i < n:
        s = committed[i]; j = i
        while j < n and committed[j] == s: j += 1
        if s is not None:
            vis = [obs[k][s][0] for k in range(i, j) if s in obs[k]]
            if vis:
                segments.append([round(i / fps, 2), round(j / fps, 2),
                                 round(median(v[0] for v in vis), 4), round(median(v[1] for v in vis), 4),
                                 round(_pctl([v[2] for v in vis], 0.75), 4), round(median(v[3] for v in vis), 4)])
        i = j
    if not segments:
        return None
    merged = [segments[0]]                                     # coalesce adjacent same-x segments (no needless cut)
    for seg in segments[1:]:
        if abs(seg[2] - merged[-1][2]) <= _ASD_SAME_TOL: merged[-1][1] = seg[1]
        else: merged.append(seg)
    merged = _merge_brief_segments(merged)                     # absorb interjections -> no cut-away-and-back
    if len(merged) <= 1:
        return None                                           # one position the whole clip -> static focus identical
    return [tuple(s) for s in merged]

def speaker_track(cfg, src, *, start: float, end: float, src_w: int, src_h: int):
    """Follow the ACTIVE speaker across a 2-shot: a time-ordered list of (t0,t1,fx,fy,fh,ey) segments (times
    RELATIVE to the clip start) — fx/fy the talker's centroid, fh the face height (drives per-segment zoom),
    ey the eye-line (drives composition). Returns None — the fail-open signal to use the STATIC subject_focus —
    whenever there's nothing dynamic to do: no [framing] extra, a single-camera window (one face throughout),
    one position, or any error. So a single-subject clip is byte-identical to before; only a real two-person
    2-shot gets a speaker-following cut. NEVER raises. Cached per (source, window)."""
    if not (end > start):
        return None
    path = _track_sidecar(cfg, getattr(src, "id", "nosrc"))
    cache = _load_cache(path)
    key = _wkey(start, end)
    if key in cache:
        e = cache[key]
        return [tuple(seg) for seg in e] if e else None
    cv2 = _cv2()
    result = None
    try:
        det = _detector(cv2) if cv2 is not None else None
        if det is not None:
            result = _compute_track(cv2, det, cfg, src, start, end)
    except Exception:
        result = None                                         # fail-open by contract -> static focus
    if result is None:
        return None                                           # M12: transient None is not cached (detect_window parity)
    cache[key] = result
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": _SIDECAR_V, "windows": cache}))
    except OSError:
        return result                                         # cache write failure just re-probes next time
    return result

def _compute_track(cv2, det, cfg, src, start: float, end: float):
    """speaker_track's detection body: ONE grid pass (extract_frames_grid) -> per-frame _track_observe ->
    pure _assemble_track. The active-speaker decision needs PIXELS (mouth motion), which the JSON detect
    stats can't carry, so this runs its own grid pass; only multi-speaker windows pay it. The first/last
    segment snap to [0, dur] so the render's time-expression covers the whole clip."""
    from fanops import keyframes
    source_id = getattr(src, "id", "nosrc")
    tmp = cfg.agent_io / "framing" / "tmp" / f"{source_id}_asd_{_wkey(start, end)}"
    # M2: source_id+cfg threaded so the grid pass shares the content-addressed cache with
    # detect_window and motion_saliency on the same window — one ffmpeg, not three.
    frames = keyframes.extract_frames_grid(getattr(src, "source_path", ""), start, end,
                                           fps=_ASD_FPS, out_dir=tmp, width=_KF_WIDTH,
                                           source_id=source_id, cfg=cfg)
    track = _assemble_track(_track_observe(cv2, det, frames), _ASD_FPS)
    if not track:
        return None
    dur = end - start
    snapped = [list(s) for s in track]
    snapped[0][0] = 0.0; snapped[-1][1] = round(dur, 2)       # cover the whole window for the time-expression
    return [tuple(s) for s in snapped]

def _median_face(stats: dict | None):
    """The DOMINANT face per frame (score desc, fh tie-break via _pick_dominant_face), reduced to the
    median (fx,fy,fh,ey) over the window plus detection confidence = fraction of frames with a face.
    None when no frames/faces."""
    frames = (stats or {}).get("frames") or []
    if not frames:
        return None
    picks = [_pick_dominant_face(fr) for fr in frames if fr]   # dominant face per occupied frame
    if not picks:
        return None
    conf = len(picks) / len(frames)
    return (round(median(p[0] for p in picks), 4), round(median(p[1] for p in picks), 4),
            round(median(p[2] for p in picks), 4), round(median(p[3] for p in picks), 4), conf)

def subject_focus(cfg, src, *, start: float, end: float):
    """The dominant subject as (fx, fy, fh, ey) in [0,1] across this window — centroid + face HEIGHT (for
    zoom-to-consistent-size) + eye-line (for composition) — reduced from the SINGLE detect_window grid pass
    (so no separate keyframe probe). None when smart framing can't place it (no [framing] extra, fewer than
    _MIN_CONF frames with a face, or any failure) -> the render falls back to the centered crop. NEVER raises."""
    if not (end > start):
        return None
    m = _median_face(detect_window(cfg, src, start=start, end=end))
    if m is None or m[4] < _MIN_CONF:
        return None                                           # too few detections -> fail-open to centered crop
    return (m[0], m[1], m[2], m[3])

def _saliency_centroid(cv2, frames: list[str]):
    """The normalized centroid of inter-frame CHANGE across the grid (where the motion is) — for music /
    silent / no-face windows with no subject to lock. None when there's no usable motion. Pixel layer kept
    separate so motion_saliency is testable without cv2."""
    import numpy as np
    prev = None; acc = None
    for fp in frames:
        try:
            img = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
            if img is None: continue
            a = img.astype(np.float32)
            if prev is not None and prev.shape == a.shape:
                d = np.abs(a - prev)
                acc = d if acc is None else acc + d
            prev = a
        except Exception:
            continue                                          # a bad frame never sinks the window
    if acc is None or float(acc.sum()) <= 0.0:
        return None
    h, w = acc.shape[:2]
    ys, xs = np.indices((h, w))
    total = float(acc.sum())
    fx = float((xs * acc).sum() / total) / max(1, w - 1)
    fy = float((ys * acc).sum() / total) / max(1, h - 1)
    return (round(min(1.0, max(0.0, fx)), 4), round(min(1.0, max(0.0, fy)), 4))

def _saliency_sidecar(cfg, source_id: str) -> Path:
    return cfg.agent_io / "framing" / f"{source_id}.saliency.json"

def motion_saliency(cfg, src, *, start: float, end: float):
    """For music / silent / no-people windows with NO face to lock: the centroid of inter-frame motion, so
    the crop drifts toward where the action is instead of a blind center. ONE grid pass; (fx,fy) or None
    (fail-open -> centered). CACHED per (source, window) — like detect_window/speaker_track — so the in-lock
    commit re-probes nothing and the warm-artifact skip never re-spawns ffmpeg. NEVER raises."""
    if not (end > start):
        return None
    path = _saliency_sidecar(cfg, getattr(src, "id", "nosrc"))
    cache = _load_cache(path)
    key = _wkey(start, end)
    if key in cache:
        e = cache[key]
        return tuple(e) if e else None
    cv2 = _cv2()
    if cv2 is None:
        return None                                           # extra absent -> don't cache (may install later)
    from fanops import keyframes
    source_id = getattr(src, "id", "nosrc")
    tmp = cfg.agent_io / "framing" / "tmp" / f"{source_id}_sal_{key}"
    # M2: source_id+cfg threaded so the grid pass shares the content-addressed cache with
    # detect_window and speaker_track on the same window.
    frames = keyframes.extract_frames_grid(getattr(src, "source_path", ""), start, end,
                                           fps=_ASD_FPS, out_dir=tmp, width=_KF_WIDTH,
                                           source_id=source_id, cfg=cfg)
    try:
        result = _saliency_centroid(cv2, frames) if frames else None
    except Exception:
        result = None
    if result is None:
        return None                                           # M12: transient None is not cached (detect_window parity)
    cache[key] = list(result)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": _SIDECAR_V, "windows": cache}))
    except OSError:
        return result                                         # cache write failure just re-probes next time
    return tuple(result)
