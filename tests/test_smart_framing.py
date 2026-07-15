# tests/test_smart_framing.py — Smart framing (subject-aware reframe). The 9:16 crop SLIDES onto the
# detected subject instead of the blind top/center guess: framing.subject_focus returns a normalized
# centroid, clip.reframe_filter turns it into a clamped crop offset, and both render paths thread it
# through ffmpeg_clip_cmd + the render fingerprint. Detection MISSES are FAIL-OPEN: a stub/flag returning
# None -> focus=None -> today's centered crop, byte-identical. But the cv2 DEPENDENCY is now REQUIRED when
# smart_framing is ON: with the extra ABSENT + smart_framing ON, _resolve_framing REFUSES (ToolchainMissingError)
# rather than silently centre-crop (see the require_cv2 raise-tests below). cv2 is absent in the hermetic unit
# job, so router tests stub the DETECTION functions (detect_window/speaker_track/subject_focus); the real
# require_cv2 runtime builds a real detector (cv2 is installed in the unit lane) and the stubbed detection drives the router.
import json, re, shutil, subprocess, types
from pathlib import Path
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, Fmt
from fanops import framing
from fanops.clip import (reframe_filter, _render_fingerprint, render_account_cut,
                         _segments_filter_complex, ffmpeg_segments_cmd, render_reframed, _ch0_for)
import fanops.clip as clipmod
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
    # face fh=0.30 (within the zoom cap at the 0.42 target) -> crop height SHRINKS so the face fills the target.
    from fanops.clip import _FACE_FRAC_TALK
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.30, 0.40), content_type=framing.CT_SINGLE)
    w, h, x, y = _crop_dims(vf)
    assert h < 1080                                              # zoomed in (crop height below full height)
    assert abs(h - round(1080 * 0.30 / _FACE_FRAC_TALK)) <= 2   # ch = src_h*fh/_FACE_FRAC_TALK, under the cap
    assert abs(w - round(h * 1080 / 1920)) <= 1                 # crop keeps 9:16
    assert vf.endswith("scale=1080:1920,setsar=1")

def test_zoom_bounded_by_max_so_tiny_face_never_blurs():
    # an extreme tiny face is FAR -> held WIDE (clamped by _ZOOM_MAX_FAR), never an unbounded upscale-blur punch-in.
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.02, 0.40), content_type=framing.CT_SINGLE)
    _w, h, _x, _y = _crop_dims(vf)
    assert h == round(1080 / clipmod._ZOOM_MAX_FAR)            # clamped to the far/wide cap, not 0.02-driven

def test_music_uses_wider_zoom_than_talk():
    # music keeps more stage/body context -> a wider crop (taller ch) than talk for the same face.
    talk = _crop_dims(reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.20, 0.40), content_type=framing.CT_SINGLE))
    music = _crop_dims(reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.20, 0.40), content_type=framing.CT_MUSIC))
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

def test_fingerprint_face_height_and_content_type_bust():
    # adding face-height/eyeline (zoom) or changing content_type changes the bytes -> must re-render once.
    base2 = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", focus=(0.5, 0.5))
    quad = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", focus=(0.5, 0.5, 0.2, 0.4), content_type="single-speaker-talk")
    assert quad != base2                             # a sized/eyelined focus -> new fp (zoom changes the pixels)
    music = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", focus=(0.5, 0.5, 0.2, 0.4), content_type="music")
    assert music != quad                            # music zooms wider -> different bytes -> different fp
    # a 2-tuple focus with no content_type stays byte-identical to the pre-zoom fingerprint (no needless re-render)
    assert _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", focus=(0.5, 0.5), content_type=None) == base2


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
    # shape (fx,fy,fh,ey,fw): the dominant (largest-fh) face's median over the window. Legacy 4-tuple stats
    # carry no width, so fw is None (the clip geometry then falls back to today's centering on that axis).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.8, 0.4, 0.2, 0.36]]] * 4 + [[]]}        # 4 of 5 frames have a face
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: stats)
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) == (0.8, 0.4, 0.2, 0.36, None)

def test_subject_focus_picks_dominant_largest_face(tmp_path, monkeypatch):
    # two faces per frame -> the LARGER (fh) one is the subject for the static lock.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.2, 0.5, 0.10, 0.45], [0.8, 0.5, 0.30, 0.40]]] * 4}
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: stats)
    fx, fy, fh, ey, fw = framing.subject_focus(cfg, src, start=10.0, end=14.0)
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
def test_step_expr_is_a_hard_cut_not_a_pan():
    from fanops.clip import _step_expr
    assert _step_expr([], [400]) == "400"                                   # single value -> constant
    expr = _step_expr([5.0], [118, 1232])
    assert expr == "if(lt(t\\,5.0)\\,118\\,1232)"                          # INSTANT cut at the switch time
    assert "clip(" not in expr                                             # NOT a slow pan across the gap

def test_reframe_track_hard_cut_zoomed():
    # 6-tuple track (face-height+eyeline): zoomed crop (constant w/h) + a HARD CUT x between speakers
    # (no pan across the empty middle of a 2-shot — proven on real footage to read as a glitch).
    track = [(0.0, 5.0, 0.22, 0.5, 0.18, 0.42), (5.0, 10.0, 0.80, 0.45, 0.18, 0.40)]
    vf = reframe_filter("9:16", 1920, 1080, track=track, content_type=framing.CT_MULTI)
    assert vf.startswith("crop=w=") and "x=if(lt(t\\,5.0)\\," in vf        # constant w/h, instant cut at the switch
    assert "clip((t-" not in vf                                            # no slow pan
    h = int(vf.split("crop=w=", 1)[1].split(":")[1].split(":")[0].replace("h=", ""))
    assert h < 1080                                                        # zoomed (not full-height blind crop)
    assert vf.endswith("scale=1080:1920,setsar=1")

def test_reframe_track_overrides_static_focus():
    track = [(0.0, 5.0, 0.22, 0.5, 0.18, 0.42), (5.0, 10.0, 0.80, 0.45, 0.18, 0.40)]
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.5, 0.2, 0.4), track=track, content_type=framing.CT_MULTI)
    assert "if(lt(t\\," in vf                                              # the dynamic track wins over a static focus

def test_reframe_track_none_is_today():
    # no track -> unchanged: identical to the focus/centered paths (single-subject clips never change).
    assert reframe_filter("9:16", 1920, 1080, track=None) == "crop=ih*1080/1920:ih,scale=1080:1920,setsar=1"

def test_fingerprint_track_is_additive():
    base = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "")
    tr = [(0.0, 2.0, 0.22, 0.5), (2.0, 5.0, 0.80, 0.45)]
    with_tr = _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", track=tr)
    assert with_tr != base                                                 # a track -> re-render
    assert _render_fingerprint("s.mp4", 0.0, 5.0, "9:16", 1920, 1080, "", track=None) == base


# ---------------------------------------------------------------- per-segment concat render (random-sizes fix) ----
def test_segments_filter_complex_sizes_each_speaker_independently():
    # The core fix: a 2-shot whose two speakers differ in source face-size must get DIFFERENT crop heights,
    # so each lands at a consistent on-screen size (one ffmpeg crop can't — it sets w/h once per stream).
    track = [(0.0, 5.0, 0.80, 0.40, 0.30, 0.40), (5.0, 10.0, 0.22, 0.45, 0.20, 0.45)]  # both NEAR, different sizes
    fc = _segments_filter_complex(track, 1920, 1080, "9:16", framing.CT_MULTI)
    chains = [c for c in fc.split(";") if c.startswith("[0:v]") or c.startswith("[1:v]")]
    assert len(chains) == 2
    h0 = int(re.search(r"crop=\d+:(\d+):", chains[0]).group(1))
    h1 = int(re.search(r"crop=\d+:(\d+):", chains[1]).group(1))
    assert h0 != h1                                          # different zoom per speaker (each sized to itself)
    assert h0 > h1                                           # bigger near face (0.30) needs LESS zoom -> TALLER crop than 0.20
    assert "concat=n=2:v=1:a=1[vout][aout]" in fc           # video+audio concatenated, mapped out

