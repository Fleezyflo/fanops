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
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from fanops.errors import StageBusyError, ToolchainMissingError
from fanops.framing_outcomes import (HARD_FAILURE_EVENTS, NEGATIVE_RESULT_EVENTS, FramingEventType as _FE,
                                     FramingOutcome as _FO, FramingStrategy as _FS, FramingTrace,
                                     ResolverInvariantError, StrategyAttempt, StrategyState, record as _rec)
from fanops.transcribe import window_has_trusted_speech as _window_has_speech

_SIDECAR_V = 6               # track-sidecar schema (v6: + per-observation face WIDTH for the horizontal safe-area; v5: min-shot-duration merge)
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

class _FramingRuntime:
    """A per-resolution framing runtime: the imported cv2 module + a REAL, already-constructed YuNet detector.
    Built ONCE per _resolve_framing by _framing_runtime_or_raise and threaded into detect_window /
    speaker_track / subject_focus / motion_saliency so they reuse this one detector instead of each
    constructing their own. NOT a process-global: a fresh runtime per resolution, so the detector's mutable
    input-size state (det.setInputSize, reset per frame in _track_observe / _detect_faces) is only ever
    touched sequentially within a single resolution — no cross-thread sharing, no concurrency hazard."""
    __slots__ = ("cv2", "detector")
    def __init__(self, cv2, detector):
        self.cv2 = cv2
        self.detector = detector

def _framing_runtime_or_raise(cfg) -> "_FramingRuntime":
    """Construct the ONE YuNet detector for a framing resolution, or raise ToolchainMissingError LOUDLY.

    This is the smart-framing prerequisite gate AND the sole constructor for the resolution. It proves the
    detector can ACTUALLY be built — not merely that cv2 imports and the attr/file exist — so a corrupt or
    incompatible ONNX, an OpenCV ABI mismatch, or any failure inside FaceDetectorYN.create() REFUSES here,
    before a single centered frame can be produced. A broken prerequisite is NOT a detection miss.

    Called ONLY when cfg.smart_framing is ON (clip._resolve_framing); the OFF path never reaches it. NEVER
    degrades to centered — it refuses. (No bare except that swallows: the create() failure is caught only to
    RE-RAISE as ToolchainMissingError with a remediation message; test_swallow_ratchet.py has no quarrel.)"""
    cv2 = _cv2()
    if cv2 is None:
        raise ToolchainMissingError(
            "smart framing is ON but OpenCV (cv2) is not installed — "
            "run: pip install -e '.[framing]'  (or set FANOPS_SMART_FRAMING=0 to centre-crop)")
    if getattr(getattr(cv2, "FaceDetectorYN", None), "create", None) is None:
        raise ToolchainMissingError(
            "smart framing is ON but this OpenCV is too old for the YuNet face detector "
            "(no cv2.FaceDetectorYN.create) — reinstall the [framing] extra, "
            "or set FANOPS_SMART_FRAMING=0 to centre-crop")
    if not _model_path().exists():
        raise ToolchainMissingError(
            f"smart framing is ON but the vendored YuNet model is missing ({_MODEL}) — "
            "reinstall the [framing] extra, or set FANOPS_SMART_FRAMING=0 to centre-crop")
    try:
        detector = _detector(cv2)                              # the REAL construction — proves it builds
    except Exception as e:                                     # any create() error -> loud refusal, NOT centered
        raise ToolchainMissingError(
            "smart framing is ON but the YuNet face detector failed to construct "
            f"(OpenCV/model incompatible: {type(e).__name__}: {e}) — "
            "reinstall the [framing] extra, or set FANOPS_SMART_FRAMING=0 to centre-crop") from e
    if detector is None:                                       # _detector swallowed a build failure -> still refuse
        raise ToolchainMissingError(
            "smart framing is ON but the YuNet face detector could not be built "
            "(OpenCV/model incompatible, or the vendored model is unreadable) — "
            "reinstall the [framing] extra, or set FANOPS_SMART_FRAMING=0 to centre-crop")
    return _FramingRuntime(cv2, detector)

def require_cv2(cfg) -> None:
    """Thin gate wrapper: build the framing runtime and discard it, so callers that only want the
    refusal (not the detector) still get the FULL construction-backed check. The production path
    (clip._resolve_framing) uses _framing_runtime_or_raise directly and REUSES the detector, so the
    guard costs zero EXTRA construction there. NEVER degrades — it refuses."""
    _framing_runtime_or_raise(cfg)

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
_DETECT_V = 3               # bump to invalidate cached grid stats when the detection SHAPE changes (v3: + face WIDTH)
_DETECT_FPS = 4.0           # grid sampling rate: 4 frames/s is fine for ~1s active-speaker decisions, cheap in one pass

def _detect_faces(cv2, det, img_path: str) -> list[tuple[float, float, float, float, float, float]]:
    """Every face in one frame as (cx, cy, fh, ey, score, fw) normalized to [0,1]: center x/y, face-box
    HEIGHT (drives zoom-to-consistent-size), EYE-LINE y (drives eyeline composition), YuNet CONFIDENCE
    SCORE (used by _pick_dominant_face to filter phantom wall-art detections), and face-box WIDTH (drives
    the horizontal safe-area — E1). fw is APPENDED so score stays at index 4: _pick_dominant_face/_face_count
    read [4]/[2] unchanged. YuNet rows are [x,y,w,h, rEye(4,5), lEye(6,7), nose(8,9), rMouth(10,11),
    lMouth(12,13), score]. Fail-open: an unreadable frame / missing landmark -> [] or ey=cy, never raises."""
    out: list[tuple[float, float, float, float, float, float]] = []
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
            fw = min(1.0, max(0.0, float(f[2]) / w))                              # face-box WIDTH -> horizontal safe-area (E1)
            try: ey = min(1.0, max(0.0, ((float(f[5]) + float(f[7])) / 2) / h))   # eye-line from rEye/lEye y
            except (IndexError, ValueError, TypeError): ey = cy                    # no landmark -> face center
            try: sc = round(min(1.0, max(0.0, float(f[14]))), 4)                  # YuNet score at index 14
            except (IndexError, ValueError, TypeError): sc = 0.0                  # missing score -> 0 (fail-open)
            out.append((round(cx, 4), round(cy, 4), round(fh, 4), round(ey, 4), sc, round(fw, 4)))
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

