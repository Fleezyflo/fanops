# tests/test_smart_framing.py — Smart framing (subject-aware reframe). The 9:16 crop SLIDES onto the
# detected subject instead of the blind top/center guess: framing.subject_focus returns a normalized
# centroid, clip.reframe_filter turns it into a clamped crop offset, and both render paths thread it
# through ffmpeg_clip_cmd + the render fingerprint. Everything is FAIL-OPEN: no [framing] extra / no
# detection / flag off -> focus=None -> today's centered crop, byte-identical. cv2 is absent in CI, so the
# no-extra path is the live default; the detection path is exercised with stubs.
import json
from pathlib import Path
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, Fmt
from fanops import framing
from fanops.clip import reframe_filter, _render_fingerprint, render_account_cut
from fanops import overlay


# ---------------------------------------------------------------- reframe_filter offset math ----
def test_focus_none_is_byte_identical_to_today():
    # the universal fail-open: focus=None on EVERY branch == the exact crop ffmpeg produced before.
    assert reframe_filter("9:16", 1920, 1080) == "crop=ih*1080/1920:ih,scale=1080:1920,setsar=1"          # width-crop center
    assert reframe_filter("9:16", 1080, 2400) == "crop=iw:iw*1920/1080,scale=1080:1920,setsar=1"          # height-crop center
    assert reframe_filter("9:16", 1080, 2400, top_bias=True) == \
        "crop=iw:iw*1920/1080:0:(ih-iw*1920/1080)/4,scale=1080:1920,setsar=1"                              # top_bias unchanged
    assert reframe_filter("9:16", 1080, 1920) == "scale=1080:1920,setsar=1"                               # scale-only unchanged


def test_width_crop_slides_x_onto_subject():
    # landscape source, subject on the RIGHT (fx=0.8) -> the crop window slides right (x>0), clamped in-bounds.
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.8, 0.5))
    assert vf == "crop=ih*1080/1920:ih:1232:0,scale=1080:1920,setsar=1"     # cw=608, x=clamp(1536-304,0,1312)=1232


def test_height_crop_slides_y_and_overrides_top_bias():
    # tall source, subject LOWER (fy=0.5) -> y offset; focus takes precedence over top_bias.
    vf = reframe_filter("9:16", 1080, 2400, focus=(0.5, 0.5), top_bias=True)
    assert vf == "crop=iw:iw*1920/1080:0:240,scale=1080:1920,setsar=1"      # ch=1920, y=clamp(1200-960,0,480)=240


def test_offset_clamped_in_bounds_never_runs_off_frame():
    # an extreme centroid clamps to the frame edge, never a negative or out-of-range crop origin.
    assert ":1312:0," in reframe_filter("9:16", 1920, 1080, focus=(0.99, 0.5))   # clamped to src_w-cw
    assert ":0:0," in reframe_filter("9:16", 1920, 1080, focus=(0.01, 0.5))      # clamped to 0
    assert ":0:480," in reframe_filter("9:16", 1080, 2400, focus=(0.5, 0.99))    # height clamped to src_h-ch


def test_focus_ignored_on_scale_only_and_unknown_source():
    assert reframe_filter("9:16", 1080, 1920, focus=(0.8, 0.2)) == "scale=1080:1920,setsar=1"   # no crop -> nothing to offset
    assert "pad=" in reframe_filter("9:16", 0, 0, focus=(0.8, 0.2))                              # unknown dims -> pad branch


# ---------------------------------------------------------------- zoom-to-face + eyeline (T5) ----
def _crop_dims(vf):
    # parse "crop=W:H:X:Y" (numeric form) -> (W,H,X,Y) ints
    body = vf.split("crop=", 1)[1].split(",", 1)[0]
    return [int(p) for p in body.split(":")]

def test_legacy_2tuple_focus_is_unchanged_no_zoom():
    # a 2-tuple focus (no face height) must NOT zoom -> byte-identical to the pre-zoom symbolic form.
    assert reframe_filter("9:16", 1920, 1080, focus=(0.8, 0.5)) == "crop=ih*1080/1920:ih:1232:0,scale=1080:1920,setsar=1"

