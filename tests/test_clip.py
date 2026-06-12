import subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, ClipState, Fmt
from fanops.clip import ffmpeg_clip_cmd, reframe_filter, render_moment, render_aspects_for
from fanops import overlay


def _vf_of(cmd: list[str]) -> str:
    """Return the value passed to the (last) -vf flag in an ffmpeg cmd list."""
    return cmd[cmd.index("-vf") + 1]

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
        # FLAG last-arg (capability probe) is not an output path — see the b"X" stub above
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
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
        # a FLAG last-arg (e.g. the `ffmpeg -filters` capability probe) is NOT an output path —
        # writing it would drop a junk `-filters` file into the repo root on every suite run
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
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

def test_ffmpeg_clip_cmd_appends_extra_vf():
    # extra_vf is chained AFTER the reframe with a comma; default None keeps old behavior.
    cmd = ffmpeg_clip_cmd("/s/x.mp4", "/o/c.mp4", 1.5, 8.0, "9:16",
                          src_w=1920, src_h=1080, extra_vf="subtitles=x.ass")
    vf = _vf_of(cmd)
    assert vf.endswith(",subtitles=x.ass")                 # reframe first, then the chained filter
    assert reframe_filter("9:16", 1920, 1080) in vf        # reframe still present, un-mangled
    # default None == old behavior: exactly the reframe, no trailing comma/filter
    plain = ffmpeg_clip_cmd("/s/x.mp4", "/o/c.mp4", 1.5, 8.0, "9:16", src_w=1920, src_h=1080)
    assert _vf_of(plain) == reframe_filter("9:16", 1920, 1080)


def test_render_burns_subtitles_when_enabled(tmp_path, mocker, monkeypatch):
    # source WITH a transcript + a moment; burn_subs ON; ffmpeg HAS the text filter ->
    # the -vf must chain "subtitles=" after the reframe AND an .ass file is written to disk.
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080,
                          transcript=[{"start": 0.0, "end": 3.0, "text": "hello world"},
                                      {"start": 3.0, "end": 6.0, "text": "second line"}]))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided,
                          hook="big hook"))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # FLAG last-arg (capability probe) is not an output path — see the b"X" stub above
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    vf = _vf_of(captured["cmd"])
    assert "subtitles=" in vf                               # the burn filter was chained
    assert reframe_filter("9:16", 1920, 1080) in vf         # ... after the reframe
    # an .ass file was written adjacent to the clip
    ass_files = list(cfg.clips.glob("*.ass"))
    assert ass_files, "expected a written .ass subtitle file"
    assert ass_files[0].read_text(encoding="utf-8").startswith("[Script Info]")


def test_render_failopen_when_no_textfilter(tmp_path, mocker, monkeypatch):
    # burn_subs ON but ffmpeg LACKS the text filter -> NO "subtitles=" in -vf, the clip still
    # renders, and exactly ONE warning is logged. NEVER raises.
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080,
                          transcript=[{"start": 0.0, "end": 3.0, "text": "hello world"}]))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided, hook="hook"))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # FLAG last-arg (capability probe) is not an output path — see the b"X" stub above
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)   # must NOT raise
    assert clip.state is ClipState.rendered                          # clip still renders
    vf = _vf_of(captured["cmd"])
    assert "subtitles=" not in vf                                    # plain reframe only
    assert vf == reframe_filter("9:16", 1920, 1080)
    assert not list(cfg.clips.glob("*.ass"))                         # no .ass written
    # one warning logged about the missing text filter
    log = cfg.log_path.read_text()
    assert "subtitles" in log.lower() and "without" in log.lower()


def test_render_failopen_when_no_transcript(tmp_path, mocker, monkeypatch):
    # burn_subs ON, ffmpeg HAS the filter, but the source transcript is None -> no "subtitles="
    # in -vf, the clip still renders. (Empty transcript == nothing to burn; not an error.)
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, transcript=None))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided, hook="hook"))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # FLAG last-arg (capability probe) is not an output path — see the b"X" stub above
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    assert "subtitles=" not in _vf_of(captured["cmd"])
    assert not list(cfg.clips.glob("*.ass"))


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


def test_render_moment_records_error_when_ffmpeg_hangs(tmp_path, mocker):
    # A HUNG ffmpeg (corrupt input, stuck filesystem) is worse than an absent one: render_moment
    # runs INSIDE advance()'s ledger transaction, so an unbounded subprocess held the flock against
    # every other pass and Studio write. The run must carry a hard timeout=, and TimeoutExpired must
    # fail-safe exactly like the absent/nonzero-rc branches: ClipState.error + moment stays
    # `decided` (retriable on re-run), never a raise.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided))
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.clip.subprocess.run", side_effect=hung)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)   # must NOT raise
    assert clip.state is ClipState.error
    assert "timed out" in (clip.error_reason or "")
    assert led.moments["mom_1"].state is MomentState.decided          # retriable, not terminal
    assert seen.get("timeout") == 600.0                               # the bound is actually wired