def detect_window(cfg, src, *, start: float, end: float, _rt=None, _trace=None) -> dict | None:
    """ONE grid pass over [start,end) -> {fps, frames:[[ [cx,cy,fh,ey], ... per face ], ... per frame ]},
    cached per (source, window) in a `<source_id>.detect.json` sidecar. This is the SINGLE detection that
    feeds classify_window + subject_focus + speaker_track + motion_saliency. Returns None on every fail-open
    path (no [framing] extra, no detector, no frames, any error) -> callers fall back to the centered crop.
    NEVER raises.

    `_rt` (internal): a _FramingRuntime carrying the ALREADY-constructed detector. When _resolve_framing
    passes it, this function REUSES `_rt.detector` instead of building its own — so a resolution constructs
    the YuNet detector exactly once. When _rt is None (legacy/direct callers, detection-stubbed tests), the
    historical fail-open self-build path runs unchanged (cv2/detector None -> None -> centered).

    M2 — bracketed by a per-(framing, source_id) stage_lock so two concurrent callers don't both
    spawn the OpenCV detection pass and racingly clobber the sidecar (last-writer-wins lost work).
    The first acquirer fills the cache; the second enters the critical section, finds the cached
    window, returns. Cache is checked BEFORE the lock as a fast path (no contention on a warm
    sidecar) AND AFTER acquisition as the race-closing re-check. Atomic sidecar write (tmp +
    os.replace) so a torn write never poisons a concurrent reader.

    `_trace` (internal, framing_outcomes.FramingTrace): records WHY this returned None — an invalid
    window, an absent ffmpeg, a failed encode, an empty glob, a detector blowup. Today all of those
    are one indistinguishable `None`, which classify_window then turns into CT_NOPEOPLE. Byte-identical
    when `_trace is None` (the production path)."""
    if not (end > start):
        _rec(_trace, _FE.INVALID_WINDOW, window_s=round(end - start, 3))
        return None
    source_id = getattr(src, "id", "nosrc")
    path = _detect_sidecar(cfg, source_id)
    key = _wkey(start, end)
    # Fast path: a warm sidecar with this window cached -> return without acquiring the lock.
    cache = _load_detect_cache(path)
    if key in cache:
        return cache[key]
    if _rt is not None:                                        # reuse the resolution's one constructed detector
        cv2, det = _rt.cv2, _rt.detector
    else:
        cv2 = _cv2()
        if cv2 is None:
            _rec(_trace, _FE.CV2_UNAVAILABLE)                  # ‡ legacy path: production already raised
            return None
        det = _detector(cv2)
        if det is None:
            # _detector swallows BOTH causes behind one None. Separate them rather than emit one vague event.
            _rec(_trace, _FE.DETECTOR_INIT_FAILED if _model_path().exists() else _FE.MODEL_MISSING)
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
                                               source_id=source_id, cfg=cfg, _trace=_trace)
        stats = None
        try:
            if frames:
                stats = {"fps": _DETECT_FPS,
                         "frames": [[list(t) for t in _detect_faces(cv2, det, fp)] for fp in frames]}
        except Exception as exc:
            _rec(_trace, _FE.DETECTOR_RUNTIME_FAILED, exc_type=type(exc).__name__)
            stats = None                                      # fail-open by contract
        if stats is None:
            return None                                       # frames==[] -> keyframes ALREADY recorded which one
        _rec(_trace, _FE.FACES_DETECTED, frames=len(frames), fps=_DETECT_FPS,
             faces=sum(len(fr) for fr in stats["frames"]))
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
_CLUSTER_MIN_FH = 0.06        # a face smaller than this is background/wall speck, not a seated host (E2 recall)

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

def _two_cluster(stats: dict | None) -> bool:
    """E2 multi-person RECALL: True when two DISTINCT left/right face clusters persist across the sampled
    frames — a real two-shot even when each host is only INTERMITTENTLY the dominant face (the undercount
    the median-based _face_count suffers: a turned/distant 2nd host drops below the relative phantom gate in
    ≥half the frames -> median 1 -> CT_SINGLE, cropping the other speaker out). A cluster on each side of the
    split (dead zone ± _ASD_SAME_TOL to reject a single near-centre face whose cx jitters across 0.5) must be
    (a) STRUCTURALLY present — a face ≥ _CLUSTER_MIN_FH in ≥ K occupied frames, with ≥1 frame where BOTH sides
    co-occur (two SIMULTANEOUS faces, not one crossing the frame over time) — AND (b) a GENUINE face at its
    PEAK: its best (score×fh) over the window must clear the phantom-quality gate relative to the window's
    dominant. (b) is the RELAXATION of the phantom gate the recall needs (a real host clears it in even ONE
    camera-facing frame, not the ≥half-frames the median demanded) WITHOUT admitting wall-art: a persistent
    low-score/tiny decoy beside one speaker never peaks above the gate, so it stays a single-speaker phantom
    (CT_SINGLE). Deterministic for a fixed window (no sampling, no randomness) — the stability the
    requalification pins."""
    frames = (stats or {}).get("frames") or []
    occ = [fr for fr in frames if fr]
    if len(occ) < 2:
        return False
    def _q(f):
        return (f[4] if len(f) > 4 else 1.0) * f[2]           # score×fh (legacy no-score -> area only), like _face_count
    dom_q = max((_q(f) for fr in occ for f in fr), default=0.0)
    if dom_q <= 0:
        return False
    lo, hi = _ASD_SIDE_SPLIT - _ASD_SAME_TOL, _ASD_SIDE_SPLIT + _ASD_SAME_TOL
    nL = nR = both = 0
    peak_l = peak_r = 0.0
    for fr in occ:
        lf = [f for f in fr if f[0] < lo and f[2] >= _CLUSTER_MIN_FH]
        rf = [f for f in fr if f[0] > hi and f[2] >= _CLUSTER_MIN_FH]
        nL += bool(lf); nR += bool(rf); both += (bool(lf) and bool(rf))
        if lf: peak_l = max(peak_l, max(_q(f) for f in lf))
        if rf: peak_r = max(peak_r, max(_q(f) for f in rf))
    k = max(2, round(_MIN_CONF * len(occ)))
    gate = _PHANTOM_QUALITY_RATIO * dom_q
    real_l = nL >= k and peak_l >= gate                       # present AND a genuine face at its peak (not wall-art)
    real_r = nR >= k and peak_r >= gate
    return real_l and real_r and both >= 1

def classify_window(cfg, src, *, start: float, end: float, stats: dict | None, _trace=None) -> str:
    """Pure routing over the cached detect stats + trusted transcript: one of the five CT_* strings. No
    ffmpeg, no cv2. faces==0 -> no-people; faces>=2 + trusted speech -> multi-speaker-talk; 1 face +
    trusted speech -> single; face + no trusted speech -> music (a demucs vocal stem present) else silent.

    Stats None -> no-people. THIS IS THE THIRD ERASURE: a detection that FAILED (absent ffmpeg, a dead
    encode, a detector blowup) arrives here as `None` and gets MANUFACTURED into CT_NOPEOPLE — an
    affirmative claim that the room was empty, which nothing observed. The return value is UNCHANGED
    (production depends on it), but framing._resolve now refuses to TRUST an outcome the detection phase
    failed into: it pins final_outcome=UNRESOLVED with the real root_cause, so an empty room and a broken
    toolchain stop being the same answer."""
    faces = _face_count(stats)
    two = _two_cluster(stats)                                  # E2: recall a persistent L/R two-shot the median misses
    if faces <= 0 and not two:
        _rec(_trace, _FE.NO_PEOPLE, faces=0)
        return CT_NOPEOPLE
    if _window_has_speech(src, start, end):
        return CT_MULTI if (faces >= 2 or two) else CT_SINGLE
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
    """PER FRAME of the grid, observe each 2-shot side (L/R of _ASD_SIDE_SPLIT): {side: ((fx,fy,fh,ey,fw), motion)}
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
                    fw = min(1.0, max(0.0, float(f[2]) / w))                       # face-box WIDTH -> horizontal safe-area (E1)
                    try: ey = min(1.0, max(0.0, ((float(f[5]) + float(f[7])) / 2) / h))
                    except (IndexError, ValueError, TypeError): ey = cy
                    roi = _mouth_roi(cv2, img, f); motion = 0.0
                    if roi is not None and prev_roi[side] is not None and prev_roi[side].shape == roi.shape:
                        motion = float(np.mean(np.abs(roi.astype(int) - prev_roi[side].astype(int))))
                    if roi is not None: prev_roi[side] = roi
                    per[side] = ((round(cx, 4), round(cy, 4), round(fh, 4), round(ey, 4), round(fw, 4)), motion)
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
    """PURE reduction of per-frame observations -> active-speaker segments [t0,t1,fx,fy,fh,ey(,fw)] (relative s;
    fw appended per E1b when the observations carry a face WIDTH, for the horizontal safe-area),
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
                seg = [round(i / fps, 2), round(j / fps, 2),
                       round(median(v[0] for v in vis), 4), round(median(v[1] for v in vis), 4),
                       round(_pctl([v[2] for v in vis], 0.75), 4), round(median(v[3] for v in vis), 4)]
                if all(len(v) > 4 for v in vis):              # E1b: per-segment face WIDTH (median), for the safe-area
                    seg.append(round(median(v[4] for v in vis), 4))
                segments.append(seg)
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