def test_segments_filter_complex_threads_subtitles():
    track = [(0.0, 3.0, 0.30, 0.4, 0.2, 0.4), (3.0, 6.0, 0.70, 0.4, 0.2, 0.4)]
    fc = _segments_filter_complex(track, 1920, 1080, "9:16", framing.CT_MULTI, sub_token="subtitles='x.ass'")
    assert "concat=n=2:v=1:a=1[vc][aout]" in fc             # concat -> [vc], then subs -> [vout]
    assert fc.strip().endswith("[vc]subtitles='x.ass'[vout]")

def test_ffmpeg_segments_cmd_one_seeked_input_per_segment():
    track = [(0.0, 2.0, 0.3, 0.4, 0.2, 0.4), (2.0, 5.0, 0.7, 0.4, 0.2, 0.4), (5.0, 8.0, 0.3, 0.4, 0.2, 0.4)]
    cmd = ffmpeg_segments_cmd("src.mp4", "out.mp4", 100.0, 108.0, "9:16", track, src_w=1920, src_h=1080)
    sss = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-ss"]
    assert sss == ["100.000", "102.000", "105.000"]         # absolute seek = clip start + each segment's t0
    ts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-t"]
    assert ts == ["2.000", "3.000", "3.000"]                # each input limited to its segment duration
    assert cmd.count("-i") == 3 and "-filter_complex" in cmd
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"] == ["[vout]", "[aout]"]


def test_ffmpeg_segments_cmd_forces_cfr_so_concat_gaps_dont_drop_fps():
    # E4: the concat filter leaves a 1-frame PTS gap per join -> VFR output whose avg_frame_rate sags below
    # the source (29.835 vs 29.97) and whose burned subtitles drift. `-fps_mode cfr` resamples to a constant
    # grid. It MUST sit in the OUTPUT options (after the maps), not among the per-input `-ss/-t/-i` flags.
    track = [(0.0, 2.0, 0.3, 0.4, 0.2, 0.4), (2.0, 5.0, 0.7, 0.4, 0.2, 0.4), (5.0, 8.0, 0.3, 0.4, 0.2, 0.4)]
    cmd = ffmpeg_segments_cmd("src.mp4", "out.mp4", 100.0, 108.0, "9:16", track, src_w=1920, src_h=1080)
    assert "-fps_mode" in cmd and cmd[cmd.index("-fps_mode") + 1] == "cfr"
    assert cmd.index("-fps_mode") > cmd.index("-filter_complex")   # an output option, after the last input

def test_ch0_for_routes_by_aspect():
    assert _ch0_for("9:16", 1920, 1080) == 1080             # wide source -> width-crop -> full height baseline
    assert _ch0_for("9:16", 1080, 2000) == round(1080 * 1920 / 1080)  # tall source -> height-crop baseline
    assert _ch0_for("9:16", 1080, 1920) is None             # already 9:16 -> segment scale-only
    assert _ch0_for("9:16", 0, 0) is None                   # unknown dims -> scale-only (fail-open)

def test_render_reframed_uses_segments_for_a_track(monkeypatch):
    seen = {}
    def fake_run(cmd, **k):
        seen["cmd"] = cmd
        from pathlib import Path as _P; _P(cmd[-1]).write_bytes(b"x")   # pretend ffmpeg wrote the file
        return types.SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr("fanops.clip.subprocess.run", fake_run)
    track = [(0.0, 3.0, 0.3, 0.4, 0.2, 0.4), (3.0, 6.0, 0.7, 0.4, 0.2, 0.4)]
    out = str(_tmp_out := __import__("tempfile").mktemp(suffix=".mp4"))
    r = render_reframed("src.mp4", out, 0.0, 6.0, "9:16", src_w=1920, src_h=1080, track=track, content_type=framing.CT_MULTI)
    assert r.returncode == 0
    assert "-filter_complex" in seen["cmd"]                 # took the per-segment concat path, not single-pass crop

def test_render_reframed_single_pass_without_track(monkeypatch):
    seen = {}
    def fake_run(cmd, **k):
        seen["cmd"] = cmd
        from pathlib import Path as _P; _P(cmd[-1]).write_bytes(b"x")
        return types.SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr("fanops.clip.subprocess.run", fake_run)
    out = str(__import__("tempfile").mktemp(suffix=".mp4"))
    render_reframed("src.mp4", out, 0.0, 6.0, "9:16", src_w=1920, src_h=1080, focus=(0.5, 0.45, 0.3, 0.4),
                    content_type=framing.CT_SINGLE)
    assert "-filter_complex" not in seen["cmd"] and "-vf" in seen["cmd"]   # single-pass crop, not concat

def test_render_reframed_falls_back_when_segments_rejected(monkeypatch):
    calls = []
    def fake_run(cmd, **k):
        calls.append(cmd)
        from pathlib import Path as _P
        if "-filter_complex" in cmd:                        # segment graph rejected by a working ffmpeg
            return types.SimpleNamespace(returncode=1, stderr="bad filter")
        _P(cmd[-1]).write_bytes(b"x")                       # single-pass fallback succeeds
        return types.SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr("fanops.clip.subprocess.run", fake_run)
    out = str(__import__("tempfile").mktemp(suffix=".mp4"))
    track = [(0.0, 3.0, 0.3, 0.4, 0.2, 0.4), (3.0, 6.0, 0.7, 0.4, 0.2, 0.4)]
    r = render_reframed("src.mp4", out, 0.0, 6.0, "9:16", src_w=1920, src_h=1080, track=track, content_type=framing.CT_MULTI)
    assert r.returncode == 0                                 # fell back to single-pass and succeeded (fail-open)
    assert len(calls) == 2 and "-filter_complex" in calls[0] and "-vf" in calls[1]


# ---------------------------------------------------------------- stable render: static crop + adaptive far zoom ----
def test_far_face_held_wide_not_punched_into_mic():
    # a FAR/small face (< _SMALL_FACE_FRAC) is held WIDE (contextual) — a near face of the SAME source size band
    # would punch in tighter. Proves the adaptive cap keeps a far/occluded speaker out of a tight mic crop.
    far = _crop_dims(reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.14, 0.40), content_type=framing.CT_SINGLE))
    near = _crop_dims(reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.45, 0.30, 0.40), content_type=framing.CT_SINGLE))
    # far crop height is clamped by the FAR cap (a wide shot): ch >= ch0 / _ZOOM_MAX_FAR
    assert far[1] >= round(1080 / clipmod._ZOOM_MAX_FAR) - 1
    assert far[1] > near[1]                                  # the far subject's crop is WIDER (less zoom) than the near punch-in

def test_segment_chain_far_speaker_held_wide():
    # in a 2-shot, the far speaker's segment crop must be WIDER (less zoom) than the near speaker's.
    track = [(0.0, 5.0, 0.80, 0.4, 0.30, 0.40), (5.0, 10.0, 0.22, 0.45, 0.13, 0.45)]  # near (0.30) then far (0.13)
    fc = _segments_filter_complex(track, 1920, 1080, "9:16", framing.CT_MULTI)
    chains = [c for c in fc.split(";") if c.startswith("[0:v]") or c.startswith("[1:v]")]
    h_near = int(re.search(r"crop=\d+:(\d+):", chains[0]).group(1))
    h_far = int(re.search(r"crop=\d+:(\d+):", chains[1]).group(1))
    assert h_far > h_near                                    # far speaker held wider (context), near punches in

