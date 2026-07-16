# tests/test_reframe_s5_d2.py — S5 / Track A: D2 composition (dead-space).
# S4 recognised the PIP layout and kept it out of the active-speaker path, but still rendered the blind centre.
# The blind centre lands on the WALL between the presenter and the tile column. S5 re-anchors onto the
# presenter, using the SAME mild subject-lock S3 ships (RENDER_SUBJECT_LOCK -> _GENTLE_ZOOM_MAX).
#
# THE ONE S5 INVARIANT (spec F3 + F2; AC-D2/AC-D3): the presenter occupies the salient region, the output is
# not weighted onto empty background, his face is not edge-pinned — achieved at the WIDEST crop that does so.
#
# MEASURED (raw-detections.json, all 36 D2) — this is why the centre is wrong and what "widest" means here:
#   * the presenter is edge-pinned in 33/36 (L_cx within 0.05 of the crop's left edge 0.342) and OUTSIDE the
#     crop entirely in 3/36 (L_cx 0.309-0.400);
#   * his face is already at/above _FACE_FRAC_TALK (L_fh 0.396-0.491), so _zoom_h clamps to full source height
#     and the re-anchor is a near-pure HORIZONTAL shift (1.00x at the median, 1.06x at the smallest presenter).
#     F6 is satisfied by construction, not by a tuned threshold.
#
# AC-D4 (tile retention) IS NOT ENGAGED, and that is a derivation, not a preference:
#   * PRESERVING the tiles is geometrically impossible in one 9:16 crop — presenter->tile centre separation is
#     0.351-0.508 of width against a 0.316-wide crop, in 36/36, before either face box is added;
#   * the CURRENT output already drops them — every tile centre is outside the centre crop's right edge
#     (R_cx 0.716-0.873 vs 0.658), in 36/36, so no tile is EVER >=50% visible today.
#   So this slice moves tile retention from zero to zero. It is not the "presenter-only composition" AC-D4
#   reserves for P1 (Track B) — that gate protects a CHOICE, and no such choice exists.
#
# Fixtures stay adversarial (tiles out-score the presenter, as the real data does — see test_reframe_s4_d2).
import pytest
from fanops.config import Config
from fanops import framing, clip
from fanops.framing_outcomes import (FramingOutcome as _FO, FramingStrategy as _FS,
                                     FramingEventType as _FE)


class _Src:
    id = "src_t"; source_path = "/none/x.mp4"; width = 1920; height = 1080
    duration = 60.0; transcript = []; language = "en"; meta = {}; sha256 = "d"; signal_peaks = []


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    return Config(root=tmp_path)


def _stub(monkeypatch, **spec):
    monkeypatch.setattr(framing, "_framing_runtime_or_raise", lambda c: object())
    def mk(s):
        def fn(*a, _trace=None, **kw):
            events, value = s if (isinstance(s, tuple) and len(s) == 2 and isinstance(s[0], list)) else ([], s)
            for e in events:
                if _trace is not None: _trace.record(e)
            return value
        return fn
    for name, s in spec.items():
        monkeypatch.setattr(framing, name, mk(s))


def _face(cx, *, cy=0.45, fh=0.276, fw=0.166, score=0.93):
    return [round(cx, 4), round(cy, 4), round(fh, 4), round(cy, 4), score, round(fw, 4)]

def _stats(frames): return {"fps": 4.0, "frames": frames}

_PRESENTER_CX, _PRESENTER_FH, _PRESENTER_FW = 0.358, 0.404, 0.235
_TILES = [_face(0.76, cy=0.18, fh=0.242, fw=0.145, score=0.95),
          _face(0.78, cy=0.50, fh=0.242, fw=0.145, score=0.96),
          _face(0.77, cy=0.82, fh=0.170, fw=0.102, score=0.97)]      # smallest tile scores HIGHEST
_D2 = _stats([[_face(_PRESENTER_CX, cy=0.52, fh=_PRESENTER_FH, fw=_PRESENTER_FW, score=0.9303)] + _TILES
              for _ in range(86)])
_D1A = _stats([[_face(0.25, fh=0.19, fw=0.13), _face(0.75, fh=0.19, fw=0.13)] for _ in range(44)])
_D1B = _stats([[_face(0.352)] for _ in range(38)] +
              [[_face(0.352), _face(0.74, fh=0.20, fw=0.12, score=0.88)] for _ in range(22)])

_SW, _SH = 1920, 1080
_CROP_W = _SH * 1080 / 1920                       # 607.5 px — a 9:16 crop of a 16:9 source
_CENTRE_X0 = round((_SW - _CROP_W) / 2)           # 656
_CENTRE_X1 = _CENTRE_X0 + round(_CROP_W)          # 1264
_FACE_L = _PRESENTER_CX * _SW - (_PRESENTER_FW * _SW) / 2
_FACE_R = _PRESENTER_CX * _SW + (_PRESENTER_FW * _SW) / 2


def _resolve(monkeypatch, cfg, stats):
    _stub(monkeypatch, detect_window=stats, classify_window=framing.CT_MULTI,
          speaker_track=([_FE.NO_TRACK], None), subject_focus=([_FE.NO_FACE], None))
    return framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)

def _box(vf):
    """(cw, ch, x, y) from a crop token. At 1.00x zoom _focus_crop keeps the legacy SYMBOLIC width/height
    ("crop=ih*1080/1920:ih:X:Y") and carries the re-anchor in X — that is the byte-identity-preserving branch,
    and it is the one D2 takes, so the parser must read it rather than assume four ints."""
    parts = vf.split("crop=")[1].split(",")[0].split(":")
    if parts[0].startswith("ih*"):
        return round(_CROP_W), _SH, int(parts[2]), int(parts[3])
    return [int(v) for v in parts]