def speaker_track(cfg, src, *, start: float, end: float, src_w: int, src_h: int, _rt=None, _trace=None):
    """Follow the ACTIVE speaker across a 2-shot: a time-ordered list of (t0,t1,fx,fy,fh,ey(,fw)) segments (times
    RELATIVE to the clip start) — fx/fy the talker's centroid, fh the face height (drives per-segment zoom),
    ey the eye-line (drives composition), fw the face width (E1b — the horizontal safe-area, when observed).
    Returns None — the fail-open signal to use the STATIC subject_focus —
    whenever there's nothing dynamic to do: no [framing] extra, a single-camera window (one face throughout),
    one position, or any error. So a single-subject clip is byte-identical to before; only a real two-person
    2-shot gets a speaker-following cut. Cached per (source, window).

    NOT "never raises" in the strict sense — the try below catches everything _compute_track throws
    (including the stage_lock / mkdir OSErrors that reach it through extract_frames_grid) and returns
    None NORMALLY. That is exactly why COMPLETION CANNOT BE INFERRED FROM A NORMAL RETURN: this
    function returns the same None after a hard failure as it does after concluding "no 2-shot here".
    `_trace` is what separates them.
    `_rt` (internal): reuse the resolution's ALREADY-constructed detector instead of building another."""
    if not (end > start):
        _rec(_trace, _FE.INVALID_WINDOW, window_s=round(end - start, 3))
        return None
    path = _track_sidecar(cfg, getattr(src, "id", "nosrc"))
    cache = _load_cache(path)
    key = _wkey(start, end)
    if key in cache:
        e = cache[key]
        if not e:
            _rec(_trace, _FE.NO_TRACK, segments=0)            # a CACHED conclusion is still a conclusion
            return None
        _rec(_trace, _FE.TRACK_ASSEMBLED, segments=len(e))
        return [tuple(seg) for seg in e]
    result = None
    hard = False                                              # did a HARD failure happen, or did we conclude?
    try:
        if _rt is not None:                                   # reuse the resolution's one constructed detector
            cv2, det = _rt.cv2, _rt.detector
        else:
            cv2 = _cv2()
            det = _detector(cv2) if cv2 is not None else None
        if det is None:                                       # ‡ legacy _rt=None path: production already raised
            _rec(_trace, _FE.CV2_UNAVAILABLE if cv2 is None else _FE.DETECTOR_INIT_FAILED)
            hard = True
        else:
            result = _compute_track(cv2, det, cfg, src, start, end, _trace=_trace)
    except Exception as exc:
        # ONE broad handler on purpose: the swallow ratchet counts them per file, and splitting this
        # into two typed handlers would add one. StageBusyError is a producer lock we could not take —
        # an actionable, distinct cause, never a generic strategy blowup.
        _rec(_trace, _FE.STAGE_LOCK_BUSY if isinstance(exc, StageBusyError) else _FE.STRATEGY_RAISED,
             exc_type=type(exc).__name__)
        result = None; hard = True                            # fail-open by contract -> static focus
    if result is None:
        if not hard:
            _rec(_trace, _FE.NO_TRACK, segments=0)            # frames + faces, but no real 2-shot: a CONCLUSION
        return None                                           # M12: transient None is not cached (detect_window parity)
    _rec(_trace, _FE.TRACK_ASSEMBLED, segments=len(result))
    cache[key] = result
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": _SIDECAR_V, "windows": cache}))
    except OSError:
        return result                                         # cache write failure just re-probes next time
    return result

def _compute_track(cv2, det, cfg, src, start: float, end: float, _trace=None):
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
                                           source_id=source_id, cfg=cfg, _trace=_trace)
    track = _assemble_track(_track_observe(cv2, det, frames), _ASD_FPS)
    if not track:
        return None
    dur = end - start
    snapped = [list(s) for s in track]
    snapped[0][0] = 0.0; snapped[-1][1] = round(dur, 2)       # cover the whole window for the time-expression
    return [tuple(s) for s in snapped]

def _median_face(stats: dict | None):
    """The DOMINANT face per frame (score desc, fh tie-break via _pick_dominant_face), reduced to the
    median (fx,fy,fh,ey) over the window plus detection confidence = fraction of frames with a face, plus
    the median face-box WIDTH fw (E1b — drives the horizontal safe-area) when the stats carry it. fw is
    None for legacy 4-tuple stats (no width), so a caller can still fall back to today's behaviour.
    None when no frames/faces."""
    frames = (stats or {}).get("frames") or []
    if not frames:
        return None
    picks = [_pick_dominant_face(fr) for fr in frames if fr]   # dominant face per occupied frame
    if not picks:
        return None
    conf = len(picks) / len(frames)
    fws = [p[5] for p in picks if len(p) > 5]                  # E1b: face WIDTH, only when the 6-tuple carries it
    fw = round(median(fws), 4) if len(fws) == len(picks) else None
    return (round(median(p[0] for p in picks), 4), round(median(p[1] for p in picks), 4),
            round(median(p[2] for p in picks), 4), round(median(p[3] for p in picks), 4), conf, fw)

def subject_focus(cfg, src, *, start: float, end: float, _rt=None, _trace=None):
    """The dominant subject as (fx, fy, fh, ey, fw) in [0,1] across this window — centroid + face HEIGHT (for
    zoom-to-consistent-size) + eye-line (for composition) + face WIDTH (E1b — for the horizontal safe-area) —
    reduced from the SINGLE detect_window grid pass (so no separate keyframe probe). fw is None when the
    detect stats are the legacy 4-tuple shape (the clip geometry then falls back to today's centering).
    None when smart framing can't place it (fewer than _MIN_CONF frames with a face) -> the render falls
    back to the centered crop.

    This function has NO try/except and RE-ENTERS detect_window, so a stage_lock StageBusyError or an
    mkdir OSError raised down there PROPAGATES straight out of it — the "NEVER raises" this docstring
    used to claim was false. Normally the inner call is a warm-cache hit and re-probes nothing, but when
    the sidecar write failed, detect_window returns stats WITHOUT caching, so it really does re-probe.
    framing._resolve wraps the call in an _AttemptSpan, which records the escape and re-raises it unless
    the caller asked to capture — production propagation is therefore unchanged.
    `_rt` (internal): reuse the resolution's constructed detector via detect_window (one construction)."""
    if not (end > start):
        _rec(_trace, _FE.INVALID_WINDOW, window_s=round(end - start, 3))
        return None
    m = _median_face(detect_window(cfg, src, start=start, end=end, _rt=_rt, _trace=_trace))
    if m is None or m[4] < _MIN_CONF:
        _rec(_trace, _FE.NO_FACE, conf=(m[4] if m is not None else 0.0))
        return None                                           # too few detections -> fail-open to centered crop
    _rec(_trace, _FE.FOCUS_PLACED, conf=m[4])
    return (m[0], m[1], m[2], m[3], m[5])                     # (fx,fy,fh,ey,fw); m[4] is conf, m[5] is the width

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

