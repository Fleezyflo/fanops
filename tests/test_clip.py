from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, ClipState, Fmt
from fanops.clip import ffmpeg_clip_cmd, reframe_filter, render_moment, render_aspects_for

def test_clip_cmd_seek_is_output_relative_and_reframes():
    cmd = ffmpeg_clip_cmd("/s/x.mp4", "/o/c.mp4", 1.5, 8.0, "9:16", src_w=1920, src_h=1080)
    # -ss BEFORE -i (fast seek), -to AFTER -i (output-relative, version-stable)
    assert cmd.index("-ss") < cmd.index("-i") < cmd.index("-to")
    assert "1.5" in cmd and "6.5" in cmd          # -to is output-relative DURATION (end-start), not absolute end
    assert "8.0" not in cmd                        # FIX F39: absolute end must NOT be emitted (caused version-fragile cuts)
    assert any("crop" in p or "scale" in p for p in cmd)
    assert cmd[-1] == "/o/c.mp4"

def test_reframe_filter_handles_vertical_source():
    # wide source -> crop to 9:16; already-vertical -> scale/pad, never negative crop
    wide = reframe_filter("9:16", 1920, 1080)
    tall = reframe_filter("9:16", 1080, 1920)
    assert "crop" in wide or "scale" in wide
    assert "crop=ih*9/16" not in tall or "1080:1920" in tall  # no impossible crop on tall src

def test_render_moment_creates_clip_with_aspect(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided))
    def fake_run(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.parent_id == "mom_1" and clip.state is ClipState.rendered
    assert clip.aspect is Fmt.r9x16 and clip.id in led.clips
    assert led.moments["mom_1"].state is MomentState.clipped

def test_render_aspects_for_makes_one_clip_per_distinct_aspect(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided))
    def fake_run(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led, clips = render_aspects_for(led, cfg, "mom_1", aspects={Fmt.r9x16, Fmt.r16x9})
    assert {c.aspect for c in clips} == {Fmt.r9x16, Fmt.r16x9}
    assert len(clips) == 2

def test_render_skips_retired_moment(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.retired))
    spy = mocker.patch("fanops.clip.subprocess.run")
    led, clips = render_aspects_for(led, cfg, "mom_1", aspects={Fmt.r9x16})
    assert clips == []
    spy.assert_not_called()

def test_render_moment_records_error_on_ffmpeg_failure(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided))
    def fail_run(cmd, **kw):
        class R: returncode = 1; stderr = "boom: no such file"
        return R()   # note: writes NO output file
    mocker.patch("fanops.clip.subprocess.run", side_effect=fail_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.error
    assert "boom" in (clip.error_reason or "")
    assert clip.id in led.clips
    # moment must NOT advance to clipped on failure (so a re-run retries)
    assert led.moments["mom_1"].state is MomentState.decided

def test_render_moment_records_error_when_ffmpeg_absent(tmp_path, mocker):
    # ffmpeg off PATH -> subprocess.run raises FileNotFoundError BEFORE the process starts
    # (check=False suppresses a nonzero RETURNCODE, not a pre-launch FileNotFoundError). This
    # must fail-safe exactly like the nonzero-rc branch: record ClipState.error and leave the
    # moment at `decided` so the existing re-render path retries when ffmpeg returns — NOT a
    # raised exception (which the pipeline would quarantine into the terminal MomentState.error).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided))
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.clip.subprocess.run", side_effect=absent)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)   # must NOT raise
    assert clip.state is ClipState.error
    assert "toolchain missing: ffmpeg" in (clip.error_reason or "")
    assert clip.id in led.clips
    # moment stays retriable (NOT MomentState.error, NOT clipped) — re-run renders again
    assert led.moments["mom_1"].state is MomentState.decided

def test_reframe_branches_exact():
    # 16:9 from a tall source -> crop height then scale to even 1920x1080
    assert reframe_filter("16:9", 1080, 1920) == "crop=iw:iw*1080/1920,scale=1920:1080,setsar=1"
    # 1:1 from a wide source -> crop width (square from height) then scale
    assert reframe_filter("1:1", 1920, 1080) == "crop=ih*1080/1080:ih,scale=1080:1080,setsar=1"
    # unknown source dims -> scale+pad fallback, never a crop
    unknown = reframe_filter("9:16", 0, 0)
    assert "pad=" in unknown and "crop" not in unknown
    # near-exact aspect match -> scale only (no crop)
    assert reframe_filter("16:9", 1920, 1080) == "scale=1920:1080,setsar=1"