def test_merge_brief_segments_absorbs_interjections():
    # a brief shot (< _ASD_MIN_SEG_S) must be absorbed -> no cut-away-and-back (rapid cuts read as jitter).
    segs = [[0.0, 5.0, 0.25, 0.4, 0.2, 0.4], [5.0, 5.6, 0.80, 0.4, 0.2, 0.4], [5.6, 12.0, 0.25, 0.4, 0.2, 0.4]]
    out = framing._merge_brief_segments(segs)
    assert len(out) == 1                                     # the 0.6s interjection vanishes -> one stable shot
    assert out[0][0] == 0.0 and out[0][1] == 12.0

def test_merge_brief_segments_keeps_real_turns():
    segs = [[0.0, 5.0, 0.25, 0.4, 0.2, 0.4], [5.0, 12.0, 0.80, 0.4, 0.2, 0.4]]   # two real turns
    out = framing._merge_brief_segments(segs)
    assert len(out) == 2                                     # both shots long enough -> the cut is kept

def test_render_reframed_static_no_perframe_symbol():
    # the per-frame renderer is GONE: render_reframed must not reference it (no jitter path can be constructed).
    assert not hasattr(clipmod, "_render_perframe")


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

def _grid(n):
    return lambda *a, **k: [f"g{i}" for i in range(n)]

def test_speaker_track_follows_active_speaker(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    half = round(5.0 * framing._ASD_FPS)                                    # 5s LEFT then 5s RIGHT at the real ASD fps
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", _grid(half * 2))
    obs = [_obs("L")] * half + [_obs("R")] * half
    monkeypatch.setattr(framing, "_track_observe", lambda cv2, det, frames: obs)
    tr = framing.speaker_track(cfg, src, start=0.0, end=10.0, src_w=1920, src_h=1080)
    assert tr is not None and len(tr) == 2                                  # merged into LEFT-then-RIGHT
    assert len(tr[0]) == 6                                                  # 6-tuple: t0,t1,fx,fy,fh,ey
    assert abs(tr[0][2] - 0.22) < 0.01 and abs(tr[1][2] - 0.80) < 0.01      # fx follows the speaker
    assert tr[0][4] == 0.2 and tr[1][4] == 0.18                            # face HEIGHT (p75) carried per segment (for zoom)
    assert tr[0][0] == 0.0 and tr[-1][1] == 10.0                            # covers the whole window

def test_speaker_track_switch_is_responsive(tmp_path, monkeypatch):
    # the committed switch lands within hysteresis (_ASD_HOLD_S) of the real change, NOT the old ~1-4s lag.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    half = round(5.0 * framing._ASD_FPS)
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", _grid(half * 2))
    monkeypatch.setattr(framing, "_track_observe", lambda cv2, det, frames: [_obs("L")] * half + [_obs("R")] * half)
    tr = framing.speaker_track(cfg, src, start=0.0, end=10.0, src_w=1920, src_h=1080)
    expected = (half + round(framing._ASD_HOLD_S * framing._ASD_FPS)) / framing._ASD_FPS   # commit = real turn + dwell
    assert abs(tr[0][1] - expected) < 0.2                                   # boundary at ~5.0 + the short dwell, not laggy
    assert tr[0][1] - 5.0 <= framing._ASD_HOLD_S + 0.2                      # dwell is small -> responsive

def test_speaker_track_one_frame_blip_does_not_flip(tmp_path, monkeypatch):
    # a single louder-RIGHT frame inside an all-LEFT window must NOT cause a cut (hysteresis dwell).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    n = round(5.0 * framing._ASD_FPS)
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", _grid(n))
    obs = [_obs("L")] * n; obs[n // 2] = _obs("R")                          # one blip
    monkeypatch.setattr(framing, "_track_observe", lambda cv2, det, frames: obs)
    assert framing.speaker_track(cfg, src, start=0.0, end=5.0, src_w=1920, src_h=1080) is None   # 1 position -> None

def test_speaker_track_one_dominant_face_is_none(tmp_path, monkeypatch):
    # both visible but the SAME person always talks -> one position -> None (static focus is identical).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    n = round(5.0 * framing._ASD_FPS)
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", _grid(n))
    monkeypatch.setattr(framing, "_track_observe", lambda cv2, det, frames: [_obs("L")] * n)
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
    from tests.fixtures.speech_segments import talk_seg
    src = _talk_src(transcript=[talk_seg("so tell me about your new record", start=10.0, end=13.5)])
    st = _stats([[[0.25, 0.5, 0.2, 0.45], [0.78, 0.45, 0.18, 0.4]]] * 4)
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=st) == framing.CT_MULTI

def test_classify_single_speaker_talk():
    from tests.fixtures.speech_segments import talk_seg
    src = _talk_src(transcript=[talk_seg("let me explain how this works", start=10.0, end=13.5)])
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
    from tests.fixtures.speech_segments import talk_seg
    src = _talk_src(transcript=[talk_seg("hello there friend", start=10.0, end=13.0)])
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=None) == framing.CT_NOPEOPLE

def test_classify_junk_asr_with_face_not_talk():
    # Plan E L4b: high no_speech_prob junk ASR + face must NOT route to talk (music or silent only).
    from tests.fixtures.speech_segments import MUSIC_HALLUC
    src = _talk_src(transcript=[{**MUSIC_HALLUC, "start": 10.0, "end": 13.5}])
    st = _stats([[[0.5, 0.5, 0.22, 0.45]]] * 4)
    ct = framing.classify_window(None, src, start=10.0, end=14.0, stats=st)
    assert ct in (framing.CT_MUSIC, framing.CT_SILENT), f"junk ASR must not classify as talk, got {ct!r}"

def test_classify_degraded_legacy_not_talk():
    # Plan E L4c: degraded-tier legacy segment + 2 faces must NOT trigger multi-speaker talk.
    from tests.fixtures.speech_segments import LEGACY_EN
    src = _talk_src(transcript=[{**LEGACY_EN, "start": 10.0, "end": 13.5}])
    st = _stats([[[0.25, 0.5, 0.2, 0.45], [0.78, 0.45, 0.18, 0.4]]] * 4)
    ct = framing.classify_window(None, src, start=10.0, end=14.0, stats=st)
    assert ct != framing.CT_MULTI, f"degraded legacy must not route to MULTI, got {ct!r}"
    assert ct in (framing.CT_MUSIC, framing.CT_SILENT), f"degraded legacy must not classify as talk, got {ct!r}"


# ---------------------------------------------------------------- _resolve_framing strategy router ----
def test_resolve_multi_uses_track(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: {"frames": [[[0.2, 0.5, 0.2, 0.45]]]})
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_MULTI)
    monkeypatch.setattr(framing, "speaker_track", lambda *a, **k: [(0.0, 5.0, 0.22, 0.5, 0.2, 0.45), (5.0, 10.0, 0.8, 0.45, 0.2, 0.4)])
    focus, track, ct = _resolve_framing(cfg, src, 0.0, 10.0)
    assert track and focus is None and ct == framing.CT_MULTI

def test_resolve_multi_no_track_centres_conservatively(tmp_path, monkeypatch):
    # E3: classified MULTI but no clean 2-shot track -> conservative CENTRE (both seats), NOT a one-person
    # subject lock that would crop the other speaker out. subject_focus must NOT be called for a MULTI window.
    from fanops.clip import _resolve_framing
    cfg = Config(root=tmp_path); src = _talk_src()
    called = {"focus": False}
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: {"frames": [[[0.2, 0.5, 0.2, 0.45]]]})
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_MULTI)
    monkeypatch.setattr(framing, "speaker_track", lambda *a, **k: None)        # not a real 2-shot
    def _focus(*a, **k):
        called["focus"] = True; return (0.5, 0.5, 0.22, 0.4)
    monkeypatch.setattr(framing, "subject_focus", _focus)
    assert _resolve_framing(cfg, src, 0.0, 10.0) == (None, None, None)         # centred, both seats
    assert called["focus"] is False                                           # E3: subject_focus not run for MULTI

