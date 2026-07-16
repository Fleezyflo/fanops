# tests/test_reframe_s3_d1b.py — S3 / Track A: D1-B dominant-host edge-pin correction.
# A no-track window with ONE persistently dominant host and an intermittent/peripheral second participant no
# longer falls to the blind CENTRE crop. The evidence is unambiguous (docs/design/reframe/evidence/
# raw-detections.json, 25 D1-B clips): the hosts sit at dom_cx 0.317-0.381 while a 9:16 centre crop of a
# 16:9 source starts at x=0.342 — so the centre pins the host's face against its LEFT edge in 25/25 clips and
# excludes him outright in 6. framing._resolve now RE-ANCHORS the crop onto that host
# (content_type=RENDER_SUBJECT_LOCK, focus = his 5-tuple anchor).
#
# THE FINDING THIS SLICE RESTS ON: FB_DOMINANT is returned by BOTH D1-B and D2 (the PIP grid's presenter), so
# `kind` alone cannot wire D1-B without also capturing D2 — whose tile materiality is an OPEN product question
# (P1, Track B). The median per-frame face count separates them with NO overlap: D1-B is 1 (21 clips) or 2 (4),
# D2 is 4 for all 36. Hence the _LOCK_MAX_FACES <= 2 gate. (D1-A is also 2 but returns FB_WIDE_PAIR first.)
#
# THE ONE S3 INVARIANT: when one subject is persistently dominant and the second participant is intermittent
# or peripheral, the fallback frames the PERSISTENT DOMINANT SUBJECT rather than the empty geometric centre.
# (Spec F5/F2; AC-B1/B2.)
#
# NOT in this slice: active-speaker FOLLOWING (Track B — the lock is ONE fixed anchor for the window, so an
# intermittent second face can never induce a pan) and the D2 PIP grid (S4/S5). AR-1 (the host speaks while
# off-frame) stays an accepted residual of framing the dominant subject without audio.
#
# Fixtures are stats matched to the permanent-evidence distributions, driven directly (no detection, so the
# headless-YuNet fixture trap does not apply). Each face tuple is the detect_window shape (cx,cy,fh,ey,score,fw).
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
    """Stub the framing seams (mirrors test_framing_outcomes._stub). A (events, value) pair lets a strategy
    RECORD then RETURN NORMALLY — which is how the real fail-open strategies conclude a negative."""
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

def _resolve(monkeypatch, cfg, stats):
    _stub(monkeypatch, detect_window=stats, classify_window=framing.CT_MULTI,
          speaker_track=([_FE.NO_TRACK], None), subject_focus=([_FE.NO_FACE], None))
    return framing._resolve(cfg, _Src(), 0.0, 10.0, capture_failures=True)


# Distributions from raw-detections.json. D1-B: dom_cx med 0.352, dom_fh med 0.276, median face-count 1;
# D1-A: face-count 2, symmetric, co-present; D2: face-count 4 (presenter + a 3-tile column).
_HOST_CX = 0.352
_D1B = _stats([[_face(_HOST_CX)] for _ in range(38)] +
              [[_face(_HOST_CX), _face(0.74, fh=0.20, fw=0.12, score=0.88)] for _ in range(22)])
_D1A = _stats([[_face(0.25, fh=0.19, fw=0.13), _face(0.75, fh=0.19, fw=0.13)] for _ in range(44)])
_D2_TILES = [_face(0.85, cy=0.2, fh=0.24, fw=0.14, score=0.9), _face(0.85, cy=0.5, fh=0.24, fw=0.14, score=0.9),
             _face(0.85, cy=0.8, fh=0.24, fw=0.14, score=0.9)]
_D2 = _stats([[_face(0.30, cy=0.5, fh=0.42, fw=0.24, score=0.95)] + _D2_TILES for _ in range(57)])

_SW, _SH = 1920, 1080
_CENTRE_X0 = round((_SW - _SH * 1080 / 1920) / 2)      # 656 px — where the blind 9:16 centre crop starts
_FACE_L = _HOST_CX * _SW - (0.166 * _SW) / 2           # the host's face-box left edge, in source px
_FACE_R = _HOST_CX * _SW + (0.166 * _SW) / 2