def test_4tuple_focus_zooms_to_target_face_fraction():
    # face fh=0.24 (within the zoom cap) -> crop height SHRINKS so the face fills ~TARGET_FACE_FRAC(0.32).
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.24, 0.40), content_type=framing.CT_SINGLE)
    w, h, x, y = _crop_dims(vf)
    assert h < 1080                                              # zoomed in (crop height below full height)
    assert abs(h - round(1080 * 0.24 / 0.32)) <= 2              # ch = src_h*fh/TARGET_FACE_FRAC(0.32), under the cap
    assert abs(w - round(h * 1080 / 1920)) <= 1                 # crop keeps 9:16
    assert vf.endswith("scale=1080:1920,setsar=1")

def test_zoom_bounded_by_max_so_tiny_face_never_blurs():
    # an extreme tiny face would demand a huge zoom -> clamped to _ZOOM_MAX (crop never smaller than ch0/MAX).
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.02, 0.40), content_type=framing.CT_SINGLE)
    _w, h, _x, _y = _crop_dims(vf)
    from fanops.clip import _ZOOM_MAX
    assert h == round(1080 / _ZOOM_MAX)                         # clamped to the upscale bound, not 0.02-driven

def test_music_uses_wider_zoom_than_talk():
    # music keeps more stage/body context -> a wider crop (taller ch) than talk for the same face.
    talk = _crop_dims(reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.16, 0.40), content_type=framing.CT_SINGLE))
    music = _crop_dims(reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.16, 0.40), content_type=framing.CT_MUSIC))
    assert music[1] > talk[1]                                   # music crop height larger (less zoom)

def test_eyeline_places_eyes_in_upper_portion():
    # eye-line ey -> crop top so the eyes sit at ~EYELINE_FRAC of the frame (not centered).
    from fanops.clip import _EYELINE_FRAC
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.5, 0.16, 0.30), content_type=framing.CT_SINGLE)
    _w, h, _x, y = _crop_dims(vf)
    assert y == max(0, round(0.30 * 1080 - _EYELINE_FRAC * h))  # eyes anchored, not face-centered

def test_already_9x16_passthrough_when_face_well_sized():
    # a normal vertical with a normal face -> scale-only (NO destructive crop), byte-identical to today.
    assert reframe_filter("9:16", 1080, 1920, focus=(0.5, 0.45, 0.30, 0.40)) == "scale=1080:1920,setsar=1"

def test_already_9x16_gentle_zoom_when_face_tiny():
    # a vertical where the face is TINY -> a bounded gentle zoom-in (still 9:16), never worse than passthrough.
    from fanops.clip import _GENTLE_ZOOM_MAX
    vf = reframe_filter("9:16", 1080, 1920, focus=(0.5, 0.45, 0.05, 0.40), content_type=framing.CT_SINGLE)
    assert "crop=" in vf                                        # gentle crop applied
    _w, h, _x, _y = _crop_dims(vf)
    assert h >= round(1920 / _GENTLE_ZOOM_MAX)                  # zoom bounded (never more than the gentle cap)

def test_square_source_to_9x16_is_width_crop_zoom():
    # 1:1 -> 9:16: src_ar(1.0) > tgt_ar(0.5625) -> width-crop branch, zoom applies.
    vf = reframe_filter("9:16", 1080, 1080, focus=(0.5, 0.45, 0.16, 0.40), content_type=framing.CT_SINGLE)
    w, h, _x, _y = _crop_dims(vf)
    assert abs(w - round(h * 1080 / 1920)) <= 1 and h <= 1080

def test_portrait_non_9x16_to_9x16_is_height_crop_zoom():
    # 1080x1350 (4:5, src_ar 0.8 < tgt 0.5625? no: 0.8>0.5625 -> width-crop). Use 1080x2000 (0.54<0.5625) -> height-crop.
    vf = reframe_filter("9:16", 1080, 2000, focus=(0.5, 0.45, 0.16, 0.40), content_type=framing.CT_SINGLE)
    w, h, _x, _y = _crop_dims(vf)
    assert w <= 1080 and h <= 2000 and abs(w - round(h * 1080 / 1920)) <= 1


# ---------------------------------------------------------------- render fingerprint ----
def test_fingerprint_focus_is_additive():
    base = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "")
    with_focus = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", focus=(0.8, 0.5))
    none_focus = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", focus=None)
    assert none_focus == base                       # absent focus -> fingerprint UNCHANGED (existing clips stay valid)
    assert with_focus != base                       # a focus changes the fp -> a re-detect can't reuse a stale crop
    other = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", focus=(0.2, 0.5))
    assert other != with_focus                      # a DIFFERENT focus -> a different fp