def test_resolve_single_uses_focus(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: {"frames": []})
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_SINGLE)
    monkeypatch.setattr(framing, "subject_focus", lambda *a, **k: (0.6, 0.45, 0.25, 0.4))
    focus, track, ct = _resolve_framing(cfg, src, 0.0, 10.0)
    assert focus == (0.6, 0.45, 0.25, 0.4) and track is None and ct == framing.CT_SINGLE

def test_resolve_music_no_face_uses_saliency(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: {"frames": [[]]})
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_MUSIC)
    monkeypatch.setattr(framing, "subject_focus", lambda *a, **k: None)        # no face
    monkeypatch.setattr(framing, "motion_saliency", lambda *a, **k: (0.7, 0.4))
    focus, track, ct = _resolve_framing(cfg, src, 0.0, 10.0)
    assert focus == (0.7, 0.4) and track is None and ct is None                # saliency 2-tuple, NO zoom

def test_resolve_no_people_centers_when_no_motion(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: None)
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_NOPEOPLE)
    monkeypatch.setattr(framing, "motion_saliency", lambda *a, **k: None)
    assert _resolve_framing(cfg, src, 0.0, 10.0) == (None, None, None)         # centered (today)

def test_resolve_smart_framing_off_is_none(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "subject_focus", lambda *a, **k: (0.8, 0.5, 0.2, 0.4))   # would return, but gated off
    assert _resolve_framing(cfg, src, 0.0, 10.0) == (None, None, None)


# ================================================ smart_framing: ONE-CONSTRUCTION, FAIL-LOUD prerequisite ====
# Contract (Decision Record v4): when smart_framing is ON (production default), _resolve_framing constructs the
# REAL YuNet detector EXACTLY ONCE (framing._framing_runtime_or_raise) and reuses it for every detection call.
# A BROKEN PREREQUISITE — cv2 absent, FaceDetectorYN/.create missing, model file absent, FaceDetectorYN.create()
# returning None, or FaceDetectorYN.create() raising — REFUSES loudly with ToolchainMissingError BEFORE any
# centered output. A GENUINE DETECTION MISS (detector built OK, no face found) still fails open to centered.
# No autouse/suite-wide bypass exists; these force the real enforcement path. cv2 is really installed in the
# unit lane, so the refusals are induced by stubbing the specific seam (_cv2/_model_path/_detector), never by
# no-op'ing the guard.

def _fake_cv2_with_create(create):
    return types.SimpleNamespace(FaceDetectorYN=types.SimpleNamespace(create=create))

# (1) refuse when _cv2() returns None
def test_resolve_refuses_when_cv2_absent(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    from fanops.errors import ToolchainMissingError
    cfg = Config(root=tmp_path); src = _talk_src()             # smart_framing default ON
    monkeypatch.setattr(framing, "_cv2", lambda: None)
    with pytest.raises(ToolchainMissingError):
        _resolve_framing(cfg, src, 0.0, 10.0)

# (2) refuse when FaceDetectorYN or .create is unavailable (OpenCV too old)
def test_resolve_refuses_when_facedetector_attr_missing(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    from fanops.errors import ToolchainMissingError
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "_cv2", lambda: object())     # no FaceDetectorYN attr at all
    with pytest.raises(ToolchainMissingError):
        _resolve_framing(cfg, src, 0.0, 10.0)

# (3) refuse when the vendored model is absent
def test_resolve_refuses_when_model_missing(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    from fanops.errors import ToolchainMissingError
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "_cv2", lambda: _fake_cv2_with_create(lambda *a, **k: object()))
    monkeypatch.setattr(framing, "_model_path", lambda: Path("/definitely/absent/yunet.onnx"))
    with pytest.raises(ToolchainMissingError):
        _resolve_framing(cfg, src, 0.0, 10.0)

# (4a) refuse when the actual constructor returns None
def test_resolve_refuses_when_constructor_returns_none(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    from fanops.errors import ToolchainMissingError
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "_cv2", lambda: _fake_cv2_with_create(lambda *a, **k: None))  # create()->None
    with pytest.raises(ToolchainMissingError):
        _resolve_framing(cfg, src, 0.0, 10.0)

# (4b) refuse when the actual constructor raises
def test_resolve_refuses_when_constructor_raises(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    from fanops.errors import ToolchainMissingError
    def _boom(*a, **k): raise RuntimeError("corrupt ONNX / OpenCV ABI mismatch")
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "_cv2", lambda: _fake_cv2_with_create(_boom))
    with pytest.raises(ToolchainMissingError):
        _resolve_framing(cfg, src, 0.0, 10.0)

# (5) constructor-failure cases DO NOT reach detection-miss centering (they raise; detect_window never runs)
def test_constructor_failure_does_not_center(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    from fanops.errors import ToolchainMissingError
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "_cv2", lambda: _fake_cv2_with_create(lambda *a, **k: None))
    called = {"detect": 0}
    orig = framing.detect_window
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: called.__setitem__("detect", called["detect"] + 1) or orig(*a, **k))
    with pytest.raises(ToolchainMissingError):
        _resolve_framing(cfg, src, 0.0, 10.0)
    assert called["detect"] == 0                               # refused BEFORE detection -> no centered fallback path

# (6) initialized detector + no face found -> centered (None,None,None), NOT a raise
def test_initialized_no_face_centers(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    cfg = Config(root=tmp_path); src = _talk_src()
    # a real-shaped runtime: create() returns a usable detector object; detection then finds nothing.
    monkeypatch.setattr(framing, "_cv2", lambda: _fake_cv2_with_create(lambda *a, **k: object()))
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())          # construction SUCCEEDS
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: None)      # ...but no face -> miss
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_NOPEOPLE)
    monkeypatch.setattr(framing, "motion_saliency", lambda *a, **k: None)
    assert _resolve_framing(cfg, src, 0.0, 10.0) == (None, None, None)       # centered, no raise

# (7) render_moment reaches prerequisite enforcement and refuses on constructor failure
def test_render_moment_refuses_on_constructor_failure(tmp_path, monkeypatch):
    from fanops.clip import render_moment
    from fanops.errors import ToolchainMissingError
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    monkeypatch.setattr(framing, "_cv2", lambda: _fake_cv2_with_create(lambda *a, **k: None))  # create()->None
    with pytest.raises(ToolchainMissingError):
        render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)

# (8) render_account_cut reaches prerequisite enforcement and refuses on constructor failure
def test_render_account_cut_refuses_on_constructor_failure(tmp_path, monkeypatch):
    from fanops.errors import ToolchainMissingError
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    monkeypatch.setattr(framing, "_cv2", lambda: _fake_cv2_with_create(lambda *a, **k: None))
    with pytest.raises(ToolchainMissingError):
        render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="talk", hook="", out_path=str(cfg.clips / "acct.mp4"))

# (9) _supercut_span_entries reaches prerequisite enforcement and refuses (never partially renders)
def test_supercut_span_entries_refuses_on_missing_prereq(tmp_path, monkeypatch):
    from fanops.clip import _supercut_span_entries
    from fanops.errors import ToolchainMissingError
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "_cv2", lambda: None)
    with pytest.raises(ToolchainMissingError):
        _supercut_span_entries(cfg, src, [(0.0, 3.0), (5.0, 8.0)])