def motion_saliency(cfg, src, *, start: float, end: float, _rt=None, _trace=None):
    """For music / silent / no-people windows with NO face to lock: the centroid of inter-frame motion, so
    the crop drifts toward where the action is instead of a blind center. ONE grid pass; (fx,fy) or None
    (fail-open -> centered). CACHED per (source, window) — like detect_window/speaker_track — so the in-lock
    commit re-probes nothing and the warm-artifact skip never re-spawns ffmpeg.
    `_rt` (internal): reuse the resolution's cv2 module (saliency needs cv2 for pixel diffs, not the detector).

    Like subject_focus, "NEVER raises" was false: extract_frames_grid below sits OUTSIDE the try, so a
    stage_lock / mkdir OSError propagates. Left exactly as-is; the _AttemptSpan records and re-raises it.

    The returned focus is a bare 2-TUPLE (fx, fy) — no face height, so nothing to size a zoom to. That is
    why _resolve returns content_type=None on this branch and MUST KEEP DOING SO (see _resolve)."""
    if not (end > start):
        _rec(_trace, _FE.INVALID_WINDOW, window_s=round(end - start, 3))
        return None
    path = _saliency_sidecar(cfg, getattr(src, "id", "nosrc"))
    cache = _load_cache(path)
    key = _wkey(start, end)
    if key in cache:
        e = cache[key]
        if not e:
            _rec(_trace, _FE.NO_MOTION)                       # a CACHED conclusion is still a conclusion
            return None
        _rec(_trace, _FE.MOTION_PLACED)
        return tuple(e)
    cv2 = _rt.cv2 if _rt is not None else _cv2()
    if cv2 is None:
        _rec(_trace, _FE.CV2_UNAVAILABLE)                     # ‡ legacy path: production already raised
        return None                                           # extra absent -> don't cache (may install later)
    from fanops import keyframes
    source_id = getattr(src, "id", "nosrc")
    tmp = cfg.agent_io / "framing" / "tmp" / f"{source_id}_sal_{key}"
    # M2: source_id+cfg threaded so the grid pass shares the content-addressed cache with
    # detect_window and speaker_track on the same window.
    frames = keyframes.extract_frames_grid(getattr(src, "source_path", ""), start, end,
                                           fps=_ASD_FPS, out_dir=tmp, width=_KF_WIDTH,
                                           source_id=source_id, cfg=cfg, _trace=_trace)
    hard = False                                              # a blowup, or a real "no motion here"?
    try:
        result = _saliency_centroid(cv2, frames) if frames else None
    except Exception as exc:
        _rec(_trace, _FE.DETECTOR_RUNTIME_FAILED, exc_type=type(exc).__name__)
        result = None; hard = True
    if result is None:
        if frames and not hard:
            _rec(_trace, _FE.NO_MOTION, frames=len(frames))   # frames extracted; the pixel layer found nothing
        return None                                           # no frames -> keyframes ALREADY recorded which failure
    _rec(_trace, _FE.MOTION_PLACED, frames=len(frames))
    cache[key] = list(result)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": _SIDECAR_V, "windows": cache}))
    except OSError:
        return result                                         # cache write failure just re-probes next time
    return tuple(result)


# ---- The resolver: the routing clip._resolve_framing used to own, now instrumented -------------------
# This is the whole point of the module. The routing is UNCHANGED — same calls, same order, same
# arguments, same 3-tuple, same exceptions — but every branch now says WHY it went the way it did.

@dataclass(frozen=True)
class FramingResolution:
    """What the resolver decided AND why.

    `as_tuple()` is the exact `(focus, track, content_type)` clip._resolve_framing has always returned.
    Production reads ONLY that. Everything else is diagnostics, and diagnostics authorize nothing."""
    focus: tuple | None
    track: list | None
    content_type: str | None                       # the RETURNED ct — None on the saliency branch (D9/C-2)
    final_outcome: _FO
    final_strategy: _FS
    root_cause: _FE | None                         # set iff final_outcome is UNRESOLVED
    classified_content_type: str | None            # what classify_window said — DIAGNOSTIC, not the returned ct
    attempts: tuple
    events: tuple
    degraded_strategies: tuple

    def as_tuple(self):
        return self.focus, self.track, self.content_type

    def to_json(self) -> dict:
        return {"final_outcome": self.final_outcome.value, "final_strategy": self.final_strategy.value,
                "root_cause": (self.root_cause.value if self.root_cause else None),
                "classified_content_type": self.classified_content_type,
                "attempts": [a.to_json() for a in self.attempts],
                "events": [e.to_json() for e in self.events],
                "degraded_strategies": [s.value for s in self.degraded_strategies]}


class _AttemptSpan:
    """PRIVATE mutable builder for ONE strategy attempt. Never leaves _resolve; never serialized.

    Execution is mutable, evidence is immutable, and they are DIFFERENT OBJECTS: there is no
    partially-built StrategyAttempt, because only `finalize()` mints one and it mints it whole."""
    __slots__ = ("_trace", "_strategy", "_applicable", "_required", "_started", "_result", "_closed")

    def __init__(self, trace: FramingTrace, strategy: _FS, *, applicable: bool, required_for_center: bool):
        self._trace = trace; self._strategy = strategy
        self._applicable = applicable; self._required = required_for_center
        self._started = False; self._result = None; self._closed = False

    def __enter__(self):
        self._started = True
        self._trace.open_span(self._strategy)                 # events now attribute to THIS strategy
        return self

    def __exit__(self, exc_type, exc, tb):
        self._trace.close_span(self._strategy)
        return False                                          # NEVER suppress — _resolve decides

    @property
    def result(self):
        return self._result

    def set_result(self, value) -> None:
        """The strategy's return value, verbatim. Called once, on a normal return."""
        if self._closed: raise ResolverInvariantError("set_result after finalize()")
        self._result = value

    def set_raised(self, exc: BaseException) -> None:
        """The strategy ESCAPED. Must be called while the span is still open, or the event would
        attribute to the detection phase — naming a phase that did not fail."""
        if self._closed: raise ResolverInvariantError("set_raised after finalize()")
        self._trace.record(_FE.STAGE_LOCK_BUSY if isinstance(exc, StageBusyError) else _FE.STRATEGY_RAISED,
                           exc_type=type(exc).__name__)

    def finalize(self) -> StrategyAttempt:
        """ATOMIC. One invariant-valid frozen record. Not re-runnable.

        Completion is decided by the EVIDENCE, never by the fact of returning: the strategies fail open,
        so they return None NORMALLY after a hard failure. A hard failure OUTRANKS a conclusive negative
        even when both were recorded during the same call."""
        if self._closed: raise ResolverInvariantError("finalize() is not re-runnable")
        self._closed = True
        seen = [e.event for e in self._trace.events_for(self._strategy)]
        hard = [e for e in seen if e in HARD_FAILURE_EVENTS]
        negs = [e for e in seen if e in NEGATIVE_RESULT_EVENTS]
        common = dict(strategy=self._strategy, applicable=self._applicable,
                      required_for_center=self._required, started=self._started)
        if hard:                                              # 1. a hard failure outranks EVERYTHING
            return StrategyAttempt(**common, completed=False, failure_event=hard[0],
                                   negative_result=None, produced_focus=False)
        if bool(self._result):                                # 2. a trusted focus / track came back
            return StrategyAttempt(**common, completed=True, failure_event=None,
                                   negative_result=None, produced_focus=True)
        if negs:                                              # 3. a conclusive negative: it RAN and found nothing
            return StrategyAttempt(**common, completed=True, failure_event=None,
                                   negative_result=negs[-1], produced_focus=False)
        return StrategyAttempt(**common, completed=False, failure_event=_FE.UNKNOWN,   # 4. unattributed:
                               negative_result=None, produced_focus=False)             #    NEVER benign