# ---------------------------------------------------------------- Config.smart_framing flag ----
def test_smart_framing_defaults_on(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_SMART_FRAMING", raising=False)
    assert Config(root=tmp_path).smart_framing is True

@pytest.mark.parametrize("val,expected", [("0", False), ("false", False), ("no", False), ("off", False),
                                          ("1", True), ("", True), ("yes", True)])
def test_smart_framing_off_words(tmp_path, monkeypatch, val, expected):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", val)
    assert Config(root=tmp_path).smart_framing is expected


# ---------------------------------------------------------------- subject_focus (fail-open + cache) ----
def test_subject_focus_no_extra_is_none(tmp_path, monkeypatch):
    # cv2 absent (the CI default) -> None, and NO sidecar probe blows up. None is the fail-open signal.
    monkeypatch.setattr(framing, "_cv2", lambda: None)
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path=str(tmp_path / "s1.mp4"), width=1920, height=1080, duration=60.0)
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) is None

def test_subject_focus_non_positive_window_is_none(tmp_path):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080)
    assert framing.subject_focus(cfg, src, start=5.0, end=5.0) is None

def test_subject_focus_returns_median_quad(tmp_path, monkeypatch):
    # NEW shape (fx,fy,fh,ey): the dominant (largest-fh) face's median over the window, read from detect stats.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.8, 0.4, 0.2, 0.36]]] * 4 + [[]]}        # 4 of 5 frames have a face
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: stats)
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) == (0.8, 0.4, 0.2, 0.36)

def test_subject_focus_picks_dominant_largest_face(tmp_path, monkeypatch):
    # two faces per frame -> the LARGER (fh) one is the subject for the static lock.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.2, 0.5, 0.10, 0.45], [0.8, 0.5, 0.30, 0.40]]] * 4}
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: stats)
    fx, fy, fh, ey = framing.subject_focus(cfg, src, start=10.0, end=14.0)
    assert fx == 0.8 and fh == 0.30                                             # the bigger face wins

def test_subject_focus_low_confidence_is_none(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.8, 0.4, 0.2, 0.36]]] + [[]] * 4}        # 1 of 5 -> conf 0.2 < 0.34
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: stats)
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) is None

def test_subject_focus_no_detection_is_none(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: None)         # fail-open -> None
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) is None


# ---------------------------------------------------------------- YuNet detector (v2) ----
def test_vendored_yunet_model_ships_in_package():
    # the detector is useless without its model; assert the vendored asset is present + non-trivial.
    mp = framing._model_path()
    assert mp.exists() and mp.suffix == ".onnx" and mp.stat().st_size > 100_000

def test_detector_none_when_model_absent(monkeypatch, tmp_path):
    # model asset missing -> _detector None -> detect_window None -> center crop (fail-open), never raises.
    monkeypatch.setattr(framing, "_model_path", lambda: tmp_path / "absent.onnx")
    assert framing._detector(object()) is None

def test_detector_none_on_old_cv2_without_yunet(monkeypatch, tmp_path):
    # an OpenCV too old to expose FaceDetectorYN -> None, not an AttributeError crash.
    monkeypatch.setattr(framing, "_model_path", lambda: tmp_path / "m.onnx")
    (tmp_path / "m.onnx").write_bytes(b"x" * 200_000)
    class OldCv2: pass                                    # no FaceDetectorYN attribute
    assert framing._detector(OldCv2()) is None

def test_track_sidecar_stale_version_invalidated(tmp_path):
    # an older track sidecar (pre face-height/eyeline schema) must NOT be trusted -> recompute.
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"v": framing._SIDECAR_V - 1, "windows": {"10.0-14.0": [[0.0, 5.0, 0.5, 0.5]]}}))
    assert framing._load_cache(p) == {}                   # version mismatch -> empty -> re-probe

def test_detect_window_samples_at_detection_resolution(tmp_path, monkeypatch):
    # faces are undetectable at the 480px hook-author default; the grid pass must request higher-res frames.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    seen = {}
    def _grid(*a, **k):
        seen["width"] = k.get("width"); return ["g0", "g1"]
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", _grid)
    monkeypatch.setattr(framing, "_detect_faces", lambda cv2, det, fp: [(0.5, 0.5, 0.2, 0.45)])
    framing.detect_window(cfg, src, start=10.0, end=14.0)
    assert seen["width"] == framing._KF_WIDTH and framing._KF_WIDTH >= 960


