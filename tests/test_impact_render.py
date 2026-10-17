# tests/test_impact_render.py — M4 (structural-hooks): the render cut-window OVERRIDE on render_moment.
# render_moment derives cs/ce from m.start/m.end today; an impact-cut needs to inject a peak-derived
# window AND a DISTINCT clip id (a stitch is a new clip, never an in-place swap of the bare clip) AND a
# born state (stitch_draft, structurally unpostable). A stitched render is valid only if its actual
# duration is within DURATION_TOLERANCE of expected (cut_end - cut_start) — a short/empty container that
# passes "size > 0" must fail this check. The DEFAULT (no cut_window) path must stay byte-identical.
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, ClipState, Fmt
from fanops.clip import render_moment
from fanops.ids import child_id
import fanops.overlay as overlay


def _seed(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"), width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0.0, end=18.0, reason="r",
                          state=MomentState.decided))
    return led


def _stub_ffmpeg(mocker, *, dur=9.6):
    """Hermetic ffmpeg + ffprobe (shared stdlib subprocess). Drives real _probe_duration."""
    overlay._TEXTFILTER_CACHE = None

    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "ffprobe":
            class R:
                returncode = 0
                stdout = f"1920\n1080\n{dur}\n"
                stderr = ""
            return R()
        if cmd and cmd[0] == "ffmpeg" and "-filters" in cmd:
            class R:
                returncode = 0
                stdout = "Filters:\n"
                stderr = ""
            return R()
        if cmd and not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"CLIPBYTES")
        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)


def test_cut_window_override_builds_distinct_stitch_clip(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    _stub_ffmpeg(mocker, dur=9.6)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16,
                              cut_window=(0.0, 9.6), clip_id="stitch_x", born_state=ClipState.stitch_draft)
    assert clip.id == "stitch_x"                          # the caller's distinct id, NOT child_id(...)
    assert clip.state is ClipState.stitch_draft           # born unpostable
    assert clip.cut_seconds == 9.6                        # window honored (round(ce-cs,3))
    assert Path(clip.path).exists() and Path(clip.path).read_bytes() == b"CLIPBYTES"


def test_cut_window_does_not_touch_moment_state(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    led.set_moment_state("mom_1", MomentState.clipped)    # the bare clip already clipped this moment
    _stub_ffmpeg(mocker, dur=9.6)
    led, clip = render_moment(led, cfg, "mom_1", cut_window=(0.0, 9.6), clip_id="stitch_x",
                              born_state=ClipState.stitch_draft)
    assert led.moments["mom_1"].state is MomentState.clipped   # stitch render leaves the moment alone
    assert clip.state is ClipState.stitch_draft


def test_duration_check_fails_visibly_on_short_render(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    _stub_ffmpeg(mocker, dur=2.0)  # expected 9.6, far outside tolerance
    led, clip = render_moment(led, cfg, "mom_1", cut_window=(0.0, 9.6), clip_id="stitch_x",
                              born_state=ClipState.stitch_draft)
    assert clip.state is ClipState.error
    assert "duration" in (clip.error_reason or "")
    assert not (cfg.clips / "stitch_x.render.json").exists()   # no skip-stamp on a failed render


def test_duration_check_passes_within_tolerance(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    _stub_ffmpeg(mocker, dur=9.3)  # 0.3s off < DURATION_TOLERANCE(0.5)
    led, clip = render_moment(led, cfg, "mom_1", cut_window=(0.0, 9.6), clip_id="stitch_x",
                              born_state=ClipState.stitch_draft)
    assert clip.state is ClipState.stitch_draft


def test_bare_render_unchanged_without_cut_window(tmp_path, mocker, monkeypatch):
    # REGRESSION guard: the default path is untouched — content-addressed cid, rendered state, moment clipped
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    _stub_ffmpeg(mocker, dur=9.6)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.id == child_id("clip", "mom_1", "9:16")
    assert clip.state is ClipState.rendered
    assert led.moments["mom_1"].state is MomentState.clipped
    assert Path(clip.path).exists()


def test_stitch_cid_differs_from_bare(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = _seed(cfg)
    _stub_ffmpeg(mocker, dur=9.6)
    led, bare = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    led, stitch = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16,
                                cut_window=(0.0, 9.6), clip_id="stitch_x", born_state=ClipState.stitch_draft)
    assert bare.id != stitch.id and bare.id in led.clips and stitch.id in led.clips