# Applicability, derived from the routing below. Every strategy the routing includes is also REQUIRED
# for a defensible centre — no optional strategy exists today. The two stay distinct because they MEAN
# different things: a future optional strategy must consciously set required_for_center=False.
_ROUTE: dict = {
    CT_MULTI:    (_FS.SPEAKER_TRACK,),                    # E3: a failed track CENTRES (never a 1-person lock)
    CT_SINGLE:   (_FS.SUBJECT_FOCUS,),
    CT_MUSIC:    (_FS.SUBJECT_FOCUS, _FS.MOTION_SALIENCY),
    CT_SILENT:   (_FS.SUBJECT_FOCUS, _FS.MOTION_SALIENCY),
    CT_NOPEOPLE: (_FS.MOTION_SALIENCY,),
}


def _resolve(cfg, src, cs: float, ce: float, *, _trace=None, capture_failures: bool = False) -> FramingResolution:
    """Route this window to a reframe strategy, and record WHY.

    THE EXCEPTION CONTRACT (the load-bearing rule). This function must let escape EXACTLY what
    clip._resolve_framing lets escape today:

      * `capture_failures=False` (THE DEFAULT — the production path). Every exception propagates
        byte-for-byte as before. render_moment (no handler) and render_account_cut
        (ToolchainMissingError -> raise; other Exception -> fail-open) keep their handlers verbatim.
        A flipped default would silently convert production fail-loud into fail-open.
      * `capture_failures=True` (the read-only dry-run). A strategy exception becomes STRATEGY_RAISED /
        STAGE_LOCK_BUSY and the clip becomes UNRESOLVED, so one bad clip cannot abort a corpus scan.

    THE PREFLIGHT IS CARVED OUT. `_framing_runtime_or_raise` runs before any span opens and is NEVER
    captured: a dry-run without a detector is meaningless, so ToolchainMissingError stays FATAL IN BOTH
    MODES. `capture_failures` governs per-strategy and detection-phase exceptions only.

    THE DETECTION PHASE IS A HARD TRUST GATE. When it fails, `ct` was MANUFACTURED (a failed detection
    yields CT_NOPEOPLE), so no downstream result can be trusted — not a centre, and not a focus a
    strategy happened to place without knowing whether a subject exists. We therefore PIN
    final_outcome=UNRESOLVED with the real root_cause.

    But we DO NOT short-circuit the routing to do it. The strategies still run and the 3-tuple is still
    returned VERBATIM, because that tuple feeds clip._render_fingerprint: returning a centred tuple
    where legacy returned a saliency focus would change the fingerprint of every affected clip and make
    the daemon RE-RENDER it on the next pass. This is an evidence-gathering change; it mutates nothing.
    The trust gate lives in the OUTCOME, never in the tuple."""
    trace = _trace if _trace is not None else FramingTrace()
    rt = _framing_runtime_or_raise(cfg)      # PREFLIGHT: constructs the ONE detector, or REFUSES. Fatal in BOTH modes.

    # ---- detection phase: the ONLY unscoped context. No span is open; its events carry strategy=None. ----
    try:
        stats = detect_window(cfg, src, start=cs, end=ce, _rt=rt, _trace=trace)
        ct = classify_window(cfg, src, start=cs, end=ce, stats=stats, _trace=trace)
    except ToolchainMissingError:
        raise                                                 # a broken prerequisite is never captured
    except StageBusyError as exc:
        trace.record(_FE.STAGE_LOCK_BUSY, exc_type=type(exc).__name__)
        if not capture_failures: raise                        # production: propagation UNCHANGED
        return _unresolved(trace, _FE.STAGE_LOCK_BUSY, (), None)
    except Exception as exc:
        # A DETECTION-phase escape, before any strategy started. It gets its OWN event: reusing
        # STRATEGY_RAISED here would name a strategy that never ran — a fabricated attribution.
        trace.record(_FE.DETECTION_RAISED, exc_type=type(exc).__name__)
        if not capture_failures: raise                        # production: propagation UNCHANGED
        return _unresolved(trace, _FE.DETECTION_RAISED, (), None)

    detection_failure = trace.detection_hard_failure()
    if detection_failure is None and stats is None:
        trace.record(_FE.UNKNOWN)                             # a None nobody attributed -> never read as benign
        detection_failure = _FE.UNKNOWN

    attempts: list = []
    ran: set = set()

    def _run(strategy: _FS, fn):
        ran.add(strategy)
        span = _AttemptSpan(trace, strategy, applicable=True, required_for_center=True)
        with span:                                            # __exit__ closes the span on EVERY path
            try:
                span.set_result(fn())
            except ToolchainMissingError:
                raise                                         # never captured, in either mode
            except Exception as exc:
                span.set_raised(exc)                          # evidence FIRST, while the span is still open
                if not capture_failures:
                    raise                                     # production: propagation UNCHANGED
        attempts.append(span.finalize())
        return span.result

    # ---- the routing, byte-for-byte the legacy control flow (clip._resolve_framing:663-676) ----
    focus = None; track = None; out_ct = None
    strategy = _FS.CENTERED; outcome = None
    ct_eff = ct

    if ct_eff == CT_MULTI and subject_aware_fallback(stats).kind == FB_PIP:
        # S4/D2 (spec F4, AC-D1): a presenter-dominant PIP layout is a UI grid — one large presenter plus a
        # column of small, inert remote tiles. Inert tiles are not co-speakers to cut between, so this must
        # not enter the active-speaker path AT ALL: speaker_track never runs (that is the routing fix; a
        # conclusive no-track afterwards would be the same centre for the wrong reason).
        # COMPOSITION IS S5's SLICE. focus/track/content_type stay None here, so the render and the
        # fingerprint are byte-identical and NOTHING re-renders — this slice only makes the layout, and the
        # decision not to chase speakers across it, observable. The dry-run keeps classifying these clips
        # FRAMING_UNRESOLVED (reframe.py:367 wants CENTERED_NO_SUBJECT), which stays true: recognised, not
        # yet composed.
        strategy, outcome = _FS.PIP_LAYOUT, _FO.CENTERED_PIP_LAYOUT
    elif ct_eff == CT_MULTI:
        track = _run(_FS.SPEAKER_TRACK, lambda: speaker_track(cfg, src, start=cs, end=ce,
                                                              src_w=src.width or 0, src_h=src.height or 0,
                                                              _rt=rt, _trace=trace))
        if track:
            out_ct, strategy, outcome = ct_eff, _FS.SPEAKER_TRACK, _FO.DETECTED_MULTI
        else:
            track = None                                      # a falsy track is not a track
            # E3: classified MULTI (>=2 speakers likely) but NO clean active-speaker track. A one-person
            # static lock would crop the other speaker out (the pilot's "empty seat"); fall back to the blind
            # CENTRE-CROP (both seats) — the acceptance floor, never a regression. subject_focus is NOT run.
            # Only a COMPLETED conclusion (a real "no 2-shot") earns the conservative centre; a HARD-FAILED
            # track leaves outcome unset so the terminal logic pins UNRESOLVED (a broken toolchain is not a
            # defensible centre). In production a hard failure has already RAISED out of _run, so this branch
            # only ever sees the conclusive-negative case there.
            if attempts and attempts[-1].state is StrategyState.COMPLETED:
                # S2/D1-A: a CONCLUSIVE no-track. Is this a genuine wide TWO-SHOT (both hosts persistent)? If
                # so, compose BOTH into a vertical STACK (subject-derived, no empty gap) instead of the blind
                # centre. subject_aware_fallback returns FB_WIDE_PAIR ONLY for D1-A — D1-B (dominant host) and
                # D2 (PIP grid) get FB_DOMINANT and stay centred here (their slices are S3, S4/S5). The pair
                # anchors ride in `focus` (a 10-tuple of floats) + content_type=RENDER_STACK_PAIR — BOTH are
                # already hashed by _render_fingerprint, so exactly these clips (focus was None) re-render and
                # every other clip's fingerprint is byte-identical (no _REFRAME_GEOM_V bump).
                comp = subject_aware_fallback(stats)
                if (comp.kind == FB_WIDE_PAIR and comp.left is not None and comp.right is not None
                        and comp.left[4] is not None and comp.right[4] is not None):
                    focus = tuple(float(v) for v in comp.left) + tuple(float(v) for v in comp.right)
                    out_ct, strategy, outcome = RENDER_STACK_PAIR, _FS.SUBJECT_PAIR, _FO.STACKED_PAIR
                elif (comp.kind == FB_DOMINANT and comp.fw is not None
                        and _face_count(stats) <= _LOCK_MAX_FACES):
                    # S3/D1-B: ONE persistently dominant host, the second participant intermittent/peripheral.
                    # The blind centre crop starts at x=0.342 while these hosts sit at cx 0.317-0.381, so the
                    # centre pins the host's face against its LEFT edge (25/25 clips) or excludes it outright
                    # (6/25) — spec F5. RE-ANCHOR the SAME-WIDTH crop onto the host; the defect is POSITIONAL,
                    # so no zoom is required to fix it and none is applied for emphasis (F6 / ADR-0103 minimal
                    # zoom — clip.reframe_filter caps this content_type at _GENTLE_ZOOM_MAX). The _LOCK_MAX_FACES
                    # gate keeps D2's PIP grid out: it also yields FB_DOMINANT but its tile materiality is a
                    # Track-B product question (P1), so it stays centred here for S4/S5.
                    # NOT active-speaker following (Track B): one fixed anchor for the window, so an
                    # intermittent second face can never induce a pan. AR-1 (the host speaks off-frame) is an
                    # accepted residual of framing the dominant subject without audio.
                    focus = (float(comp.cx), float(comp.cy), float(comp.fh), float(comp.ey), float(comp.fw))
                    out_ct, strategy, outcome = RENDER_SUBJECT_LOCK, _FS.SUBJECT_LOCK, _FO.SUBJECT_LOCKED
                else:
                    out_ct, strategy, outcome = None, _FS.CENTERED, _FO.CENTERED_MULTI_UNTRACKED

    if outcome is None and ct_eff in (CT_SINGLE, CT_MUSIC, CT_SILENT):
        focus = _run(_FS.SUBJECT_FOCUS, lambda: subject_focus(cfg, src, start=cs, end=ce, _rt=rt, _trace=trace))
        if focus is not None:
            out_ct, strategy = ct_eff, _FS.SUBJECT_FOCUS
            outcome = _FO.MUSIC_FOCUS if ct_eff == CT_MUSIC else _FO.DETECTED_SINGLE

    if outcome is None and ct_eff in (CT_MUSIC, CT_SILENT, CT_NOPEOPLE):
        sal = _run(_FS.MOTION_SALIENCY, lambda: motion_saliency(cfg, src, start=cs, end=ce, _rt=rt, _trace=trace))
        if sal is not None:
            # D9 / C-2: content_type STAYS None here, and must keep doing so. A 2-tuple focus carries no
            # face height, so nothing zooms. The guard is the Layer-1 tuple vector, NOT the fingerprint
            # golden: _render_fingerprint gates `ct` behind `geom`, which is False for a 2-tuple, so
            # returning ct here would change the fingerprint of exactly NOTHING and slip through silently.
            focus, track, out_ct = sal, None, None
            strategy, outcome = _FS.MOTION_SALIENCY, _FO.MOTION_FOCUS

    if outcome is None:                                       # centered crop (today)
        focus = None; track = None; out_ct = None

    # SKIPPED: routing INCLUDED it, but a prior strategy resolved first, so it never started.
    for s in _ROUTE.get(ct, ()):
        if s not in ran:
            attempts.append(StrategyAttempt(strategy=s, applicable=True, required_for_center=True,
                                            started=False, completed=False, failure_event=None,
                                            negative_result=None, produced_focus=False))
    atts = tuple(attempts)
    degraded = tuple(a.strategy for a in atts if a.required_for_center and a.state is StrategyState.FAILED)

    if detection_failure is not None:                         # the hard trust gate — outranks any strategy result
        final_outcome, root = _FO.UNRESOLVED, detection_failure
    elif outcome is not None:
        final_outcome, root = outcome, None
    else:
        required = [a for a in atts if a.required_for_center]
        bad = [a for a in required if a.state is not StrategyState.COMPLETED]   # FAILED or (unreachably) SKIPPED
        if bad:
            final_outcome = _FO.UNRESOLVED
            root = next((a.failure_event for a in bad if a.failure_event is not None), _FE.UNKNOWN)
        elif any(a.produced_focus for a in required):
            raise ResolverInvariantError("centered terminal reached with a focus-producing required strategy")
        else:
            final_outcome, root = _FO.CENTERED_NO_SUBJECT, None   # the ONLY legitimate centre

    return FramingResolution(focus=focus, track=track, content_type=out_ct, final_outcome=final_outcome,
                             final_strategy=strategy, root_cause=root, classified_content_type=ct,
                             attempts=atts, events=trace.events, degraded_strategies=degraded)


