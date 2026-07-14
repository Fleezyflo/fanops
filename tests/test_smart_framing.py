# tests/test_smart_framing.py — Smart framing (subject-aware reframe). The 9:16 crop SLIDES onto the
# detected subject instead of the blind top/center guess: framing.subject_focus returns a normalized
# centroid, clip.reframe_filter turns it into a clamped crop offset, and both render paths thread it
# through ffmpeg_clip_cmd + the render fingerprint. Detection MISSES are FAIL-OPEN: a stub/flag returning
# None -> focus=None -> today's centered crop, byte-identical. But the cv2 DEPENDENCY is now REQUIRED when
# smart_framing is ON: with the extra ABSENT + smart_framing ON, _resolve_framing REFUSES (ToolchainMissingError)
# rather than silently centre-crop (see the require_cv2 raise-tests below). cv2 is absent in the hermetic unit
# job, so the conftest autouse `_hermetic_framing_guard` no-ops framing.require_cv2 suite-wide (smart_framing
# stays ON, production-faithful — only the absent cv2 dependency is neutralized); the detection path is
# exercised with stubs. The raise-tests below re-install the REAL require_cv2 (via _REAL_REQUIRE_CV2) to prove
# the refusal itself fires.
import json, re, types
from pathlib import Path
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, Fmt
from fanops import framing
from fanops.clip import (reframe_filter, _render_fingerprint, render_account_cut,
                         _segments_filter_complex, ffmpeg_segments_cmd, render_reframed, _ch0_for)
import fanops.clip as clipmod

_REAL_REQUIRE_CV2 = framing.require_cv2   # captured before the conftest fixture patches it -> raise-tests re-install this
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

def test_resolve_multi_falls_to_single_when_track_refuses(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "detect_window", lambda *a, **k: {"frames": []})
    monkeypatch.setattr(framing, "classify_window", lambda *a, **k: framing.CT_MULTI)
    monkeypatch.setattr(framing, "speaker_track", lambda *a, **k: None)        # not a real 2-shot
    monkeypatch.setattr(framing, "subject_focus", lambda *a, **k: (0.5, 0.5, 0.22, 0.4))
    focus, track, ct = _resolve_framing(cfg, src, 0.0, 10.0)
    assert track is None and focus == (0.5, 0.5, 0.22, 0.4) and ct == framing.CT_SINGLE

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


# ------------------------------------------------ cv2 REQUIRED when smart_framing ON (fail loud, not centre) ----
# The contract: smart_framing ON is the production default, and it now HARD-REQUIRES the [framing] extra —
# require_cv2 (clip._resolve_framing) raises ToolchainMissingError instead of silently centre-cropping every
# clip while the operator believes subject-tracking happened. OFF stays byte-identical (cv2 never consulted).
# These run in the hermetic unit job (cv2 genuinely absent) by stubbing framing._cv2 -> None.
def test_resolve_raises_when_smart_framing_on_and_cv2_absent(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    from fanops.errors import ToolchainMissingError
    monkeypatch.setattr(framing, "require_cv2", _REAL_REQUIRE_CV2)   # override the autouse no-op: exercise the REAL guard
    cfg = Config(root=tmp_path); src = _talk_src()           # smart_framing default ON
    monkeypatch.setattr(framing, "_cv2", lambda: None)        # extra absent (the CI-unit reality)
    with pytest.raises(ToolchainMissingError):
        _resolve_framing(cfg, src, 0.0, 10.0)

def test_resolve_raises_when_cv2_present_but_detector_unbuildable(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    from fanops.errors import ToolchainMissingError
    monkeypatch.setattr(framing, "require_cv2", _REAL_REQUIRE_CV2)   # override the autouse no-op: exercise the REAL guard
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(framing, "_cv2", lambda: object())    # cv2 "there" but no FaceDetectorYN
    monkeypatch.setattr(framing, "_detector", lambda cv2: None)
    with pytest.raises(ToolchainMissingError):
        _resolve_framing(cfg, src, 0.0, 10.0)

def test_resolve_off_never_touches_cv2(tmp_path, monkeypatch):
    from fanops.clip import _resolve_framing
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    cfg = Config(root=tmp_path); src = _talk_src()
    def _boom(): raise AssertionError("cv2 consulted while smart_framing OFF")
    monkeypatch.setattr(framing, "_cv2", _boom)               # must NOT be called
    assert _resolve_framing(cfg, src, 0.0, 10.0) == (None, None, None)

def test_require_cv2_passes_when_stubbed_present(tmp_path, monkeypatch):
    monkeypatch.setattr(framing, "require_cv2", _REAL_REQUIRE_CV2)   # override the autouse no-op: exercise the REAL guard
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    framing.require_cv2(cfg)                                  # no raise == pass

def test_supercut_span_entries_raises_when_smart_framing_on_and_cv2_absent(tmp_path, monkeypatch):
    # Call site #1: the S3 supercut path loops _resolve_framing per span; it must inherit the guard and
    # raise on the FIRST span (never partially render). Two spans make "raises before finishing" observable.
    from fanops.clip import _supercut_span_entries
    from fanops.errors import ToolchainMissingError
    monkeypatch.setattr(framing, "require_cv2", _REAL_REQUIRE_CV2)   # override the autouse no-op: exercise the REAL guard
    cfg = Config(root=tmp_path); src = _talk_src()           # smart_framing default ON
    monkeypatch.setattr(framing, "_cv2", lambda: None)        # extra absent
    with pytest.raises(ToolchainMissingError):
        _supercut_span_entries(cfg, src, [(0.0, 3.0), (5.0, 8.0)])


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
    # _detect_faces now returns 5-tuples (cx,cy,fh,ey,score) — score is the YuNet confidence at f[14].
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
    assert len(face) == 5, f"expected 5-tuple (cx,cy,fh,ey,score), got {face}"
    assert face[4] > 0.0, "score must be >0 for a high-confidence face"

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
    fx, fy, fh, ey = framing.subject_focus(cfg, src, start=10.0, end=14.0)
    assert abs(fx - 0.30) < 0.01, f"real speaker at x=0.30 must win; got fx={fx}"

def test_detect_window_stores_score_in_sidecar(tmp_path, monkeypatch):
    # the detect sidecar must store 5-element faces so _pick_dominant_face can use the score on cache hit.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_frames_grid", lambda *a, **k: ["g0"])
    monkeypatch.setattr(framing, "_detect_faces", lambda cv2, det, fp: [(0.5, 0.5, 0.2, 0.45, 0.88)])
    st = framing.detect_window(cfg, src, start=10.0, end=14.0)
    assert st is not None
    assert len(st["frames"][0][0]) == 5, "detect sidecar must persist 5-element faces (including score)"
    sidecar = cfg.agent_io / "framing" / "s1.detect.json"
    cached = json.loads(sidecar.read_text())
    assert cached["v"] == framing._DETECT_V
    assert len(cached["windows"]["10.0-14.0"]["frames"][0][0]) == 5
