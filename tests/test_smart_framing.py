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

def test_subject_focus_returns_median_centroid(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())                      # pretend the extra is installed
    monkeypatch.setattr("fanops.keyframes.extract_keyframes", lambda *a, **k: ["f0", "f1", "f2", "f3", "f4"])
    monkeypatch.setattr(framing, "_detect_centroids", lambda cv2, frames: [(0.8, 0.4), (0.82, 0.42), (0.78, 0.38), (0.8, 0.4)])
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) == (0.8, 0.4)  # 4 of 5 frames -> conf 0.8 >= 0.34

def test_subject_focus_low_confidence_is_none(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr("fanops.keyframes.extract_keyframes", lambda *a, **k: ["f0", "f1", "f2", "f3", "f4"])
    monkeypatch.setattr(framing, "_detect_centroids", lambda cv2, frames: [(0.8, 0.4)])   # 1 of 5 -> conf 0.2 < 0.34
    assert framing.subject_focus(cfg, src, start=10.0, end=14.0) is None

def test_subject_focus_caches_to_sidecar_and_skips_reprobe(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    calls = {"n": 0}
    def _extract(*a, **k):
        calls["n"] += 1
        return ["f0", "f1", "f2", "f3", "f4"]
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr("fanops.keyframes.extract_keyframes", _extract)
    monkeypatch.setattr(framing, "_detect_centroids", lambda cv2, frames: [(0.5, 0.5)] * 5)
    first = framing.subject_focus(cfg, src, start=10.0, end=14.0)
    second = framing.subject_focus(cfg, src, start=10.0, end=14.0)        # window key matches -> cache hit
    assert first == second == (0.5, 0.5)
    assert calls["n"] == 1                                                # the SECOND call never re-probed
    sidecar = cfg.agent_io / "framing" / "s1.json"
    assert sidecar.exists() and json.loads(sidecar.read_text())["windows"]["10.0-14.0"]["fx"] == 0.5


# ---------------------------------------------------------------- YuNet detector (v2) ----
def test_vendored_yunet_model_ships_in_package():
    # the detector is useless without its model; assert the vendored asset is present + non-trivial.
    mp = framing._model_path()
    assert mp.exists() and mp.suffix == ".onnx" and mp.stat().st_size > 100_000

def test_detect_centroids_no_model_is_empty(monkeypatch, tmp_path):
    # model asset missing -> _detector None -> [] (fail-open to center crop), never raises.
    monkeypatch.setattr(framing, "_model_path", lambda: tmp_path / "absent.onnx")
    assert framing._detect_centroids(object(), ["f0", "f1"]) == []

def test_detector_none_on_old_cv2_without_yunet(monkeypatch, tmp_path):
    # an OpenCV too old to expose FaceDetectorYN -> None, not an AttributeError crash.
    monkeypatch.setattr(framing, "_model_path", lambda: tmp_path / "m.onnx")
    (tmp_path / "m.onnx").write_bytes(b"x" * 200_000)
    class OldCv2: pass                                    # no FaceDetectorYN attribute
    assert framing._detector(OldCv2()) is None

def test_sidecar_v1_cache_is_invalidated_by_v2(tmp_path):
    # the Haar-era (v1) all-null sidecars must NOT be trusted now the detector changed -> recompute.
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"v": 1, "windows": {"10.0-14.0": {"fx": 0.5, "fy": 0.5}}}))
    assert framing._load_cache(p) == {}                   # version mismatch -> empty -> re-probe

def test_subject_focus_samples_at_detection_resolution(tmp_path, monkeypatch):
    # faces are undetectable at the 480px hook-author default; detection must request higher-res frames.
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    seen = {}
    def _extract(*a, **k):
        seen["width"] = k.get("width")
        return ["f0", "f1", "f2", "f3", "f4"]
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr("fanops.keyframes.extract_keyframes", _extract)
    monkeypatch.setattr(framing, "_detect_centroids", lambda cv2, frames: [(0.5, 0.5)] * 5)
    framing.subject_focus(cfg, src, start=10.0, end=14.0)
    assert seen["width"] == framing._KF_WIDTH and framing._KF_WIDTH >= 960