def _crop_box(vf):
    """(cw, ch, x, y) from a numeric `crop=w:h:x:y,...` filter string."""
    return [int(v) for v in vf.split("crop=")[1].split(",")[0].split(":")]


# ---- the invariant ---------------------------------------------------------------------------------

def test_d1b_dominant_host_resolves_to_a_subject_lock(monkeypatch, cfg):
    r = _resolve(monkeypatch, cfg, _D1B)
    assert r.final_outcome is _FO.SUBJECT_LOCKED
    assert r.final_strategy is _FS.SUBJECT_LOCK
    assert r.content_type == framing.RENDER_SUBJECT_LOCK

def test_d1b_focus_anchors_on_the_dominant_host_not_the_centre(monkeypatch, cfg):
    r = _resolve(monkeypatch, cfg, _D1B)
    assert r.focus is not None and len(r.focus) == 5
    assert all(isinstance(v, float) for v in r.focus)
    assert abs(r.focus[0] - _HOST_CX) < 0.02          # ON the host, NOT 0.5
    assert abs(r.focus[0] - 0.5) > 0.1

def test_the_blind_centre_crop_cuts_the_hosts_face_this_is_the_defect():
    """FAILING-BEFORE: today's output for this window. The centre crop starts at x=656 while the host's face
    box starts at x=516 — 140px of his face is outside the frame. This is what S3 corrects."""
    before = clip.reframe_filter("9:16", _SW, _SH, focus=None, track=None, content_type=None)
    assert before.startswith("crop=ih*1080/1920:ih,")        # the symbolic blind centre
    assert _CENTRE_X0 > _FACE_L                              # the crop's left edge is INSIDE the face box

def test_subject_lock_contains_the_full_face_box_with_the_host_materially_framed(monkeypatch, cfg):
    """AC-B1: the dominant host's full face is inside the crop and not edge-pinned."""
    r = _resolve(monkeypatch, cfg, _D1B)
    cw, ch, x, y = _crop_box(clip.reframe_filter("9:16", _SW, _SH, focus=r.focus, track=None,
                                                 content_type=r.content_type))
    assert x <= _FACE_L and (x + cw) >= _FACE_R              # the whole face box is inside
    assert abs((x + cw / 2) - _HOST_CX * _SW) < 0.12 * _SW   # and materially centred, not pinned to an edge

def test_subject_lock_zoom_is_gentle_never_a_punch_in(monkeypatch, cfg):
    """AC-B2 / spec F6 / ADR-0103 minimal zoom: the D1-B defect is POSITIONAL, so the crop MOVES onto the host
    rather than magnifying him. The uncapped _ZOOM_MAX path would reach 1.52x here (face at _FACE_FRAC_TALK);
    the lock is capped at _GENTLE_ZOOM_MAX, which is strictly wider."""
    r = _resolve(monkeypatch, cfg, _D1B)
    _, ch, _, _ = _crop_box(clip.reframe_filter("9:16", _SW, _SH, focus=r.focus, track=None,
                                                content_type=r.content_type))
    assert _SH / ch <= clip._GENTLE_ZOOM_MAX + 0.01
    ungated = clip._zoom_h(_SH, _SH, r.focus[2], clip._target_frac(None),
                           zoom_max=clip._adaptive_zoom_max(r.focus[2], clip._ZOOM_MAX))
    assert ch > ungated                                      # strictly WIDER than the emphasis zoom: shows more

def test_subject_lock_is_a_fixed_anchor_never_a_track(monkeypatch, cfg):
    """Active-speaker FOLLOWING is Track B. One anchor for the window means an intermittent second face — the
    22 two-face frames in this fixture — cannot induce a pan."""
    r = _resolve(monkeypatch, cfg, _D1B)
    assert r.track is None
    vf = clip.reframe_filter("9:16", _SW, _SH, focus=r.focus, track=None, content_type=r.content_type)
    assert "if(" not in vf and "lt(" not in vf     # _step_expr's hard-cut t-expression is absent -> a constant crop
    cw, ch, x, y = _crop_box(vf)                   # ... and the box parses as four plain ints


# ---- the guard: the other two defect classes must NOT be captured ----------------------------------

