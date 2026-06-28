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
import contextlib, json, os
from pathlib import Path
from statistics import median

_SIDECAR_V = 2               # bump to invalidate cached focuses when the detector changes (v2: Haar -> YuNet)
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

def _detect_centroids(cv2, frames: list[str]) -> list[tuple[float, float]]:
    """Per frame, the normalized center of the LARGEST detected face (the dominant subject). A frame with
    no face contributes nothing. Any per-frame read/decode error is skipped, never fatal (fail-open)."""
    det = _detector(cv2)
    if det is None:
        return []                                             # detector unavailable -> no detection
    out: list[tuple[float, float]] = []
    for fp in frames:
        try:
            img = cv2.imread(fp)
            if img is None: continue
            h, w = img.shape[:2]
            if not (w and h): continue
            det.setInputSize((w, h))
            _n, faces = det.detect(img)
            if faces is None or len(faces) == 0: continue
            b = max(faces, key=lambda f: float(f[2]) * float(f[3]))   # widest*tallest box = the dominant subject
            cx = min(1.0, max(0.0, (float(b[0]) + float(b[2]) / 2) / w))
            cy = min(1.0, max(0.0, (float(b[1]) + float(b[3]) / 2) / h))
            out.append((cx, cy))
        except Exception:
            continue                                          # a single bad frame never sinks the window
    return out

def _sidecar(cfg, source_id: str) -> Path:
    return cfg.agent_io / "framing" / f"{source_id}.json"

def _wkey(start: float, end: float) -> str:
    return f"{round(start, 2)}-{round(end, 2)}"