# ---- the defect this slice closes ------------------------------------------------------------------

def test_the_blind_centre_is_dead_space_dominant_and_edge_pins_the_presenter():
    """FAILING-BEFORE. The centre crop spans x=[656,1264]; the presenter's face box starts at 460 — so 196px
    of him is cut — while the rest of the frame is the wall between him and the tile column."""
    before = clip.reframe_filter("9:16", _SW, _SH, focus=None, track=None, content_type=None)
    assert before.startswith("crop=ih*1080/1920:ih,")
    assert _CENTRE_X0 > _FACE_L                             # the crop's left edge cuts into his face


# ---- the invariant ---------------------------------------------------------------------------------

def test_d2_presenter_is_framed_not_the_dead_space(monkeypatch, cfg):
    r = _resolve(monkeypatch, cfg, _D2)
    assert r.final_outcome is _FO.PIP_PRESENTER_FRAMED
    assert r.final_strategy is _FS.PIP_LAYOUT                # S4's routing is retained: still not the ASD path
    assert r.content_type == framing.RENDER_SUBJECT_LOCK
    assert r.focus is not None and len(r.focus) == 5
    assert r.focus[0] == pytest.approx(_PRESENTER_CX, abs=0.02)

def test_ac_d2_presenter_salient_and_not_edge_pinned(monkeypatch, cfg):
    r = _resolve(monkeypatch, cfg, _D2)
    cw, ch, x, y = _box(clip.reframe_filter("9:16", _SW, _SH, focus=r.focus, track=None,
                                            content_type=r.content_type))
    assert x <= _FACE_L and (x + cw) >= _FACE_R              # his whole face box is inside the crop
    assert abs((x + cw / 2) - _PRESENTER_CX * _SW) < 0.12 * _SW   # and materially centred, not pinned

def test_ac_d3_widest_crop_no_punch_in(monkeypatch, cfg):
    """His face already exceeds _FACE_FRAC_TALK, so the crop clamps to full source height: a pure horizontal
    re-anchor at 1.00x. F6 is satisfied by construction here, not by a threshold."""
    r = _resolve(monkeypatch, cfg, _D2)
    _, ch, _, _ = _box(clip.reframe_filter("9:16", _SW, _SH, focus=r.focus, track=None,
                                           content_type=r.content_type))
    assert _SH / ch <= clip._GENTLE_ZOOM_MAX + 0.01
    assert ch == _SH                                        # this fixture: NO zoom at all

def test_no_remote_tile_is_promoted(monkeypatch, cfg):
    """The tiles must not gain prominence. The crop is anchored on the presenter and its right edge stops well
    short of the tile column — retention stays at the zero it already was, never a stack or a pad."""
    r = _resolve(monkeypatch, cfg, _D2)
    cw, ch, x, y = _box(clip.reframe_filter("9:16", _SW, _SH, focus=r.focus, track=None,
                                            content_type=r.content_type))
    for tile in _TILES:
        assert (x + cw) < tile[0] * _SW                     # every tile centre is right of the crop
    assert framing.RENDER_STACK_PAIR not in (r.content_type or "")   # never composed as a pair/stack


# ---- the tile-retention derivation (AC-D4 is not engaged) ------------------------------------------

def test_a_single_crop_cannot_hold_both_presenter_and_tiles():
    """Pins the geometry that makes AC-D4 moot: 'preserve the tiles' is not an option that exists."""
    sep = min(t[0] for t in _TILES) - _PRESENTER_CX
    assert sep > (_CROP_W / _SW)                            # separation exceeds the whole crop width...
    assert _CENTRE_X1 < min(t[0] for t in _TILES) * _SW     # ...and today's centre already excludes every tile


# ---- the other classes are untouched ---------------------------------------------------------------

def test_d1a_and_d1b_are_unaffected(monkeypatch, cfg):
    assert _resolve(monkeypatch, cfg, _D1A).final_outcome is _FO.STACKED_PAIR
    assert _resolve(monkeypatch, cfg, _D1B).final_outcome is _FO.SUBJECT_LOCKED


# ---- fingerprint / re-render population ------------------------------------------------------------

def _fp(**kw):
    base = dict(src_path="x.mp4", cs=0.0, ce=10.0, aspect_value="9:16", src_w=1920, src_h=1080, ass_text="")
    return clip._render_fingerprint(**{**base, **kw})

def test_exactly_the_d2_population_re_renders(monkeypatch, cfg):
    r = _resolve(monkeypatch, cfg, _D2)
    assert _fp(focus=r.focus, track=None, content_type=r.content_type) != _fp(focus=None, track=None, content_type=None)

def test_geom_version_is_not_bumped():
    """S5 affects one defect population. A _REFRAME_GEOM_V bump would invalidate every zoomed clip."""
    assert clip._REFRAME_GEOM_V == 5

def test_a_legacy_detection_without_face_width_stays_centred(monkeypatch, cfg):
    """No face WIDTH -> E1b's horizontal safe-area cannot be honoured, so the layout is still recognised (kept
    out of the ASD path) but NOT re-anchored on geometry we cannot verify. Fail-safe, and explicit."""
    legacy = _stats([[[_PRESENTER_CX, 0.52, _PRESENTER_FH, 0.52, 0.93],
                      [0.76, 0.18, 0.242, 0.18, 0.95], [0.78, 0.50, 0.242, 0.50, 0.96],
                      [0.77, 0.82, 0.170, 0.82, 0.97]] for _ in range(86)])
    r = _resolve(monkeypatch, cfg, legacy)
    assert r.final_outcome is _FO.CENTERED_PIP_LAYOUT
    assert r.as_tuple() == (None, None, None)
