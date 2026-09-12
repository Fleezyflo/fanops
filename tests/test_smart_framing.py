# tests/test_smart_framing.py — Smart framing (subject-aware reframe). The 9:16 crop SLIDES onto the
# detected subject instead of the blind top/center guess: framing.subject_focus returns a normalized
# centroid, clip.reframe_filter turns it into a clamped crop offset, and both render paths thread it
# through ffmpeg_clip_cmd + the render fingerprint. When smart_framing is ON, cv2 is REQUIRED:
# _resolve_framing / require_cv2 raise ToolchainMissingError rather than silently centre-crop.
# Unattributed detection None is UNRESOLVED/UNKNOWN, not a centre 3-tuple as success. Detection
# inputs are seeded via detect/track/saliency sidecars (write-path); ffmpeg is stubbed at subprocess.run.
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
from fanops.errors import ToolchainMissingError
from fanops.framing_outcomes import FramingEventType as _FE, FramingOutcome as _FO


@pytest.fixture(autouse=True)
def _clear_yunet_cache():
    framing._reset_yunet_cache()
    yield
    framing._reset_yunet_cache()


def _write_detect(cfg, src_id, start, end, stats):
    p = cfg.agent_io / "framing" / f"{src_id}.detect.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"v": framing._DETECT_V, "windows": {f"{round(start, 2)}-{round(end, 2)}": stats}}))


def _write_track(cfg, src_id, start, end, track):
    p = cfg.agent_io / "framing" / f"{src_id}.track.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"v": framing._SIDECAR_V, "windows": {f"{round(start, 2)}-{round(end, 2)}": track}}))


def _write_saliency(cfg, src_id, start, end, sal):
    p = cfg.agent_io / "framing" / f"{src_id}.saliency.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"v": framing._SIDECAR_V, "windows": {f"{round(start, 2)}-{round(end, 2)}": sal}}))


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


# ---------------------------------------------------------------- subject_focus (cache + miss) ----
def test_require_cv2_is_fail_closed(tmp_path):
    # No framing._cv2 patch. Missing extra/model/detector → ToolchainMissingError; present toolchain builds.
    framing.require_cv2(Config(root=tmp_path))

def test_subject_focus_non_positive_window_is_none(tmp_path):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080)
    assert framing.subject_focus(cfg, src, start=5.0, end=5.0) is None

def test_subject_focus_returns_median_quad(tmp_path):
    # shape (fx,fy,fh,ey,fw): the dominant (largest-fh) face's median over the window. Legacy 4-tuple stats
    # carry no width, so fw is None (the clip geometry then falls back to today's centering on that axis).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.8, 0.4, 0.2, 0.36]]] * 4 + [[]]}        # 4 of 5 frames have a face
    _write_detect(cfg, "s1", 10.0, 14.0, stats)
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) == (0.8, 0.4, 0.2, 0.36, None)

def test_subject_focus_picks_dominant_largest_face(tmp_path):
    # two faces per frame -> the LARGER (fh) one is the subject for the static lock.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.2, 0.5, 0.10, 0.45], [0.8, 0.5, 0.30, 0.40]]] * 4}
    _write_detect(cfg, "s1", 10.0, 14.0, stats)
    fx, fy, fh, ey, fw = framing.subject_focus(cfg, src, start=10.0, end=14.0)
    assert fx == 0.8 and fh == 0.30                                             # the bigger face wins

def test_subject_focus_low_confidence_is_none(tmp_path):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.8, 0.4, 0.2, 0.36]]] + [[]] * 4}        # 1 of 5 -> conf 0.2 < 0.34
    _write_detect(cfg, "s1", 10.0, 14.0, stats)
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) is None

def test_subject_focus_no_detection_is_none(tmp_path):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    _write_detect(cfg, "s1", 10.0, 14.0, {"fps": 4.0, "frames": [[]] * 5})
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) is None