# ---------------------------------------------------------------- active-speaker track (time-varying crop) ----
def test_lerp_expr_is_a_smooth_ramp_not_a_step():
    from fanops.clip import _lerp_expr
    assert _lerp_expr([], [400], ramp=0.4) == "400"                         # single value -> constant
    expr = _lerp_expr([5.0], [118, 1232], ramp=0.4)
    assert "clip((t-4.6)/0.4\\,0\\,1)" in expr                             # linear ramp ENDING at the switch (5.0-0.4)
    assert expr.startswith("118+") and "(1114)" in expr                   # base + (delta=1232-118) * ramp
    assert "if(lt(" not in expr                                            # NOT the old hard step

def test_reframe_track_smooth_zoomed_pan():
    # 6-tuple track (with face-height+eyeline): zoomed crop (constant w/h) + smooth x ramp between speakers.
    track = [(0.0, 5.0, 0.22, 0.5, 0.18, 0.42), (5.0, 10.0, 0.80, 0.45, 0.18, 0.40)]
    vf = reframe_filter("9:16", 1920, 1080, track=track, content_type=framing.CT_MULTI)
    assert vf.startswith("crop=w=") and "x=" in vf and "clip((t-" in vf    # constant w/h, smooth time-varying x
    assert "if(lt(t" not in vf                                             # no hard teleport
    assert vf.endswith("scale=1080:1920,setsar=1")

def test_reframe_track_overrides_static_focus():
    track = [(0.0, 5.0, 0.22, 0.5, 0.18, 0.42), (5.0, 10.0, 0.80, 0.45, 0.18, 0.40)]
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.5, 0.2, 0.4), track=track, content_type=framing.CT_MULTI)
    assert "clip((t-" in vf                                                # the dynamic track wins over a static focus

def test_reframe_track_none_is_today():
    # no track -> unchanged: identical to the focus/centered paths (single-subject clips never change).
    assert reframe_filter("9:16", 1920, 1080, track=None) == "crop=ih*1080/1920:ih,scale=1080:1920,setsar=1"

def test_fingerprint_track_is_additive():
    base = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "")
    tr = [(0.0, 2.0, 0.22, 0.5), (2.0, 5.0, 0.80, 0.45)]
    with_tr = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", track=tr)
    assert with_tr != base                                                 # a track -> re-render
    assert _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", track=None) == base

def test_speaker_track_no_extra_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(framing, "_cv2", lambda: None)                     # cv2 absent (CI) -> static path
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    assert framing.speaker_track(cfg, src, start=10.0, end=30.0, src_w=1920, src_h=1080) is None

def _obs(loud_side, fhL=0.2, fhR=0.18):
    # one frame's observation: each side -> ((fx,fy,fh,ey), mouth-motion). loud_side gets high motion.
    L = ((0.22, 0.50, fhL, 0.45), 50.0 if loud_side == "L" else 5.0)
    R = ((0.80, 0.45, fhR, 0.40), 50.0 if loud_side == "R" else 5.0)
    return {"L": L, "R": R}

def test_speaker_track_follows_active_speaker(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: [f"g{i}" for i in range(40)])
    obs = [_obs("L")] * 20 + [_obs("R")] * 20                               # 40 frames @4fps = 10s; LEFT then RIGHT
    monkeypatch.setattr(framing, "_track_observe", lambda cv2, det, frames: obs)
    tr = framing.speaker_track(cfg, src, start=0.0, end=10.0, src_w=1920, src_h=1080)
    assert tr is not None and len(tr) == 2                                  # merged into LEFT-then-RIGHT
    assert len(tr[0]) == 6                                                  # 6-tuple: t0,t1,fx,fy,fh,ey
    assert abs(tr[0][2] - 0.22) < 0.01 and abs(tr[1][2] - 0.80) < 0.01      # fx follows the speaker
    assert tr[0][4] == 0.2 and tr[1][4] == 0.18                            # face HEIGHT carried per segment (for zoom)
    assert tr[0][0] == 0.0 and tr[-1][1] == 10.0                            # covers the whole window

