import subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, ClipState, Fmt
from fanops.clip import ffmpeg_clip_cmd, reframe_filter, render_moment, render_aspects_for, fit_window, snap_window
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

# --- clip-length enforcement: a real clip is 12-22s, not a 3-4s fragment ---------------------
# The model picks a moment; render widens it to a watchable 12-22s window (and the subtitle overlay
# follows the same window). fit_window is the pure lever; render_moment is where it's applied.

def test_fit_window_expands_short_pick_to_min():
    s, e = fit_window(10.0, 13.0, 120.0)        # a 3s pick on a long source
    assert 12.0 <= (e - s) <= 22.0
    assert s == 10.0 and e == 22.0              # keeps the chosen entry, grows the tail to 12s

def test_fit_window_keeps_in_band_pick_unchanged():
    assert fit_window(10.0, 27.0, 120.0) == (10.0, 27.0)   # 17s already in band -> untouched

def test_fit_window_keeps_13s_pick_in_band():
    assert fit_window(10.0, 23.0, 120.0) == (10.0, 23.0)   # 13s now in band -> untouched (was trimmed at 20)

def test_fit_window_trims_overlong_pick_to_max():
    assert fit_window(10.0, 40.0, 120.0) == (10.0, 32.0)   # 30s -> trimmed to 22s from the entry

def test_fit_window_borrows_lead_in_at_eof():
    # a short pick at the very end can't grow forward past EOF, so pull the start back instead
    assert fit_window(58.0, 59.0, 60.0) == (48.0, 60.0)    # 12s window butted against the end

def test_fit_window_uses_whole_source_when_shorter_than_min():
    assert fit_window(2.0, 4.0, 8.0) == (0.0, 8.0)         # source < 12s -> use all of it

def test_fit_window_11s_source_is_whole():
    assert fit_window(0.0, 11.0, 11.0) == (0.0, 11.0)      # an 11s source (real data) -> whole clip

def test_fit_window_unprobed_duration_grows_without_clamp():
    assert fit_window(10.0, 12.0, 0.0) == (10.0, 22.0)     # duration 0 (unprobed) -> no EOF clamp

def _capture_render(tmp_path, mocker, start, end, *, duration):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=duration))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=start, end=end, reason="r", state=MomentState.decided))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    cmd = captured["cmd"]
    return float(cmd[cmd.index("-ss") + 1]), float(cmd[cmd.index("-to") + 1])  # (seek, output-relative duration)

def test_render_moment_widens_short_pick_to_real_clip(tmp_path, mocker):
    ss, dur = _capture_render(tmp_path, mocker, 10.0, 14.0, duration=120.0)  # 4s pick
    assert 12.0 <= dur <= 22.0                                               # widened to a real clip

def test_render_moment_keeps_in_band_window(tmp_path, mocker):
    ss, dur = _capture_render(tmp_path, mocker, 10.0, 28.0, duration=120.0)  # 18s pick already
    assert ss == 10.0 and dur == 18.0                                        # left exactly as picked

# --- boundary snapping: a clip should never begin mid-word or end mid-phrase -------------------
# snap_window nudges each edge (<= max_shift) onto a nearby transcript-line boundary; render_moment
# applies it AFTER fit_window so the band is enforced first, then the edges land on clean cuts.

def test_snap_window_pulls_start_to_line_start():
    tr = [{"start": 9.4, "end": 12.0, "text": "a"}, {"start": 12.0, "end": 16.0, "text": "b"}]
    assert snap_window(10.0, 16.0, tr) == (9.4, 16.0)      # mid-line start 10.0 -> line start 9.4

def test_snap_window_extends_end_to_line_end():
    tr = [{"start": 0.0, "end": 4.0, "text": "a"}, {"start": 4.0, "end": 17.2, "text": "b"}]
    assert snap_window(0.0, 16.5, tr) == (0.0, 17.2)       # mid-phrase end 16.5 -> phrase end 17.2

def test_snap_window_leaves_edges_with_no_near_boundary():
    tr = [{"start": 0.0, "end": 5.0, "text": "a"}]
    assert snap_window(20.0, 35.0, tr) == (20.0, 35.0)     # nearest boundary > max_shift -> unchanged

def test_snap_window_no_transcript_is_identity():
    assert snap_window(10.0, 22.0, None) == (10.0, 22.0)
    assert snap_window(10.0, 22.0, []) == (10.0, 22.0)

def test_snap_window_ignores_malformed_lines():
    tr = [{"text": "no times"}, {"start": 9.5, "end": 20.0, "text": "ok"}]
    assert snap_window(10.0, 20.4, tr) == (9.5, 20.0)      # lines missing start/end are skipped

def test_snap_window_never_inverts():
    # snapping the start forward and the end backward could cross them — must keep the original window
    tr = [{"start": 13.0, "end": 99.0, "text": "late"}, {"start": 0.0, "end": 12.5, "text": "early"}]
    assert snap_window(12.9, 13.1, tr) == (12.9, 13.1)

def test_snap_window_clamps_end_to_duration():
    # a whisper line end can overshoot the real file end; the snapped end must not exceed duration
    # (restores fit_window's EOF clamp, which snap runs after and would otherwise undo).
    tr = [{"start": 0.0, "end": 23.4, "text": "x"}]
    assert snap_window(0.0, 22.0, tr, duration=22.0) == (0.0, 22.0)   # 23.4 within max_shift but EOF-clamped

def test_snap_window_clamps_negative_start_to_zero():
    # a whisper first-segment start can be slightly negative; the snapped start must stay >= 0
    tr = [{"start": -0.8, "end": 20.0, "text": "x"}]
    assert snap_window(0.3, 20.0, tr, duration=60.0) == (0.0, 20.0)

def _capture_render_full(tmp_path, mocker, monkeypatch, *, start, end, duration, transcript=None, profile=None):
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")            # isolate: no subtitle pass in these cuts
    if profile: monkeypatch.setenv("FANOPS_CLIP_PROFILE", profile)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=duration, transcript=transcript))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=start, end=end, reason="r", state=MomentState.decided))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    cmd = captured["cmd"]
    return float(cmd[cmd.index("-ss") + 1]), float(cmd[cmd.index("-to") + 1])

def test_render_moment_snaps_cut_to_transcript_boundaries(tmp_path, mocker, monkeypatch):
    tr = [{"start": 9.3, "end": 12.0, "text": "a"}, {"start": 25.0, "end": 28.4, "text": "b"}]
    ss, to = _capture_render_full(tmp_path, mocker, monkeypatch, start=10.0, end=28.0,
                                  duration=120.0, transcript=tr)   # 18s in-band pick
    assert ss == 9.3                                       # start snapped to the line boundary
    assert round(ss + to, 1) == 28.4                       # end snapped to the phrase end

def test_render_moment_song_profile_uses_wider_band(tmp_path, mocker, monkeypatch):
    # a 14s pick on a song source grows to the 18s SONG floor (talk would keep it at 14)
    ss, to = _capture_render_full(tmp_path, mocker, monkeypatch, start=10.0, end=24.0,
                                  duration=120.0, profile="song")
    assert to == 18.0 and 18.0 <= to <= 35.0