# ---------------------------------------------------------------- YuNet detector (v2) ----
def test_vendored_yunet_model_ships_in_package():
    # the detector is useless without its model; assert the vendored asset is present + non-trivial.
    mp = framing._model_path()
    assert mp.exists() and mp.suffix == ".onnx" and mp.stat().st_size > 100_000

def test_track_sidecar_stale_version_invalidated(tmp_path):
    # an older track sidecar (pre face-height/eyeline schema) must NOT be trusted -> recompute.
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"v": framing._SIDECAR_V - 1, "windows": {"10.0-14.0": [[0.0, 5.0, 0.5, 0.5]]}}))
    assert framing._load_cache(p) == {}                   # version mismatch -> empty -> re-probe

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
    # Plan E L4b: low-logprob junk ASR + face must NOT route to talk (music or silent only).
    from tests.fixtures.speech_segments import LOW_LOGPROB
    src = _talk_src(transcript=[{**LOW_LOGPROB, "start": 10.0, "end": 13.5}])
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


# ---------------------------------------------------------------- _resolve_framing strategy router (sidecars) ----
def test_resolve_multi_uses_track(tmp_path):
    from tests.fixtures.speech_segments import talk_seg
    cfg = Config(root=tmp_path)
    src = _talk_src(transcript=[talk_seg("so tell me about your new record", start=0.0, end=8.0)])
    two = {"fps": 4.0, "frames": [[[0.22, 0.5, 0.2, 0.45, 0.9, 0.12],
                                   [0.80, 0.45, 0.2, 0.40, 0.9, 0.12]]] * 4}
    track = [(0.0, 5.0, 0.22, 0.5, 0.2, 0.45), (5.0, 10.0, 0.8, 0.45, 0.2, 0.4)]
    _write_detect(cfg, src.id, 0.0, 10.0, two)
    _write_track(cfg, src.id, 0.0, 10.0, track)
    _write_saliency(cfg, src.id, 0.0, 10.0, [])
    focus, got, ct = clipmod._resolve_framing(cfg, src, 0.0, 10.0)
    assert got and focus is None and ct == framing.CT_MULTI

def test_resolve_single_uses_focus(tmp_path):
    from tests.fixtures.speech_segments import talk_seg
    cfg = Config(root=tmp_path)
    src = _talk_src(transcript=[talk_seg("let me explain how this works", start=0.0, end=8.0)])
    stats = {"fps": 4.0, "frames": [[[0.6, 0.45, 0.25, 0.4, 0.9, 0.14]]] * 4}
    _write_detect(cfg, src.id, 0.0, 10.0, stats)
    _write_track(cfg, src.id, 0.0, 10.0, [])
    _write_saliency(cfg, src.id, 0.0, 10.0, [])
    focus, track, ct = clipmod._resolve_framing(cfg, src, 0.0, 10.0)
    assert focus is not None and abs(focus[0] - 0.6) < 0.01 and track is None and ct == framing.CT_SINGLE

def test_resolve_no_people_unattributed_miss_is_unresolved(tmp_path):
    cfg = Config(root=tmp_path); src = _talk_src()
    _write_detect(cfg, src.id, 0.0, 10.0, None)
    _write_saliency(cfg, src.id, 0.0, 10.0, [])
    r = framing._resolve(cfg, src, 0.0, 10.0, capture_failures=True)
    assert r.final_outcome is _FO.UNRESOLVED and r.root_cause is _FE.UNKNOWN

def test_resolve_smart_framing_off_never_constructs(tmp_path, monkeypatch):
    import cv2
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    def _boom(*a, **k):
        raise AssertionError("YuNet constructed while smart_framing is OFF")
    monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(_boom))
    framing._reset_yunet_cache()
    cfg = Config(root=tmp_path); src = _talk_src()
    assert clipmod._resolve_framing(cfg, src, 0.0, 10.0) == (None, None, None)