def test_speaker_track_switch_is_responsive(tmp_path, monkeypatch):
    # the switch lands ~1s after the real change (frame 20 = 5.0s), NOT the old ~4s lag.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: [f"g{i}" for i in range(40)])
    monkeypatch.setattr(framing, "_track_observe", lambda cv2, det, frames: [_obs("L")] * 20 + [_obs("R")] * 20)
    tr = framing.speaker_track(cfg, src, start=0.0, end=10.0, src_w=1920, src_h=1080)
    assert 5.0 <= tr[0][1] <= 6.0                                           # boundary within ~1s of the real switch

def test_speaker_track_one_frame_blip_does_not_flip(tmp_path, monkeypatch):
    # a single louder-RIGHT frame inside an all-LEFT window must NOT cause a cut (hysteresis dwell).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: [f"g{i}" for i in range(20)])
    obs = [_obs("L")] * 20; obs[10] = _obs("R")                             # one blip
    monkeypatch.setattr(framing, "_track_observe", lambda cv2, det, frames: obs)
    assert framing.speaker_track(cfg, src, start=0.0, end=5.0, src_w=1920, src_h=1080) is None   # 1 position -> None

def test_speaker_track_one_dominant_face_is_none(tmp_path, monkeypatch):
    # both visible but the SAME person always talks -> one position -> None (static focus is identical).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: [f"g{i}" for i in range(20)])
    monkeypatch.setattr(framing, "_track_observe", lambda cv2, det, frames: [_obs("L")] * 20)
    assert framing.speaker_track(cfg, src, start=0.0, end=5.0, src_w=1920, src_h=1080) is None


# ---------------------------------------------------------------- motion_saliency (no-face follow) ----
def test_motion_saliency_returns_change_centroid(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: ["g0", "g1", "g2"])
    monkeypatch.setattr(framing, "_saliency_centroid", lambda cv2, frames: (0.7, 0.4))
    assert framing.motion_saliency(cfg, src, start=10.0, end=14.0) == (0.7, 0.4)

def test_motion_saliency_no_cv2_is_none(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: None)
    assert framing.motion_saliency(cfg, src, start=10.0, end=14.0) is None


# ---------------------------------------------------------------- detect_window (single grid pass) ----
def test_detect_window_builds_per_frame_face_stats(tmp_path, monkeypatch):
    # ONE grid pass -> per-frame list of [cx,cy,fh,ey] faces, cached to a .detect.json sidecar.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    faces = {"g0": [(0.25, 0.50, 0.20, 0.45)],
             "g1": [(0.25, 0.50, 0.20, 0.45), (0.78, 0.45, 0.18, 0.40)]}
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: ["g0", "g1"])
    monkeypatch.setattr(framing, "_detect_faces", lambda cv2, det, fp: faces[fp])
    st = framing.detect_window(cfg, src, start=10.0, end=14.0)
    assert st is not None
    assert st["frames"] == [[[0.25, 0.5, 0.2, 0.45]],
                            [[0.25, 0.5, 0.2, 0.45], [0.78, 0.45, 0.18, 0.4]]]
    assert st["fps"] == framing._DETECT_FPS
    sidecar = cfg.agent_io / "framing" / "s1.detect.json"
    assert sidecar.exists() and json.loads(sidecar.read_text())["v"] == framing._DETECT_V

def test_detect_window_no_cv2_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(framing, "_cv2", lambda: None)             # extra absent -> None (fail-open)
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    assert framing.detect_window(cfg, src, start=10.0, end=14.0) is None

def test_detect_window_empty_grid_is_none(tmp_path, monkeypatch):
    # ffmpeg gave no frames -> None (fail-open to center crop), never a crash.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: [])
    assert framing.detect_window(cfg, src, start=10.0, end=14.0) is None