# (10) the REAL installed OpenCV path passes (integration: needs the [framing] extra + vendored model)
@pytest.mark.integration
def test_real_opencv_runtime_constructs(tmp_path):
    # In the e2e/base lanes cv2 is genuinely installed; the runtime must build the detector without raising.
    rt = framing._framing_runtime_or_raise(Config(root=tmp_path))
    assert rt.cv2 is not None and rt.detector is not None

# (11) ONE cold-sidecar _resolve_framing invocation calls FaceDetectorYN.create EXACTLY ONCE (integration:
# needs real cv2 so the real constructor is the thing being counted)
@pytest.mark.integration
def test_one_resolve_constructs_detector_exactly_once(tmp_path, monkeypatch):
    import cv2
    from fanops.clip import _resolve_framing
    calls = {"n": 0}
    orig = cv2.FaceDetectorYN.create
    monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or orig(*a, **k))))
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: [])  # ffmpeg-free, forces the miss path
    cfg = Config(root=tmp_path); src = _talk_src()             # cold sidecar (fresh tmp root)
    _resolve_framing(cfg, src, 0.0, 6.0)
    assert calls["n"] == 1                                     # constructed ONCE, not per detection function

@pytest.mark.integration
def test_framing_construction_and_extraction_counts_reported(tmp_path, monkeypatch, capsys):
    # CI-authoritative instrumentation: with REAL cv2, measure and ASSERT the per-resolution counts the
    # decision record reports (constructor calls, detector objects, detect_window calls, grid extractions),
    # and prove the sidecar cache makes a WARM second resolution do zero new construction/extraction.
    import cv2
    from fanops.clip import _resolve_framing
    from fanops import framing as fr
    n = {"create": 0, "grid": 0, "detect_window": 0}
    orig_create = cv2.FaceDetectorYN.create
    monkeypatch.setattr(cv2.FaceDetectorYN, "create",
                        staticmethod(lambda *a, **k: (n.__setitem__("create", n["create"] + 1) or orig_create(*a, **k))))
    # Return ONE synthetic frame so detect_window writes its sidecar (an empty grid -> stats None -> no cache,
    # which would make the warm-cache assertion meaningless). YuNet finding no face in it is fine (miss->centered).
    import numpy as np
    def _one_frame(path, *a, **k):
        n["grid"] += 1
        f = tmp_path / "syn.png"; import cv2 as _c2
        _c2.imwrite(str(f), np.full((540, 960, 3), 90, np.uint8))
        return [str(f)]
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", _one_frame)
    orig_dw = fr.detect_window
    monkeypatch.setattr(fr, "detect_window",
                        lambda *a, **k: (n.__setitem__("detect_window", n["detect_window"] + 1) or orig_dw(*a, **k)))
    cfg = Config(root=tmp_path); src = _talk_src()             # COLD sidecar (fresh tmp root)

    _resolve_framing(cfg, src, 0.0, 6.0)                        # first (cold) resolution
    cold = dict(n)
    print(f"[framing-counts] COLD resolution: FaceDetectorYN.create={cold['create']} "
          f"detect_window={cold['detect_window']} grid_extract={cold['grid']}")
    assert cold["create"] == 1, f"expected exactly ONE detector construction per resolution, got {cold['create']}"

    for k in n: n[k] = 0
    _resolve_framing(cfg, src, 0.0, 6.0)                        # second (WARM sidecar) resolution, same window
    warm = dict(n)
    print(f"[framing-counts] WARM resolution (same window): FaceDetectorYN.create={warm['create']} "
          f"detect_window={warm['detect_window']} grid_extract={warm['grid']}")
    # Construction is PER-RESOLUTION: each _resolve_framing builds exactly ONE detector (the prerequisite is
    # re-proved every resolution — never 2). The sidecar caches DETECTION RESULTS, not the detector object, so
    # a warm resolution still constructs 1 but does LESS extraction (detect_window's grid pass hits the cache).
    assert warm["create"] == 1, f"each resolution constructs exactly one detector, got {warm['create']}"
    assert warm["grid"] < cold["grid"], (
        f"warm sidecar must reduce frame extraction ({warm['grid']} !< {cold['grid']})")
    print("[framing-counts] init scope = PER-RESOLUTION (fresh _FramingRuntime each _resolve_framing): "
          f"create=1 every resolution (never 2); warm sidecar cuts extraction {cold['grid']}->{warm['grid']}")

# (13) OFF CONTRACT: the toggle is evaluated BEFORE the runtime build, so the retained OFF path never
# requires OpenCV. If _resolve_framing ever built the runtime first, OFF would start demanding the extra.
def test_resolve_off_never_constructs_runtime(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    def _boom_rt(cfg): raise AssertionError("framing runtime CONSTRUCTED while smart_framing is OFF")
    def _boom_cv2(): raise AssertionError("cv2 consulted while smart_framing is OFF")
    monkeypatch.setattr(framing, "_framing_runtime_or_raise", _boom_rt)
    monkeypatch.setattr(framing, "_cv2", _boom_cv2)
    cfg = Config(root=tmp_path); src = _talk_src()
    assert _resolve_framing(cfg, src, 0.0, 10.0) == (None, None, None)   # centered; no runtime, no cv2

# (14) OBJECT LIFETIME / CONCURRENCY. What THIS TEST proves: PER-INVOCATION ALLOCATION on the path it
# exercises — 2 sequential + 2 concurrent resolutions yield 4 distinct _FramingRuntime objects and 4 distinct
# detector objects, and no runtime is retained in module/Config/Source state at the end. It does NOT prove that
# no OTHER code path could retain one; that rests on code inspection (one construction site framing.py:100; the
# only callers are clip._resolve_framing (local binding) and require_cv2 (builds+discards); consumers only READ
# _rt.cv2/_rt.detector; no global/module cache). See docs/design/cv2-decision-record-v4.md §4b. Together they
# are why a YuNet detector (mutable setInputSize state) is never shared across concurrent resolutions.
def test_framing_runtime_is_per_invocation_never_shared(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from fanops.clip import _resolve_framing
    real_rt = framing._framing_runtime_or_raise
    seen = []
    lock = __import__("threading").Lock()
    monkeypatch.setattr(framing, "_cv2", lambda: _fake_cv2_with_create(lambda *a, **k: object()))
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())      # a DISTINCT detector object per build
    def _spy(cfg):
        rt = real_rt(cfg)
        with lock: seen.append(rt)
        return rt
    monkeypatch.setattr(framing, "_framing_runtime_or_raise", _spy)
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: None)  # never touch the fake detector
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_NOPEOPLE)
    monkeypatch.setattr(framing, "motion_saliency", lambda *a, **k: None)
    cfg = Config(root=tmp_path); src = _talk_src()

    _resolve_framing(cfg, src, 0.0, 5.0)                                  # sequential x2
    _resolve_framing(cfg, src, 0.0, 5.0)
    with ThreadPoolExecutor(max_workers=2) as ex:                         # concurrent x2
        list(ex.map(lambda _: _resolve_framing(cfg, src, 0.0, 5.0), range(2)))

    assert len(seen) == 4
    assert len({id(rt) for rt in seen}) == 4, "each resolution must build its OWN runtime (none cached/shared)"
    assert len({id(rt.detector) for rt in seen}) == 4, "each resolution must own its detector (YuNet holds mutable state)"
    # nothing stashed a runtime in module state, on the Config, or on the Source
    assert not [k for k, v in vars(framing).items() if isinstance(v, framing._FramingRuntime)], \
        "no module-level _FramingRuntime may be retained"
    assert not [a for a in dir(cfg) if isinstance(getattr(cfg, a, None), framing._FramingRuntime)], \
        "the runtime must never be stored on Config"
    assert not [a for a in dir(src) if isinstance(getattr(src, a, None), framing._FramingRuntime)], \
        "the runtime must never be stored on the Source"