# ================================================ smart_framing: ONE-CONSTRUCTION, FAIL-LOUD prerequisite ====
# Contract: when smart_framing is ON (production default), the REAL YuNet detector is built ONCE per process
# (framing._framing_runtime_or_raise / _detector cache) and reused for every render_moment / _resolve_framing.
# A BROKEN PREREQUISITE — cv2 absent, FaceDetectorYN/.create missing, model file absent, FaceDetectorYN.create()
# returning None, or FaceDetectorYN.create() raising — REFUSES loudly with ToolchainMissingError BEFORE any
# centered output. An unattributed detection miss is UNRESOLVED/UNKNOWN, not a centre 3-tuple as success.
# Refusals are induced at the OpenCV constructor / model-path edge, never by patching framing._cv2 to None.

def _break_yunet_create(monkeypatch, *, result=None, exc=None):
    import cv2
    if exc is not None:
        def _boom(*a, **k):
            raise exc
        monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(_boom))
    else:
        monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(lambda *a, **k: result))
    framing._reset_yunet_cache()


def test_resolve_refuses_when_facedetector_attr_missing(tmp_path, monkeypatch):
    import cv2
    cfg = Config(root=tmp_path); src = _talk_src()
    monkeypatch.setattr(cv2, "FaceDetectorYN", None)
    framing._reset_yunet_cache()
    with pytest.raises(ToolchainMissingError):
        clipmod._resolve_framing(cfg, src, 0.0, 10.0)

def test_resolve_refuses_when_model_missing(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); src = _talk_src()
    orig = Path.exists
    def exists(self):
        if self.name == framing._MODEL:
            return False
        return orig(self)
    monkeypatch.setattr(Path, "exists", exists)
    framing._reset_yunet_cache()
    with pytest.raises(ToolchainMissingError):
        clipmod._resolve_framing(cfg, src, 0.0, 10.0)

def test_resolve_refuses_when_constructor_returns_none(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); src = _talk_src()
    _break_yunet_create(monkeypatch, result=None)
    with pytest.raises(ToolchainMissingError):
        clipmod._resolve_framing(cfg, src, 0.0, 10.0)

def test_resolve_refuses_when_constructor_raises(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); src = _talk_src()
    _break_yunet_create(monkeypatch, exc=RuntimeError("corrupt ONNX / OpenCV ABI mismatch"))
    with pytest.raises(ToolchainMissingError):
        clipmod._resolve_framing(cfg, src, 0.0, 10.0)

def test_render_moment_refuses_on_constructor_failure(tmp_path, monkeypatch):
    from fanops.clip import render_moment
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    _break_yunet_create(monkeypatch, result=None)
    with pytest.raises(ToolchainMissingError):
        render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)

def test_render_account_cut_refuses_on_constructor_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    _break_yunet_create(monkeypatch, result=None)
    with pytest.raises(ToolchainMissingError):
        render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="talk", hook="", out_path=str(cfg.clips / "acct.mp4"))

def test_supercut_span_entries_refuses_on_missing_prereq(tmp_path, monkeypatch):
    from fanops.clip import _supercut_span_entries
    cfg = Config(root=tmp_path); src = _talk_src()
    _break_yunet_create(monkeypatch, result=None)
    with pytest.raises(ToolchainMissingError):
        _supercut_span_entries(cfg, src, [(0.0, 3.0), (5.0, 8.0)])

# (10) the REAL installed OpenCV path passes (integration: needs the [framing] extra + vendored model)
@pytest.mark.integration
def test_real_opencv_runtime_constructs(tmp_path):
    # In the e2e/base lanes cv2 is genuinely installed; the runtime must build the detector without raising.
    rt = framing._framing_runtime_or_raise(Config(root=tmp_path))
    assert rt.cv2 is not None and rt.detector is not None