def test_detect_window_caches_and_skips_reprobe(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    calls = {"n": 0}
    def _grid(*a, **k):
        calls["n"] += 1; return ["g0"]
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", _grid)
    monkeypatch.setattr(framing, "_detect_faces", lambda cv2, det, fp: [(0.5, 0.5, 0.2, 0.45)])
    a = framing.detect_window(cfg, src, start=10.0, end=14.0)
    b = framing.detect_window(cfg, src, start=10.0, end=14.0)        # cache hit -> no second grid
    assert a == b and calls["n"] == 1

def test_detect_sidecar_version_invalidated(tmp_path):
    p = tmp_path / "old.detect.json"
    p.write_text(json.dumps({"v": framing._DETECT_V - 1, "windows": {"10.0-14.0": {"frames": []}}}))
    assert framing._load_detect_cache(p) == {}                       # stale version -> recompute


# ---------------------------------------------------------------- classify_window (content type) ----
def _stats(faces_per_frame):
    # faces_per_frame: list of per-frame face lists, each face [cx,cy,fh,ey]
    return {"fps": 4.0, "frames": faces_per_frame}

def _talk_src(**kw):
    base = dict(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    base.update(kw); return Source(**base)

def test_classify_multi_speaker_talk():
    # >=2 stable faces + real speech in the window -> the ONLY content type that switches speakers.
    src = _talk_src(transcript=[{"start": 10.0, "end": 13.5, "text": "so tell me about your new record"}])
    st = _stats([[[0.25, 0.5, 0.2, 0.45], [0.78, 0.45, 0.18, 0.4]]] * 4)
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=st) == framing.CT_MULTI

def test_classify_single_speaker_talk():
    src = _talk_src(transcript=[{"start": 10.0, "end": 13.5, "text": "let me explain how this works"}])
    st = _stats([[[0.5, 0.5, 0.22, 0.45]]] * 4)
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=st) == framing.CT_SINGLE

def test_classify_music_when_vocals_but_no_speech():
    # face present, NO recognized speech in window, demucs produced a vocal stem -> music (wider lock, no flicker).
    src = _talk_src(transcript=[], meta={"vocals_isolated": True})
    st = _stats([[[0.5, 0.5, 0.3, 0.45]]] * 4)
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=st) == framing.CT_MUSIC

def test_classify_silent_when_no_speech_no_vocals():
    src = _talk_src(transcript=[], meta={})
    st = _stats([[[0.5, 0.5, 0.3, 0.45]]] * 4)
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=st) == framing.CT_SILENT

def test_classify_no_people_when_no_faces():
    src = _talk_src(transcript=[{"start": 10.0, "end": 13.0, "text": "music plays over a city skyline"}])
    st = _stats([[], [], [], []])                                  # frames with no faces
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=st) == framing.CT_NOPEOPLE

def test_classify_old_source_without_meta_does_not_crash():
    src = _talk_src(transcript=None)                               # untranscribed, meta default
    st = _stats([[[0.5, 0.5, 0.3, 0.45]]] * 4)
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=st) == framing.CT_SILENT

def test_classify_stats_none_is_no_people():
    # detection unavailable -> no face data -> no-people (caller fails open to centered crop regardless).
    src = _talk_src(transcript=[{"start": 10.0, "end": 13.0, "text": "hello there friend"}])
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=None) == framing.CT_NOPEOPLE


# ---------------------------------------------------------------- render path threading ----
def _src_moment(cfg, *, start=10, end=14, dur=120.0):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=dur))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=start, end=end, reason="r", state=MomentState.clipped))
    return led

def _capturing_run(captured):
    def run(cmd, **kw):
        if not str(cmd[-1]).startswith("-"):
            captured["cmd"] = cmd
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CUT")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    return run

def _vf_of(cmd):
    return cmd[cmd.index("-vf") + 1]

def test_account_cut_applies_detected_focus(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: False)        # isolate the reframe (no subs chain)
    monkeypatch.setattr(framing, "subject_focus", lambda *a, **k: (0.8, 0.5))   # a detected subject on the right
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    ok, _ = render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="talk",
                               hook="", out_path=str(cfg.clips / "acct.mp4"))
    assert ok and ":1232:0," in _vf_of(captured["cmd"])                         # the x-offset reached the ffmpeg -vf

def test_account_cut_off_flag_is_centered(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")                             # flag OFF -> focus never resolved
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: False)
    # subject_focus would return a focus, but the off flag must short-circuit it -> centered crop (today).
    monkeypatch.setattr(framing, "subject_focus", lambda *a, **k: (0.8, 0.5))
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    ok, _ = render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="talk",
                               hook="", out_path=str(cfg.clips / "acct.mp4"))
    assert ok and _vf_of(captured["cmd"]) == "crop=ih*1080/1920:ih,scale=1080:1920,setsar=1"   # centered, no offset