# (12) no suite-wide or autouse require_cv2 / runtime bypass exists (guards the 6dca52c regression class)
def test_no_autouse_framing_guard_bypass():
    conf = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    assert "require_cv2" not in conf, "conftest must not monkeypatch require_cv2 (that hides missing prereqs)"
    assert "_framing_runtime_or_raise" not in conf, "conftest must not bypass the framing runtime"
    assert "_hermetic_framing_guard" not in conf, "the suite-wide framing bypass fixture must not return"



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
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: {"frames": [[[0.8, 0.5, 0.2, 0.45]]]})
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_SINGLE)
    monkeypatch.setattr(framing, "subject_focus", lambda *a, **k: (0.8, 0.5))   # a detected subject (2-tuple -> no zoom)
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


def test_sidecar_loaders_reject_nondict_windows(tmp_path):
    # NEVER-raises contract (module docstring): a corrupt sidecar whose "windows" is NOT a dict must yield {}
    # (recompute), never the raw value. Returning a string/list let the caller's `key in cache` / `cache[key]`
    # raise TypeError OUTSIDE the load try -> a crash on the safety-critical reframe path. isinstance-guard both.
    from fanops.framing import _load_cache, _load_detect_cache, _SIDECAR_V, _DETECT_V
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"v": _SIDECAR_V, "windows": "corrupt-not-a-dict"}))
    assert _load_cache(p) == {}
    p.write_text(json.dumps({"v": _DETECT_V, "windows": ["also", "wrong"]}))
    assert _load_detect_cache(p) == {}
    # a genuinely-shaped sidecar still round-trips (the guard doesn't reject valid dicts)
    p.write_text(json.dumps({"v": _SIDECAR_V, "windows": {"0.0-6.0": {"focus": [0.5, 0.4]}}}))
    assert _load_cache(p) == {"0.0-6.0": {"focus": [0.5, 0.4]}}


# ---------------------------------------------------------------- real OpenCV/YuNet smoke (MOL-196) ----
@pytest.mark.integration
def test_real_yunet_detection_path_executes(tmp_path):
    """MOL-196: every other detection test stubs cv2. This one proves the REAL OpenCV/YuNet path runs when
    the [framing] extra is actually installed (the e2e CI job) — the vendored model loads into a real
    cv2.FaceDetectorYN and detection runs end-to-end on a real image. Skips locally when cv2 is absent
    (in CI's e2e job [framing] IS installed, so FANOPS_REQUIRE_E2E turns any skip here into a failure)."""
    cv2 = framing._cv2()
    if cv2 is None:
        pytest.skip("cv2 (opencv-python-headless / [framing] extra) not installed")
    import numpy as np
    det = framing._detector(cv2)
    assert det is not None, "vendored YuNet model failed to load into real cv2.FaceDetectorYN"
    img = tmp_path / "frame.png"
    cv2.imwrite(str(img), np.zeros((320, 320, 3), dtype=np.uint8))     # real image write via real cv2
    faces = framing._detect_faces(cv2, det, str(img))                  # real detection pass (blank frame -> [])
    assert isinstance(faces, list)                                     # the real path executed without raising


# ---------------------------------------------------------------- phantom face fix (mol-framing-phantom-faces) ----

def test_detect_v_bumped():
    # cache version must be >=2 so stale 4-element (no-score) sidecars are invalidated on upgrade.
    assert framing._DETECT_V >= 2

def test_detect_faces_includes_score(monkeypatch, tmp_path):
    # _detect_faces now returns 6-tuples (cx,cy,fh,ey,score,fw) — score is the YuNet confidence at f[14],
    # fw is the face-box WIDTH (E1, appended so score stays at [4]).
    # YuNet row: [x,y,w,h, rEyeX,rEyeY, lEyeX,lEyeY, noseX,noseY, rMX,rMY, lMX,lMY, score]
    _face_row = [10.0, 10.0, 80.0, 60.0,    # x,y,w,h
                 40.0, 20.0, 60.0, 20.0,     # rEye, lEye
                 50.0, 30.0,                  # nose
                 40.0, 50.0, 60.0, 50.0,     # rMouth, lMouth
                 0.92]                        # score at index 14
    class _FakeImg:                           # stub image — shape[0,1] give h,w; no numpy required
        shape = (100, 160, 3)
    class _FakeDet:
        def setInputSize(self, sz): pass
        def detect(self, img): return 1, [_face_row]   # list-of-lists, not numpy — _detect_faces iterates it fine
    class _CV2:
        def imread(self, p): return _FakeImg()
    faces = framing._detect_faces(_CV2(), _FakeDet(), str(tmp_path / "f.png"))
    assert len(faces) == 1
    face = faces[0]
    assert len(face) == 6, f"expected 6-tuple (cx,cy,fh,ey,score,fw), got {face}"
    assert face[4] > 0.0, "score must be >0 for a high-confidence face"
    assert abs(face[5] - 0.5) < 1e-6, f"fw = box width / frame width = 80/160 = 0.5, got {face[5]}"  # E1 face WIDTH

def test_pick_dominant_face_prefers_high_score():
    # score-first: a smaller but higher-confidence face beats a larger lower-confidence face.
    real   = [0.3, 0.5, 0.22, 0.45, 0.88]   # real speaker — high score, normal size
    decoy  = [0.8, 0.5, 0.35, 0.40, 0.63]   # wall-art phantom — higher area but lower score
    assert framing._pick_dominant_face([real, decoy]) == real
    assert framing._pick_dominant_face([decoy, real]) == real   # order-invariant

def test_pick_dominant_face_area_tiebreak():
    # equal score -> larger area (fh) wins.
    small = [0.3, 0.5, 0.10, 0.45, 0.85]
    large = [0.7, 0.5, 0.28, 0.40, 0.85]
    assert framing._pick_dominant_face([small, large]) == large
    assert framing._pick_dominant_face([large, small]) == large

def test_pick_dominant_face_empty_is_none():
    assert framing._pick_dominant_face([]) is None

def test_pick_dominant_face_legacy_4tuple_area_only():
    # 4-element faces (no score field) must still work — falls back to area comparison.
    small = [0.3, 0.5, 0.10, 0.45]
    large = [0.7, 0.5, 0.28, 0.40]
    assert framing._pick_dominant_face([small, large]) == large

def test_face_count_phantom_decoy_is_single():
    # ONE real speaker (high score, normal fh) + ONE phantom wall-art decoy (low score, tiny fh)
    # must yield count=1 so classify_window returns CT_SINGLE, not CT_MULTI.
    real_face  = [0.30, 0.50, 0.25, 0.45, 0.87]   # real speaker
    phantom    = [0.75, 0.48, 0.06, 0.43, 0.64]   # wall-art/poster face — score AND area tiny relative to real
    st = _stats([[real_face, phantom]] * 4)
    assert framing._face_count(st) == 1, "phantom decoy must not inflate face count to MULTI"

def test_face_count_real_two_shot_is_multi():
    # two comparable faces (real 2-shot interview) must still give count=2 → CT_MULTI preserved.
    left  = [0.22, 0.50, 0.24, 0.45, 0.86]
    right = [0.80, 0.45, 0.21, 0.42, 0.83]
    st = _stats([[left, right]] * 4)
    assert framing._face_count(st) == 2, "real 2-shot must remain MULTI (no regression)"