def test_framing_runtime_reuses_yunet_once_per_process(tmp_path, monkeypatch):
    import cv2
    from concurrent.futures import ThreadPoolExecutor
    creates = {"n": 0}
    orig = cv2.FaceDetectorYN.create
    def _create(*a, **k):
        creates["n"] += 1
        return orig(*a, **k)
    monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(_create))
    cfg = Config(root=tmp_path)
    framing._reset_yunet_cache()

    def _once(_):
        return framing._framing_runtime_or_raise(cfg)
    _once(None); _once(None)
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(_once, range(2)))

    assert creates["n"] == 1, "FaceDetectorYN.create must run once per process, not per resolution"
    rt_a = framing._framing_runtime_or_raise(cfg)
    rt_b = framing._framing_runtime_or_raise(cfg)
    assert rt_a is rt_b
    assert rt_a.detector is rt_b.detector

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
    from tests.fixtures.speech_segments import talk_seg
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0,
                          transcript=[talk_seg("let me explain how this works", start=10.0, end=13.5)]))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10, end=14, reason="r", state=MomentState.clipped))
    stats = {"fps": 4.0, "frames": [[[0.8, 0.5, 0.2, 0.45]]] * 4}
    _write_detect(cfg, "src_1", 10, 14, stats)
    _write_track(cfg, "src_1", 10, 14, [])
    _write_saliency(cfg, "src_1", 10, 14, [])
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    ok, _ = render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="talk",
                               hook="", out_path=str(cfg.clips / "acct.mp4"))
    centred = "crop=ih*1080/1920:ih,scale=1080:1920,setsar=1"
    assert ok and "cmd" in captured and _vf_of(captured["cmd"]) != centred

def test_account_cut_off_flag_is_centered(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")                             # flag OFF -> focus never resolved
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
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
    """MOL-196: the REAL OpenCV/YuNet path. Fail-closed: ImportError / ToolchainMissingError is the
    signal (no unit skip). FANOPS_REQUIRE_E2E already converts integration skips to failures."""
    import cv2
    import numpy as np
    framing.require_cv2(Config(root=tmp_path))
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

def test_detect_faces_includes_score(tmp_path):
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

def test_classify_phantom_decoy_routes_to_single():
    # end-to-end: phantom wall-art face next to a real speaker must NOT trigger multi-speaker switching.
    from tests.fixtures.speech_segments import talk_seg
    src = _talk_src(transcript=[talk_seg("here is my take on this", start=10.0, end=13.5)])
    real_face = [0.30, 0.50, 0.25, 0.45, 0.87]
    phantom   = [0.75, 0.48, 0.06, 0.43, 0.64]
    st = _stats([[real_face, phantom]] * 4)
    ct = framing.classify_window(None, src, start=10.0, end=14.0, stats=st)
    assert ct == framing.CT_SINGLE, f"phantom decoy must route to SINGLE, got {ct!r}"

def test_subject_focus_picks_real_speaker_over_phantom(tmp_path):
    # off-center real speaker (score=0.87) must win over phantom decoy (score=0.64) as subject focus.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    real_face = [0.30, 0.50, 0.25, 0.45, 0.87]   # real speaker at x=0.30
    phantom   = [0.75, 0.48, 0.06, 0.43, 0.64]   # phantom near right edge
    stats = {"fps": 4.0, "frames": [[real_face, phantom]] * 4}
    _write_detect(cfg, "s1", 10.0, 14.0, stats)
    fx, fy, fh, ey, fw = framing.subject_focus(cfg, src, start=10.0, end=14.0)
    assert abs(fx - 0.30) < 0.01, f"real speaker at x=0.30 must win; got fx={fx}"

def test_detect_window_round_trips_score_in_sidecar(tmp_path):
    # the detect sidecar must store 6-element faces (cx,cy,fh,ey,score,fw) so _pick_dominant_face uses the
    # score AND the geometry can use the width on a cache hit (E1).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    stats = {"fps": 4.0, "frames": [[[0.5, 0.5, 0.2, 0.45, 0.88, 0.15]]]}
    _write_detect(cfg, "s1", 10.0, 14.0, stats)
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
