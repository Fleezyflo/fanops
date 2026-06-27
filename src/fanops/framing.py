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

_SIDECAR_V = 1               # bump to invalidate cached focuses when the detector changes
_KF_COUNT = 5                # frames sampled across the window — enough for a stable median, cheap to probe
_MIN_CONF = 0.34             # need a face in >=34% of sampled frames (>=2 of 5) — else fall back to center crop

def _cv2():
    """The OpenCV module, or None when the [framing] extra isn't installed (caller -> center crop)."""
    try:
        import cv2                                            # noqa: PLC0415 — lazy by design (optional extra)
        return cv2
    except Exception:
        return None

def _detect_centroids(cv2, frames: list[str]) -> list[tuple[float, float]]:
    """Per frame, the normalized center of the LARGEST detected face (the dominant subject). A frame with
    no face contributes nothing. Any per-frame read/decode error is skipped, never fatal (fail-open)."""
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cascade.empty():
        return []                                             # cascade asset missing -> no detection
    out: list[tuple[float, float]] = []
    for fp in frames:
        try:
            img = cv2.imread(fp)
            if img is None: continue
            h, w = img.shape[:2]
            if not (w and h): continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
            if len(faces) == 0: continue
            x, y, fw, fh = max(faces, key=lambda b: b[2] * b[3])   # the biggest face = the dominant subject
            out.append(((x + fw / 2) / w, (y + fh / 2) / h))
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
                                       count=_KF_COUNT, out_dir=tmp)
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