def _load_cache(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        if d.get("v") != _SIDECAR_V: return {}               # stale detector version -> recompute
        return d.get("windows") or {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}                                             # corrupt sidecar -> recompute (overwrites)

_ASD_BIN_S = 2.0             # active-speaker decision granularity: re-pick the on-screen speaker every ~2s
_ASD_FRAMES = 4             # frames per bin for the mouth-motion measure (>=2 needed for a temporal diff)
_ASD_RATIO = 1.25          # the louder mouth must out-move the other by this factor to STEAL the frame (else hold)
_ASD_SAME_TOL = 0.08       # two centroids within this normalized x are "the same shot" -> merge (no needless cut)
_ASD_SIDE_SPLIT = 0.5      # faces left/right of this normalized x are different speakers (the 2-shot split)

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

def _bin_active(cv2, det, frames: list[str]):
    """Across one bin's frames, return {side: (centroid, motion)} per occupied side (L/R of _ASD_SIDE_SPLIT).
    motion = mean abs frame-to-frame diff of the side's mouth ROI (speaking -> high). A side seen <2 frames
    has motion 0.0 (can't measure)."""
    import numpy as np
    cents = {"L": [], "R": []}; rois = {"L": [], "R": []}
    for fp in frames:
        try:
            img = cv2.imread(fp)
            if img is None: continue
            h, w = img.shape[:2]
            if not (w and h): continue
            det.setInputSize((w, h))
            _n, faces = det.detect(img)
            if faces is None: continue
            for f in faces:
                cx = (float(f[0]) + float(f[2]) / 2) / w
                side = "L" if cx < _ASD_SIDE_SPLIT else "R"
                cents[side].append((min(1.0, max(0.0, cx)), min(1.0, max(0.0, (float(f[1]) + float(f[3]) / 2) / h))))
                roi = _mouth_roi(cv2, img, f)
                if roi is not None: rois[side].append(roi)
        except Exception:
            continue                                          # a single bad frame never sinks the bin (fail-open)
    out = {}
    for side in ("L", "R"):
        if not cents[side]: continue
        fx = round(median(c[0] for c in cents[side]), 4); fy = round(median(c[1] for c in cents[side]), 4)
        diffs = [float(np.mean(np.abs(rois[side][i].astype(int) - rois[side][i - 1].astype(int))))
                 for i in range(1, len(rois[side]))]
        out[side] = ((fx, fy), (sum(diffs) / len(diffs)) if diffs else 0.0)
    return out

def speaker_track(cfg, src, *, start: float, end: float, src_w: int, src_h: int):
    """Follow the ACTIVE speaker across a 2-shot: a time-ordered list of (t0_rel, t1_rel, fx, fy) segments
    (times RELATIVE to the clip start) where fx/fy is the speaking subject's normalized centroid. Returns
    None — the fail-open signal that the caller should use the STATIC subject_focus (today's single crop) —
    whenever there's nothing dynamic to do: no [framing] extra, a single-camera window (one face throughout),
    one detected position, or any error. So a single-subject clip is byte-identical to before; only a real
    two-person 2-shot gets a speaker-following cut. NEVER raises. Cached per (source, window)."""
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
    cache[key] = result
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": _SIDECAR_V, "windows": cache}))
    except OSError:
        return result                                         # cache write failure just re-probes next time
    return result

def _compute_track(cv2, det, cfg, src, start: float, end: float):
    """The detection body of speaker_track (kept separate so the cache/fail-open wrapper stays tiny)."""
    from fanops.keyframes import extract_keyframes
    # Per-(source, window) tmp dir — same concurrency-collision fix as subject_focus (WS4): the prewarm pool
    # would otherwise let two workers clobber each other's bin frames in a shared dir.
    tmp = cfg.agent_io / "framing" / "tmp" / f"{getattr(src, 'id', 'nosrc')}_asd_{_wkey(start, end)}"
    dur = end - start
    nbins = max(1, int(dur // _ASD_BIN_S))
    raw = []                                                  # [t0_rel, t1_rel, fx, fy] per bin
    prev = None                                               # last chosen (fx, fy) — held through ambiguous/empty bins
    for i in range(nbins):
        bs, be = start + dur * i / nbins, start + dur * (i + 1) / nbins
        frames = extract_keyframes(getattr(src, "source_path", ""), bs, be,
                                   count=_ASD_FRAMES, out_dir=tmp, width=_KF_WIDTH)
        sides = _bin_active(cv2, det, frames)
        for f in frames:
            with contextlib.suppress(OSError):
                os.unlink(f)
        if len(sides) >= 2:
            ranked = sorted(sides.items(), key=lambda kv: kv[1][1], reverse=True)   # by motion desc
            top_c, top_m = ranked[0][1]; snd_m = ranked[1][1][1]
            chosen = top_c if top_m >= _ASD_RATIO * max(snd_m, 1e-6) else (prev or top_c)
        elif len(sides) == 1:
            chosen = next(iter(sides.values()))[0]
        else:
            chosen = prev                                     # no face this bin -> hold the last speaker
        if chosen is not None:
            prev = chosen
            raw.append([round(bs - start, 2), round(be - start, 2), chosen[0], chosen[1]])
    with contextlib.suppress(OSError):                        # remove the now-empty per-call dir (frames already unlinked)
        os.rmdir(tmp)
    if not raw:
        return None                                           # nothing detected anywhere -> static path
    merged = [raw[0]]                                         # coalesce adjacent same-position bins (no needless cut)
    for seg in raw[1:]:
        if abs(seg[2] - merged[-1][2]) <= _ASD_SAME_TOL:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)
    if len(merged) <= 1:
        return None                                           # one position the whole clip -> static focus is identical (+ cheaper)
    return [tuple(seg) for seg in merged]

def subject_focus(cfg, src, *, start: float, end: float):
    """The dominant subject's normalized centroid (fx, fy) in [0,1] across this cut window, or None when
    smart framing can't place it (no [framing] extra, no/too-few detections, or any failure). Cached per
    (source, window) in a versioned sidecar. NEVER raises — a None result is the universal fail-open signal
    and the render falls back to the centered crop."""
    if not (end > start):
        return None
    key = _wkey(start, end)
    path = _sidecar(cfg, getattr(src, "id", "nosrc"))
    cache = _load_cache(path)
    if key in cache:
        e = cache[key]
        return (e["fx"], e["fy"]) if e and e.get("fx") is not None else None
    cv2 = _cv2()
    frames: list[str] = []
    fx = fy = None
    # WS4 (audit c0-f2/c2-f1): a PER-(source, window) tmp dir, not one shared framing/tmp. extract_keyframes
    # names files only by (rounded-start, index), so two sources whose windows share a start would collide in a
    # shared dir — under FANOPS_CONCURRENT_SOURCES the prewarm runs this in a thread pool and a worker would
    # clobber/unlink another's frames (silently wrong crop or a None read). Keying the dir on (source, window)
    # makes the collision domain per-call, so the race can't be constructed (safe to default the flag on).
    tmp = cfg.agent_io / "framing" / "tmp" / f"{getattr(src, 'id', 'nosrc')}_{key}"
    try:
        if cv2 is not None:
            from fanops.keyframes import extract_keyframes
            frames = extract_keyframes(getattr(src, "source_path", ""), start, end,
                                       count=_KF_COUNT, out_dir=tmp, width=_KF_WIDTH)
            if frames:
                cents = _detect_centroids(cv2, frames)
                if cents and (len(cents) / len(frames)) >= _MIN_CONF:
                    fx = round(median(c[0] for c in cents), 4)
                    fy = round(median(c[1] for c in cents), 4)
    except Exception:
        fx = fy = None                                        # fail-open by contract
    finally:
        for f in frames:
            with contextlib.suppress(OSError):
                os.unlink(f)
        with contextlib.suppress(OSError):                   # remove the now-empty per-call dir (no accumulation)
            os.rmdir(tmp)
    cache[key] = {"fx": fx, "fy": fy}
    result = (fx, fy) if fx is not None else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": _SIDECAR_V, "windows": cache}))
    except OSError:
        return result                                         # cache write failure just re-probes next time
    return result