def _unresolved(trace: FramingTrace, root: _FE, attempts: tuple, ct: str | None) -> FramingResolution:
    """A capture-mode early exit: the detection phase escaped, so nothing ran and nothing is trusted.
    The 3-tuple is the centered crop — the same thing the raising production path would never have
    reached a return for at all."""
    return FramingResolution(focus=None, track=None, content_type=None, final_outcome=_FO.UNRESOLVED,
                             final_strategy=_FS.CENTERED, root_cause=root, classified_content_type=ct,
                             attempts=attempts, events=trace.events, degraded_strategies=())


# ---- S1: shared subject-aware fallback primitive (Track A / ADR-0103 / spec F5) -----------------------
# The EXPLICIT structured alternative to the implicit blind-centre fallback. A PURE reducer over the cached
# detect stats (the SAME per-frame face tuples classify_window / subject_focus / _two_cluster already read)
# that decides, from the detected subject POSITIONS alone: is this ONE stable dominant subject, TWO
# persistent wide-separated subjects, or too little evidence to say? It renders NOTHING and is wired into NO
# route here — S2/S3/S4/S5 consume it per defect population — so every existing framing path stays
# byte-identical and no clip's render fingerprint changes in this slice.
FB_DOMINANT = "dominant"          # one stable dominant subject -> anchor + span on THAT subject
FB_WIDE_PAIR = "wide-pair"        # two persistent wide-separated subjects -> span covers BOTH (retain both)
FB_PIP = "pip"                    # S4/D2: presenter-dominant PIP grid -> anchor on the PRESENTER, tiles are inert UI
FB_INSUFFICIENT = "insufficient"  # evidence below the floor -> EXPLICIT no-op (caller keeps its safe default)