# ---------------------------------------------------------------- active-speaker track (time-varying crop) ----
def test_time_expr_builds_escaped_nested_if():
    from fanops.clip import _time_expr
    assert _time_expr([], [400]) == "400"                                   # single value -> no if
    assert _time_expr([4.33], [163, 1195]) == "if(lt(t\\,4.33)\\,163\\,1195)"
    assert _time_expr([4.33, 8.67], [163, 1195, 147]) == \
        "if(lt(t\\,4.33)\\,163\\,if(lt(t\\,8.67)\\,1195\\,147))"           # commas escaped for filtergraph

def test_reframe_track_cuts_x_over_time():
    track = [(0.0, 5.0, 0.22, 0.5), (5.0, 10.0, 0.80, 0.45)]               # left then right speaker
    vf = reframe_filter("9:16", 1920, 1080, track=track)
    assert vf.startswith("crop=w=ih*1080/1920:h=ih:x=if(lt(t\\,5.0)\\,")    # time-varying x crop
    assert "118" in vf and "1232" in vf and vf.endswith("scale=1080:1920,setsar=1")   # fx .22->x118, .80->x1232

def test_reframe_track_overrides_static_focus():
    track = [(0.0, 5.0, 0.22, 0.5), (5.0, 10.0, 0.80, 0.45)]
    vf = reframe_filter("9:16", 1920, 1080, focus=(0.5, 0.5), track=track)
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

def test_speaker_track_no_extra_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(framing, "_cv2", lambda: None)                     # cv2 absent (CI) -> static path
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    assert framing.speaker_track(cfg, src, start=10.0, end=30.0, src_w=1920, src_h=1080) is None

def test_speaker_track_follows_active_speaker(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_keyframes", lambda *a, **k: ["f0", "f1", "f2", "f3"])
    seq = [{"L": ((0.22, 0.5), 50.0), "R": ((0.80, 0.45), 10.0)} if i < 5    # first half: LEFT louder
           else {"L": ((0.22, 0.5), 10.0), "R": ((0.80, 0.45), 50.0)} for i in range(10)]  # second half: RIGHT
    n = {"i": 0}
    def _fake_bin(cv2, det, frames):
        d = seq[n["i"]]; n["i"] += 1; return d
    monkeypatch.setattr(framing, "_bin_active", _fake_bin)
    tr = framing.speaker_track(cfg, src, start=0.0, end=20.0, src_w=1920, src_h=1080)
    assert tr is not None and len(tr) == 2                                  # merged into LEFT-then-RIGHT
    assert abs(tr[0][2] - 0.22) < 0.01 and abs(tr[1][2] - 0.80) < 0.01      # follows the speaker
    assert tr[0][0] == 0.0 and tr[-1][1] == 20.0                            # covers the whole window

def test_speaker_track_one_dominant_face_is_none(tmp_path, monkeypatch):
    # both visible but the SAME person always talks -> one position -> None (static focus is identical).
    cfg = Config(root=tmp_path)
    src = Source(id="s1", source_path="x.mp4", width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(framing, "_cv2", lambda: object())
    monkeypatch.setattr(framing, "_detector", lambda cv2: object())
    monkeypatch.setattr("fanops.keyframes.extract_keyframes", lambda *a, **k: ["f0", "f1", "f2", "f3"])
    monkeypatch.setattr(framing, "_bin_active", lambda *a: {"L": ((0.22, 0.5), 50.0), "R": ((0.80, 0.45), 5.0)})
    assert framing.speaker_track(cfg, src, start=0.0, end=20.0, src_w=1920, src_h=1080) is None


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