def test_d2_pip_grid_is_not_captured_by_the_subject_lock(monkeypatch, cfg):
    """The D2 PIP grid ALSO yields FB_DOMINANT. Framing its presenter presenter-only would pre-empt P1
    (tile materiality, Track B), so the _LOCK_MAX_FACES gate must keep it centred for S4/S5."""
    r = _resolve(monkeypatch, cfg, _D2)
    assert r.final_outcome is _FO.CENTERED_MULTI_UNTRACKED
    assert r.as_tuple() == (None, None, None)

def test_the_face_count_gate_is_what_saves_d2_not_the_kind(monkeypatch, cfg):
    """Pins WHY the guard exists: the primitive really does classify D2 as FB_DOMINANT, so a naive
    `kind == FB_DOMINANT` wiring would have captured all 36 D2 clips."""
    assert framing.subject_aware_fallback(_D2).kind == framing.FB_DOMINANT
    assert framing._face_count(_D2) > framing._LOCK_MAX_FACES
    assert framing._face_count(_D1B) <= framing._LOCK_MAX_FACES

def test_d1a_wide_two_shot_still_stacks(monkeypatch, cfg):
    """S2 regression: D1-A resolves BEFORE the FB_DOMINANT branch and is unaffected."""
    r = _resolve(monkeypatch, cfg, _D1A)
    assert r.final_outcome is _FO.STACKED_PAIR
    assert r.content_type == framing.RENDER_STACK_PAIR


# ---- blast radius: every pre-existing path is byte-identical ---------------------------------------

def test_ordinary_focus_path_keeps_the_full_zoom_max():
    """zoom_base defaults to _ZOOM_MAX, so a CT_SINGLE/CT_MUSIC focus render is unchanged by S3."""
    f = (0.6, 0.45, 0.30, 0.40, 0.16)
    for ct in (None, framing.CT_SINGLE, framing.CT_MUSIC, framing.CT_MULTI):
        got = clip.reframe_filter("9:16", _SW, _SH, focus=f, track=None, content_type=ct)
        want = clip._focus_crop(f, _SW, _SH, 1080, 1920, _SH, clip._target_frac(ct),
                                symbolic_w="crop=ih*1080/1920:ih:{x}:{y}", symbolic_full=True,
                                zoom_base=clip._ZOOM_MAX)
        assert got == want

def test_far_face_clamp_never_loosens_a_gentler_base():
    """_adaptive_zoom_max's far cap only ever TIGHTENS. Without the min() a far subject under the gentle base
    would zoom 1.25x — MORE than a near one at 1.15x."""
    assert clip._adaptive_zoom_max(0.10, clip._ZOOM_MAX) == clip._ZOOM_MAX_FAR      # unchanged
    assert clip._adaptive_zoom_max(0.30, clip._ZOOM_MAX) == clip._ZOOM_MAX          # unchanged
    assert clip._adaptive_zoom_max(0.10, clip._GENTLE_ZOOM_MAX) == clip._GENTLE_ZOOM_MAX
    assert clip._adaptive_zoom_max(None, clip._GENTLE_ZOOM_MAX) == clip._GENTLE_ZOOM_MAX


# ---- fingerprint / re-render population ------------------------------------------------------------

def _fp(**kw):
    base = dict(src_path="x.mp4", cs=0.0, ce=10.0, aspect_value="9:16", src_w=1920, src_h=1080, ass_text="")
    return clip._render_fingerprint(**{**base, **kw})

def test_only_the_locked_clips_re_render(monkeypatch, cfg):
    """Exactly the D1-B population goes stale: its payload gains a `focus` (was None) + `ct`. A clip that stays
    centred keeps its fingerprint, so no other clip re-renders."""
    r = _resolve(monkeypatch, cfg, _D1B)
    centre = _fp(focus=None, track=None, content_type=None)
    assert _fp(focus=r.focus, track=None, content_type=r.content_type) != centre
    assert _fp(focus=None, track=None, content_type=None) == centre

def test_geom_version_is_not_bumped():
    """A _REFRAME_GEOM_V bump would invalidate EVERY zoomed clip in the corpus. S3 affects one defect
    population, so the version must not move — the new `focus`/`ct` keys are already hashed."""
    assert clip._REFRAME_GEOM_V == 5