# S2: a genuine wide TWO-SHOT (D1-A) is separated from a dominant-host clip (D1-B) and a PIP grid (D2) by
# MORE than _two_cluster, which fires on ALL of them (evidence: docs/design/reframe/evidence/raw-detections.json
# — every one of the 67 has two=True). The discriminators that isolate D1-A: the median face-count is EXACTLY
# 2 (D1-B is 1, D2 is 4), both hosts are co-present across the window, and their faces are of comparable size.
_PAIR_COPRESENCE = 0.65    # both/max(nL,nR): the two hosts co-occur in most occupied frames (D1-B: ~0.52-0.55)
_PAIR_PROMINENCE = 0.6     # min(peak)/max(peak): comparable face sizes (rejects a big presenter + small tile, D2)
RENDER_STACK_PAIR = "stack-pair"  # content_type render-hint: compose BOTH hosts into a vertical stack (clip.render_reframed)

# S3: FB_DOMINANT is returned by BOTH D1-B (one dominant host + an intermittent second) and D2 (a presenter +
# a PIP tile column) — so kind alone cannot wire the D1-B re-anchor without also capturing D2, whose tile
# materiality is an OPEN product question (P1, Track B). The median per-frame face count separates them with
# no overlap (evidence: docs/design/reframe/evidence/raw-detections.json + defect-map.json, all 67 clips —
# D1-B median is 1 (21 clips) or 2 (4); D2 is 4 for all 36; D1-A is 2 but returns FB_WIDE_PAIR before here).
_LOCK_MAX_FACES = 2        # gate the subject-lock to <=2 faces: captures D1-B 25/25, captures D2 0/36
RENDER_SUBJECT_LOCK = "subject-lock"  # content_type render-hint: mild re-anchor onto the dominant host (clip.reframe_filter)

# S4 / spec F4 + AC-D1: "one large face + a column of small faces — no audio". The layout signal is GEOMETRY,
# and specifically SIZE. It must never be _pick_dominant_face: that ranks by YuNet SCORE (height is only a
# tie-break), and on a PIP grid the 3-tile column draws the score maximum against the presenter's single draw —
# so the "dominant" face lands on a TILE in 36/36 D2 clips, and on the SMALLEST tile in 36/36 (evidence:
# raw-detections.json — dom_cx 0.775-0.832 at dom_fh 0.134-0.192, while the presenter is L_cx~0.367 @ L_fh~0.435).
# Score cannot separate them at all (presenter out-scores the tiles in only 25/36, by margins of -0.010..+0.064);
# SIZE separates them cleanly, presenter/tiles = 1.60-2.07x in 36/36.
# BOTH gates are required and each is load-bearing:
#   _PIP_MIN_FACES  — a GRID. D2 median face-count is 4; D1-A is 2; D1-B is 1-2. Without it the size gate alone
#                     captures 7/25 D1-B (whose ratio reaches 1.53).
#   _PIP_SIZE_RATIO — PRESENTER-DOMINANT. Below it the layout is an equal-size panel (4 real co-speakers), which
#                     is NOT presenter+tiles -> FB_INSUFFICIENT, an explicit safe no-op rather than a guess.
#                     D2 measures 1.60-2.07; D1-B tops out at 1.53 and D1-A at 1.27, so 1.4 sits in the gap.
_PIP_MIN_FACES = 3
_PIP_SIZE_RATIO = 1.4

@dataclass(frozen=True)
class FallbackComposition:
    """An explicit, subject-derived fallback composition (spec F5, ADR-0103 decision 1) — the structured
    alternative to the implicit blind centre.

    `kind` classifies the evidence. `x_min`/`x_max` is the horizontal interval (normalized [0,1]) the crop
    MUST retain: a later slice picks the WIDEST crop containing it, so a narrow span barely zooms and a wide
    span stays wide — the mild-framing / minimize-zoom requirement (spec F6). `cx,cy,ey,fh,fw` anchor and
    size the dominant subject (fh/fw drive zoom + the horizontal safe-area). `confidence` is the fraction of
    sampled frames carrying a face. FB_INSUFFICIENT carries NO geometry (all None): the caller keeps its
    current safe default, but the choice is now EXPLICIT and inspectable, never a silent blind centre.
    Deterministic — a pure function of the stats: no cv2, no I/O, never raises."""
    kind: str
    x_min: float | None
    x_max: float | None
    cx: float | None
    cy: float | None
    ey: float | None
    fh: float | None
    fw: float | None
    confidence: float
    left: tuple | None = None    # S2 FB_WIDE_PAIR: the LEFT host anchor (cx,cy,fh,ey,fw) for the vertical stack
    right: tuple | None = None   # S2 FB_WIDE_PAIR: the RIGHT host anchor (cx,cy,fh,ey,fw)

    @property
    def is_actionable(self) -> bool:
        """True when the evidence yielded a subject-derived region (DOMINANT / WIDE_PAIR / PIP); False for the
        explicit INSUFFICIENT no-op — so a caller branches on 'did we get a subject?' without string-matching."""
        return self.kind in (FB_DOMINANT, FB_WIDE_PAIR, FB_PIP)

def _c01(v: float) -> float:
    return min(1.0, max(0.0, v))                              # clamp to [0,1] — the module's inline idiom

def _fb_insufficient(conf: float) -> "FallbackComposition":
    return FallbackComposition(FB_INSUFFICIENT, None, None, None, None, None, None, None, round(conf, 4))