def test_classify_phantom_decoy_routes_to_single(tmp_path, monkeypatch):
    # end-to-end: phantom wall-art face next to a real speaker must NOT trigger multi-speaker switching.
    from tests.fixtures.speech_segments import talk_seg
    src = _talk_src(transcript=[talk_seg("here is my take on this", start=10.0, end=13.5)])
    real_face = [0.30, 0.50, 0.25, 0.45, 0.87]
    phantom   = [0.75, 0.48, 0.06, 0.43, 0.64]
    st = _stats([[real_face, phantom]] * 4)
    ct = framing.classify_window(None, src, start=10.0, end=14.0, stats=st)
    assert ct == framing.CT_SINGLE, f"phantom decoy must route to SINGLE, got {ct!r}"

def test_subject_focus_picks_real_speaker_over_phantom(tmp_path, monkeypatch):
    # off-center real speaker (score=0.87) must win over phantom decoy (score=0.64) as subject focus.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    real_face = [0.30, 0.50, 0.25, 0.45, 0.87]   # real speaker at x=0.30
    phantom   = [0.75, 0.48, 0.06, 0.43, 0.64]   # phantom near right edge
    stats = {"fps": 4.0, "frames": [[real_face, phantom]] * 4}
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: stats)
    fx, fy, fh, ey, fw = framing.subject_focus(cfg, src, start=10.0, end=14.0)
    assert abs(fx - 0.30) < 0.01, f"real speaker at x=0.30 must win; got fx={fx}"

def test_detect_window_stores_score_in_sidecar(tmp_path, monkeypatch):
    # the detect sidecar must store 6-element faces (cx,cy,fh,ey,score,fw) so _pick_dominant_face uses the
    # score AND the geometry can use the width on a cache hit (E1).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: ["g0"])
    monkeypatch.setattr(framing, "_detect_faces", lambda cv2, det, fp: [(0.5, 0.5, 0.2, 0.45, 0.88, 0.15)])
    st = framing.detect_window(cfg, src, start=10.0, end=14.0)
    assert st is not None
    assert len(st["frames"][0][0]) == 6, "detect sidecar must persist 6-element faces (score + width)"
    sidecar = cfg.agent_io / "framing" / "s1.detect.json"
    cached = json.loads(sidecar.read_text())
    assert cached["v"] == framing._DETECT_V
    assert len(cached["windows"]["10.0-14.0"]["frames"][0][0]) == 6


# ---- E1b + E2 mechanical invariants (crop coordinates + classification; no ffmpeg) — contract §6 ----
def test_e1b_safe_area_keeps_face_box_inside_with_margin():
    # SAFE-AREA: the emitted crop contains the FULL detected face box with >= margin M on every edge (fails-
    # before: the origin was clamped only to source bounds, so an off-centre cheek reached the frame edge).
    sw, sh, tw, th = 1920, 1080, 1080, 1920
    fx, fy, fh, ey, fw = 0.66, 0.46, 0.30, 0.42, 0.18
    cw, ch, x, y = clipmod._crop_box(fx, fy, fh, ey, sw, sh, tw, th, sh, clipmod._FACE_FRAC_TALK, clipmod._ZOOM_MAX, fw)
    mw, mv = clipmod._SAFE_MARGIN_FRAC * sw, clipmod._SAFE_MARGIN_FRAC * sh
    fl, fr, ft, fb = (fx - fw / 2) * sw, (fx + fw / 2) * sw, (fy - fh / 2) * sh, (fy + fh / 2) * sh
    assert x + mw <= fl + 1 and fr <= x + cw - mw + 1          # L/R face edges inside with the horizontal margin
    assert y + mv <= ft + 1 and fb <= y + ch - mv + 1          # head-top / chin inside with the vertical margin

def test_e1b_zoom_backoff_widens_never_cuts():
    # ZOOM-BACKOFF: a face too WIDE to fit at the target zoom -> the crop widens (ch grows past the target-
    # fraction zoom), never a face-cutting crop; still bounded by the source baseline.
    sw, sh, tw, th = 1920, 1080, 1080, 1920
    fx, fy, fh, ey, fw = 0.5, 0.46, 0.24, 0.42, 0.30          # a wide face (fw=0.30)
    target_ch = clipmod._zoom_h(sh, sh, fh, clipmod._FACE_FRAC_TALK,
                                zoom_max=clipmod._adaptive_zoom_max(fh, clipmod._ZOOM_MAX))
    cw, ch, x, y = clipmod._crop_box(fx, fy, fh, ey, sw, sh, tw, th, sh, clipmod._FACE_FRAC_TALK, clipmod._ZOOM_MAX, fw)
    assert ch > target_ch                                      # backed off (widened) to fit the wide face
    assert ch <= sh and cw <= sw                              # never beyond the source
    fl, fr = (fx - fw / 2) * sw, (fx + fw / 2) * sw
    assert fl >= x - 1 and fr <= x + cw + 1                   # the face is fully inside the crop (never cut)

def test_e1b_headroom_clamp_protects_the_head():
    # HEADROOM: crop_top <= head_top - headroom. A naive eyeline placement that would clip the head is pulled
    # UP by the safe-area clamp (fails-before: headroom was a fixed eyeline fraction, so a tall head clipped).
    sw, sh, cw, ch = 1920, 1080, 380, 675
    fx, fy, fh, ey, fw = 0.5, 0.5, 0.22, 0.611, 0.08
    mv = clipmod._SAFE_MARGIN_FRAC * sh
    head_top = (fy - fh / 2) * sh
    _, y_safe = clipmod._safe_origin(sw, sh, cw, ch, fx, fy, fh, ey, fw, clipmod._EYELINE_FRAC)
    _, y_naive = clipmod._place(sw, sh, cw, ch, fx, ey, clipmod._EYELINE_FRAC)
    assert y_safe <= head_top - mv + 1                        # headroom preserved
    assert y_safe < y_naive                                   # the clamp actively moved the crop up off the head

def test_e1b_no_regression_centered_and_2tuple_byte_identical():
    # NO-REGRESSION: centered (focus=None) and a 2-tuple focus (no face size) render byte-identical to the
    # pre-E1b crop, so their stored fingerprints stay valid and nothing needlessly re-renders.
    assert reframe_filter("9:16", 1920, 1080) == "crop=ih*1080/1920:ih,scale=1080:1920,setsar=1"
    assert reframe_filter("9:16", 1920, 1080, focus=(0.8, 0.5)) == "crop=ih*1080/1920:ih:1232:0,scale=1080:1920,setsar=1"
    assert reframe_filter("9:16", 1080, 2400, top_bias=True) == \
        "crop=iw:iw*1920/1080:0:(ih-iw*1920/1080)/4,scale=1080:1920,setsar=1"

def test_e2_two_cluster_recall_promotes_intermittent_two_shot():
    # RECALL: a two-shot where the 2nd host is dominant only INTERMITTENTLY -> median face count is 1 (the old
    # undercount -> CT_SINGLE), but the L/R clustering recalls it as CT_MULTI when speech is present.
    from tests.fixtures.speech_segments import talk_seg
    L = [0.24, 0.50, 0.24, 0.45, 0.88]
    R_big = [0.78, 0.46, 0.22, 0.42, 0.85]                    # 2nd host clearly present
    R_small = [0.79, 0.47, 0.09, 0.42, 0.62]                  # 2nd host turned/distant (below the RELATIVE phantom gate)
    st = _stats([[L, R_big], [L, R_small], [L, R_small], [L, R_big], [L, R_small]])
    assert framing._face_count(st) == 1                       # median-count undercounts the 2nd host
    assert framing._two_cluster(st) is True                   # but the L/R clustering recalls the two-shot
    src = _talk_src(transcript=[talk_seg("so tell me about the record", start=10.0, end=13.5)])
    assert framing.classify_window(None, src, start=10.0, end=14.0, stats=st) == framing.CT_MULTI

