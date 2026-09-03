"""Render fingerprint — content-address key for clip renders.

The render fingerprint captures everything that determines the rendered bytes (source, window,
aspect, source dims, the burned .ass text), so the lock-free pre-warm and the in-lock commit
agree on when an existing mp4 may be reused."""
from __future__ import annotations

import hashlib
import json

# bump to force re-render of ZOOM/eyeline/dynamic clips after a geometry-math change
# (v5: E1b face-box safe-area — margin on every edge, headroom, zoom-backoff, face width
#  in focus/track; v4: STATIC locked-off crop per shot — adaptive far-speaker zoom + min-shot merge)
_REFRAME_GEOM_V = 5

def _render_fingerprint_payload(src_path: str, cs: float, ce: float, aspect_value: str,
                                src_w: int, src_h: int, ass_text: str, *, top_bias: bool = False,
                                focus: tuple | None = None, track: list | None = None,
                                content_type: str | None = None,
                                supercut_segments: list[tuple[float, float]] | None = None,
                                supercut_span_entries: list | None = None) -> dict:
    """The fingerprint PAYLOAD — every input that determines the rendered bytes, before hashing.

    Split out from _render_fingerprint (which is now a thin hash over this) so a read-only caller can
    DIFF two payloads. `{cid}.render.json` persists ONLY the sha256, never the payload, so the historical
    inputs are gone: you cannot diff a hash. A reconstruction must therefore be a PROOF — enumerate the
    candidate legacy payloads, hash each, and accept the one whose digest equals the stored one."""
    payload = {"src": src_path, "cs": round(cs, 3), "ce": round(ce, 3), "aspect": aspect_value,
               "w": src_w, "h": src_h, "ass": ass_text}
    if top_bias:                                          # additive: absent key -> byte-identical fp to today
        payload["top_bias"] = True
    if focus is not None:                                 # ALL elements: a 2-tuple hashes [fx,fy] (== old);
        payload["focus"] = [round(v, 3) for v in focus]  # a 4-tuple adds fh,ey -> zoom changes bytes -> re-render
    if track:                                             # full 6-tuple (fh,ey carried) -> dynamic crop re-renders
        payload["track"] = [[round(s[0], 2), round(s[1], 2)] + [round(v, 3) for v in s[2:]] for s in track]
    geom = bool(track) or (focus is not None and len(focus) > 2)   # zoom/eyeline/dynamic present?
    if content_type and geom:                            # content_type only alters bytes when a zoom applies
        payload["ct"] = content_type
    if geom:                                              # version the new geometry so a future change can bust it
        payload["geom"] = _REFRAME_GEOM_V
    if supercut_segments:
        payload["supercut"] = [[round(float(s), 3), round(float(e), 3)] for s, e in supercut_segments]
        if supercut_span_entries:
            payload["sc_spans"] = [[round(s[0], 2), round(s[1], 2)]
                                   + [round(v, 3) if v is not None else None for v in s[2:]]
                                   for s in supercut_span_entries]
    return payload

def fingerprint_payload_bytes(payload: dict) -> bytes:
    """The EXACT canonical serialization _render_fingerprint hashes. Candidate reconstruction dedups on
    THESE BYTES, never on dict equality: {"cs": 0.0} and {"cs": -0.0} compare equal as dicts but
    serialize differently, so a dict-keyed dedup could silently drop a byte-distinct candidate and
    report a false UNRECONSTRUCTABLE. No candidate axis can produce a signed zero today (fit_window
    floors at 0.0, focus is clamped to [0,1], _compute_track snaps track[0][0] to 0.0) — this is
    DEFENCE, not a bug fix, and it costs nothing."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")

def fingerprint_of_payload(payload: dict) -> str:
    return hashlib.sha256(fingerprint_payload_bytes(payload)).hexdigest()

def _render_fingerprint(src_path: str, cs: float, ce: float, aspect_value: str,
                        src_w: int, src_h: int, ass_text: str, *, top_bias: bool = False,
                        focus: tuple | None = None, track: list | None = None,
                        content_type: str | None = None,
                        supercut_segments: list[tuple[float, float]] | None = None,
                        supercut_span_entries: list | None = None) -> str:
    return fingerprint_of_payload(_render_fingerprint_payload(
        src_path, cs, ce, aspect_value, src_w, src_h, ass_text, top_bias=top_bias, focus=focus,
        track=track, content_type=content_type, supercut_segments=supercut_segments,
        supercut_span_entries=supercut_span_entries))

def _fingerprint_matches(fp_path, fp: str) -> bool:
    try:
        return fp_path.exists() and json.loads(fp_path.read_text()).get("fp") == fp
    except (OSError, json.JSONDecodeError, ValueError):
        return False