def _pair_clusters(occ: list):
    """Per-side cluster stats for the two-shot split across the occupied frames, or None when either side is
    never present. Returns (nL, nR, both, peak_l, peak_r, left_anchor, right_anchor) where nL/nR = frames the
    side's dominant face appears, both = frames BOTH appear, peak = best score×fh on that side, and each
    anchor = median (cx,cy,fh,ey,fw) of that side's dominant face (fw None on the legacy 4-tuple shape).
    Mirrors _two_cluster's split (dead-zone ±_ASD_SAME_TOL, _CLUSTER_MIN_FH floor) so the pair is built from
    the SAME two clusters the classifier found. The counts drive the D1-A vs D1-B/D2 discriminators; the
    anchors drive the vertical-stack crop (each host in its own half)."""
    lo, hi = _ASD_SIDE_SPLIT - _ASD_SAME_TOL, _ASD_SIDE_SPLIT + _ASD_SAME_TOL
    def _q(f): return (f[4] if len(f) > 4 else 1.0) * f[2]
    L: list = []; R: list = []; nL = nR = both = 0; peak_l = peak_r = 0.0
    for fr in occ:
        lp = _pick_dominant_face([f for f in fr if f[0] < lo and f[2] >= _CLUSTER_MIN_FH])
        rp = _pick_dominant_face([f for f in fr if f[0] > hi and f[2] >= _CLUSTER_MIN_FH])
        if lp is not None: nL += 1; L.append(lp); peak_l = max(peak_l, _q(lp))
        if rp is not None: nR += 1; R.append(rp); peak_r = max(peak_r, _q(rp))
        if lp is not None and rp is not None: both += 1
    if not L or not R: return None
    def _anchor(picks):
        fws = [p[5] for p in picks if len(p) > 5]
        fw = round(median(fws), 4) if len(fws) == len(picks) else None
        return (round(median(p[0] for p in picks), 4), round(median(p[1] for p in picks), 4),
                round(median(p[2] for p in picks), 4), round(median(p[3] for p in picks), 4), fw)
    return (nL, nR, both, round(peak_l, 4), round(peak_r, 4), _anchor(L), _anchor(R))

def _pip_layout(stats: dict | None, occ: list):
    """The presenter anchor for a presenter-dominant PIP grid, or None when the layout is not one.

    Returns (anchor, tile_fh) where anchor = median (cx,cy,fh,ey,fw) of the LARGEST face per frame and tile_fh
    is the median height of every other face. The presenter is picked BY HEIGHT — never `_pick_dominant_face`,
    whose score-first ranking lands on the smallest remote tile (see the _PIP_* note above).

    None (-> the caller falls through to the pair/dominant branches, unchanged) when: the grid gate fails, no
    frame carries enough faces, or the size hierarchy is too flat to call one face the presenter (an equal-size
    panel of real co-speakers is NOT presenter+tiles). Pure; never raises."""
    if _face_count(stats) < _PIP_MIN_FACES:
        return None                                           # not a grid (D1-A is 2, D1-B is 1-2)
    pres: list = []; tile_fhs: list = []
    for fr in occ:
        if len(fr) < _PIP_MIN_FACES: continue
        ranked = sorted(fr, key=lambda f: f[2], reverse=True)  # by HEIGHT. NEVER by score.
        pres.append(ranked[0]); tile_fhs += [f[2] for f in ranked[1:]]
    if not pres or not tile_fhs:
        return None
    p_fh = median(p[2] for p in pres); t_fh = median(tile_fhs)
    if t_fh <= 0 or p_fh < _PIP_SIZE_RATIO * t_fh:
        return None                                           # too flat to name a presenter -> ambiguous
    fws = [p[5] for p in pres if len(p) > 5]
    fw = round(median(fws), 4) if len(fws) == len(pres) else None    # None on the legacy 4-tuple shape
    anchor = (round(median(p[0] for p in pres), 4), round(median(p[1] for p in pres), 4),
              round(p_fh, 4), round(median(p[3] for p in pres), 4), fw)
    return anchor, round(t_fh, 4)

def subject_aware_fallback(stats: dict | None) -> FallbackComposition:
    """From the detected face positions of a no-track window, return the EXPLICIT fallback composition
    (spec F5, ADR-0103 decision 1). PURE: same stats -> same result; no cv2, no I/O; never raises.

    - No frames, or too few carry a face (< _MIN_CONF) -> FB_INSUFFICIENT (fail-safe; the caller keeps its
      current safe default, but the no-op is now explicit and recorded).
    - Two persistent wide-separated subjects (the E2 `_two_cluster` recall) -> FB_WIDE_PAIR whose span covers
      BOTH clusters, so a later slice retains both at the widest crop.
    - Otherwise the single stable dominant subject -> FB_DOMINANT whose span is that subject's OWN face box —
      NEVER widened to a sub-threshold PIP tile or an intermittent second face.

    The span is a function of the subject positions (it moves when they move); there is no fixed
    content-blind region. `stats` is detect_window's dict — `{fps, frames:[[ (cx,cy,fh,ey,score,fw), .. ], ..]}`
    (or a legacy 4-tuple shape, where the width-derived span degenerates to the centroid, fw=None)."""
    frames = (stats or {}).get("frames") or []
    if not frames:
        return _fb_insufficient(0.0)
    occ = [fr for fr in frames if fr]
    conf = len(occ) / len(frames)
    picks = [p for p in (_pick_dominant_face(fr) for fr in occ) if p is not None]
    if not picks or conf < _MIN_CONF:
        return _fb_insufficient(conf)
    dcx = median(p[0] for p in picks); dcy = median(p[1] for p in picks)
    dfh = median(p[2] for p in picks); dey = median(p[3] for p in picks)
    fws = [p[5] for p in picks if len(p) > 5]
    dfw = median(fws) if len(fws) == len(picks) else None     # None on the legacy 4-tuple shape (no width)
    pip = _pip_layout(stats, occ)                              # S4/D2: a UI grid, not a live two-shot (F4/AC-D1)
    if pip is not None:
        pa, _t_fh = pip
        half = (pa[4] / 2) if pa[4] is not None else 0.0
        return FallbackComposition(FB_PIP, round(_c01(pa[0] - half), 4), round(_c01(pa[0] + half), 4),
                                   pa[0], pa[1], pa[3], pa[2], pa[4], round(conf, 4))
    if _two_cluster(stats):                                    # E2 recall fires on ALL of D1-A/D1-B/D2 — refine:
        pc = _pair_clusters(occ)
        if pc is not None:
            nL, nR, both, peak_l, peak_r, la, ra = pc
            copres = both / max(nL, nR)
            hi_peak = max(peak_l, peak_r)
            promin = (min(peak_l, peak_r) / hi_peak) if hi_peak > 0 else 0.0
            # D1-A ONLY: median face-count == 2 (D1-B is 1, D2 is 4) AND both hosts co-present AND comparable
            # sizes. D1-B (dominant host + intermittent 2nd) and D2 (presenter + tile column) fail a gate and
            # fall through to FB_DOMINANT, where they stay UNWIRED (centred) until their slices (S3, S4/S5).
            if _face_count(stats) == 2 and copres >= _PAIR_COPRESENCE and promin >= _PAIR_PROMINENCE:
                lcx, lfw = la[0], (la[4] or 0.0); rcx, rfw = ra[0], (ra[4] or 0.0)
                x_min = _c01(min(lcx - lfw / 2, rcx - rfw / 2))
                x_max = _c01(max(lcx + lfw / 2, rcx + rfw / 2))
                return FallbackComposition(FB_WIDE_PAIR, round(x_min, 4), round(x_max, 4),
                                           round((lcx + rcx) / 2, 4), round(dcy, 4), round(dey, 4), round(dfh, 4),
                                           (round(dfw, 4) if dfw is not None else None), round(conf, 4),
                                           left=la, right=ra)
    # DOMINANT: span is the dominant subject's OWN box — a PIP tile / intermittent second face never widens it.
    half = (dfw / 2) if dfw is not None else 0.0
    return FallbackComposition(FB_DOMINANT, round(_c01(dcx - half), 4), round(_c01(dcx + half), 4),
                               round(dcx, 4), round(dcy, 4), round(dey, 4), round(dfh, 4),
                               (round(dfw, 4) if dfw is not None else None), round(conf, 4))