def test_e2_two_cluster_rejects_centered_single_face_jitter():
    # a single near-centre face whose cx jitters across the split must NOT become two clusters (the dead zone).
    st = _stats([[[0.48, 0.5, 0.24, 0.45, 0.9]], [[0.52, 0.5, 0.24, 0.45, 0.9]]] * 3)
    assert framing._two_cluster(st) is False


# ---- E4 real-tooling proof: the concat render path under REAL ffmpeg (a command-construction test is blind
# to the muxed timeline). Helpers are `_e4_`-prefixed so they cannot collide with anything above. ----
def _e4_ass_ts(t: float) -> str:
    """seconds -> ASS timestamp H:MM:SS.cs (centiseconds)."""
    cs = int(round(t * 100)); h, cs = divmod(cs, 360000); m, cs = divmod(cs, 6000); s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def _e4_ratio(r: str) -> float:
    n, _, d = str(r).partition("/"); return float(n) / float(d or 1)

def _e4_probe(entries: list, path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", *entries, "-of", "json", str(path)],
                         check=True, capture_output=True, text=True)
    return json.loads(out.stdout)

def _e4_frame_pts(path) -> list:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True)
    # numeric-only guard (a stray N/A row is skipped without an except-swallow)
    return sorted(float(ln) for ln in out.stdout.splitlines() if re.fullmatch(r"[0-9.]+", ln.strip()))

def _e4_bright_frame_times(path, work) -> list:
    """pts_time of every OUTPUT frame whose mean luma rises clearly above the source's black baseline — i.e.
    the frames on which the burned white subtitle is actually visible. signalstats.YAVG per frame via a
    metadata=print sidecar; the threshold auto-calibrates off the minimum (black) frame so it is range-agnostic."""
    stats = Path(work) / "e4_stats.txt"
    subprocess.run(["ffmpeg", "-y", "-i", str(path), "-vf", f"signalstats,metadata=print:file={stats}",
                    "-an", "-f", "null", "-"], check=True, capture_output=True, text=True)
    pairs, cur = [], None
    for ln in stats.read_text().splitlines():
        mt = re.search(r"pts_time:([\d.]+)", ln)
        if mt: cur = float(mt.group(1))
        my = re.search(r"signalstats\.YAVG=([\d.]+)", ln)
        if my and cur is not None: pairs.append((cur, float(my.group(1))))
    if not pairs: return []
    base = min(y for _, y in pairs)
    return [t for t, y in pairs if y > base + 8.0]

@pytest.mark.integration
@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="real ffmpeg/ffprobe required")
def test_segments_concat_real_ffmpeg_holds_fps_duration_audiosync_no_gap_and_subtitle_timing(tmp_path):
    """E4 real-tooling proof (the command-construction test above cannot see this): render the 3-SEGMENT
    concat path through REAL ffmpeg and measure the muxed output. `-fps_mode cfr` must have filled the
    concat filter's per-join PTS gaps, so ALL of: avg_frame_rate matches the source within the validator's
    _FPS_TOL; duration is exact within _DUR_TOL_S; audio is present, full-length, and coterminous with the
    video (sync); NO join leaves a >1-frame PTS gap; and a burned .ass subtitle authored ENTIRELY AFTER the
    final join displays at its authored timestamps. On the pre-fix command (no cfr) the concat leaves 2-frame
    gaps -> avg_frame_rate sags (~29.83) and max frame delta ~2/fps; this pins the post-fix invariants."""
    from fanops.clip import render_reframed
    from fanops.reframe_apply import _FPS_TOL, _DUR_TOL_S
    fps, dur = 30, 6.0
    # SOLID-BLACK source: the ONLY luminance in the output is the burned white subtitle -> a clean, decodable
    # signal for the subtitle-timing assertion. 30fps CFR + a stereo tone so audio sync is measurable.
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:size=1280x720:rate={fps}:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-t", str(dur), str(src)],
                   check=True, capture_output=True, text=True)
    # A FONT-INDEPENDENT white band burned ONLY in [4.50, 5.00] — entirely AFTER the final join at 4.0s.
    # It is an ASS VECTOR DRAWING (\p1): libass rasterizes the polygon from the style's PrimaryColour with
    # NO glyph/font lookup, so the timing check cannot flake on a headless runner's font set (a glyph-based
    # subtitle renders nothing when fontconfig has no match). \an7\pos(0,0) makes the drawing coords absolute
    # screen pixels at PlayRes; the band spans the full width, y 800..1200 of 1920 -> a large luma lift.
    sub_on, sub_off = 4.50, 5.00
    ass = tmp_path / "sub.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Alignment\n"
        "Style: Box,Arial,40,&H00FFFFFF,7\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
        f"Dialogue: 0,{_e4_ass_ts(sub_on)},{_e4_ass_ts(sub_off)},Box,"
        "{\\an7\\pos(0,0)\\p1}m 0 800 l 1080 800 1080 1200 0 1200{\\p0}\n")
    # 3 segments -> 2 joins. Centered static crops (fh=None -> no zoom) keep the frame black outside the sub.
    track = [(0.0, 2.0, 0.5, 0.5, None, None),
             (2.0, 4.0, 0.5, 0.5, None, None),
             (4.0, 6.0, 0.5, 0.5, None, None)]
    dst = tmp_path / "out.mp4"
    r = render_reframed(str(src), str(dst), 0.0, dur, "9:16", src_w=1280, src_h=720,
                        track=track, extra_vf=f"subtitles='{ass}'", content_type="multi-speaker-talk")
    assert r.returncode == 0 and dst.exists() and dst.stat().st_size > 0

    # (1) avg_frame_rate within the validator's _FPS_TOL of the source rate — the direct concat-gap symptom.
    v = _e4_probe(["-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate,r_frame_rate"], dst)["streams"][0]
    avg = _e4_ratio(v["avg_frame_rate"])
    assert abs(avg - fps) <= _FPS_TOL, f"avg_frame_rate {avg} != source {fps} (tol {_FPS_TOL}); concat PTS gaps unfilled"
    # (2) duration within the validator's _DUR_TOL_S of the exact clip length.
    vdur = float(_e4_probe(["-show_entries", "format=duration"], dst)["format"]["duration"])
    assert abs(vdur - dur) <= _DUR_TOL_S, f"duration {vdur} vs {dur} (tol {_DUR_TOL_S}s)"
    # (3) audio present, ~full-length, and coterminous with the video (A/V sync holds after the joins).
    astreams = _e4_probe(["-select_streams", "a:0", "-show_entries", "stream=duration,codec_type"], dst)["streams"]
    assert astreams and astreams[0]["codec_type"] == "audio", "output lost its audio stream"
    adur = float(astreams[0]["duration"])
    assert abs(adur - dur) <= _DUR_TOL_S and abs(adur - vdur) <= 0.10, f"A/V desync: audio={adur} video={vdur}"
    # (4) NO PTS gap at any join: every consecutive video frame delta is ~1 frame; none exceeds 1.5 frames.
    pts = _e4_frame_pts(dst)
    assert len(pts) >= int(dur * fps) - 1, f"only {len(pts)} frames for a {dur}s/{fps}fps clip"
    max_delta = max(b - a for a, b in zip(pts, pts[1:]))
    assert max_delta <= 1.5 / fps, f"a join left a PTS gap: max frame delta {max_delta:.4f}s > {1.5/fps:.4f}s"
    # (5) the burned subtitle displays at its authored window, AFTER the final join (timeline did not drift).
    lit = _e4_bright_frame_times(dst, tmp_path)
    assert lit, "no burned subtitle detected in the output — the .ass burn did not reach the pixels"
    assert abs(min(lit) - sub_on) <= 2.5 / fps and abs(max(lit) - sub_off) <= 2.5 / fps, \
        f"subtitle window {min(lit):.3f}..{max(lit):.3f}s drifted from authored {sub_on}..{sub_off}s after the join"
