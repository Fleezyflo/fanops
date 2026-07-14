import json
import subprocess
import pytest
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, ClipState, Fmt, Batch
from fanops.clip import ffmpeg_clip_cmd, reframe_filter, render_moment, render_aspects_for, fit_window, snap_window
from fanops import overlay
from tests.fixtures.speech_segments import talk_seg, MUSIC_HALLUC


@pytest.fixture(autouse=True)
def _cv_off(monkeypatch):
    # M3d: creative_variation now DEFAULTS ON, but render_moment burns the MOMENT hook into the SHARED clip
    # only on the OFF path (ON -> hook=None, per-surface hooks own the burn at crosspost). This file tests
    # that shared-clip render path, so pin OFF; an ON-path test would set FANOPS_CREATIVE_VARIATION=1 itself.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")


def _vf_of(cmd: list[str]) -> str:
    """Return the value passed to the (last) -vf flag in an ffmpeg cmd list."""
    return cmd[cmd.index("-vf") + 1]

def test_ffmpeg_clip_cmd_submicrosecond_window_avoids_scientific_notation():
    # L02: ffmpeg -ss/-to args must stay decimal (never scientific notation) for sub-microsecond windows.
    cmd = ffmpeg_clip_cmd("/s/x.mp4", "/o/c.mp4", 0.0000001, 0.0000002, "9:16", src_w=1920, src_h=1080)
    joined = " ".join(cmd)
    assert "e-" not in joined.lower() and "e+" not in joined.lower()
    assert "0.000" in joined

def test_clip_cmd_seek_is_output_relative_and_reframes():
    cmd = ffmpeg_clip_cmd("/s/x.mp4", "/o/c.mp4", 1.5, 8.0, "9:16", src_w=1920, src_h=1080)
    # -ss BEFORE -i (fast seek), -to AFTER -i (output-relative, version-stable)
    assert cmd.index("-ss") < cmd.index("-i") < cmd.index("-to")
    assert "1.500" in cmd and "6.500" in cmd          # -to is output-relative DURATION (end-start), not absolute end
    assert "8.0" not in cmd                        # FIX F39: absolute end must NOT be emitted (caused version-fragile cuts)
    assert any("crop" in p or "scale" in p for p in cmd)
    assert cmd[-1] == "/o/c.mp4"

def test_reframe_filter_handles_vertical_source():
    # wide source -> crop to 9:16; already-vertical -> scale/pad, never negative crop
    wide = reframe_filter("9:16", 1920, 1080)
    tall = reframe_filter("9:16", 1080, 1920)
    assert "crop" in wide or "scale" in wide
    assert "crop=ih*9/16" not in tall or "1080:1920" in tall  # no impossible crop on tall src

# ---- Theme 2: upper-third crop bias (aware reframe), default-OFF, byte-identical when off ----

def test_reframe_filter_top_bias_off_is_byte_identical():
    # Default (top_bias=False) must equal today's reframe across every aspect + source shape.
    for aspect in ("9:16", "1:1", "16:9"):
        for w, h in ((1080, 1920), (1920, 1080), (1080, 1080), (0, 0)):
            assert reframe_filter(aspect, w, h, top_bias=True) is not None  # callable with the new kw
            assert reframe_filter(aspect, w, h, top_bias=False) == reframe_filter(aspect, w, h)

def test_reframe_filter_top_bias_lifts_a_vertical_height_crop():
    # A vertical source -> 1:1 target crops HEIGHT; the centered crop cuts the top (heads). top_bias
    # offsets the crop window UP (keeps headroom) — an explicit y offset, not the ffmpeg-default centre.
    centered = reframe_filter("1:1", 1080, 1920)
    biased = reframe_filter("1:1", 1080, 1920, top_bias=True)
    assert biased != centered
    assert ":0:" in biased and "/4" in biased            # upper-biased crop x=0, y=(leftover)/4
    assert biased.startswith("crop=iw:iw*1080/1080")

def test_reframe_filter_top_bias_noop_on_scale_only_and_width_crop():
    # 9:16->9:16 is scale-only (no crop); a wide source -> tall target crops WIDTH (full height kept).
    # Neither decapitates vertically, so top_bias leaves both byte-identical.
    assert reframe_filter("9:16", 1080, 1920, top_bias=True) == reframe_filter("9:16", 1080, 1920)
    assert reframe_filter("9:16", 1920, 1080, top_bias=True) == reframe_filter("9:16", 1920, 1080)

def test_ffmpeg_clip_cmd_threads_top_bias():
    cmd = ffmpeg_clip_cmd("/s/x.mp4", "/o/c.mp4", 1.5, 8.0, "1:1", src_w=1080, src_h=1920, top_bias=True)
    assert reframe_filter("1:1", 1080, 1920, top_bias=True) in _vf_of(cmd)
    # default (no top_bias) stays exactly today's centered reframe
    plain = ffmpeg_clip_cmd("/s/x.mp4", "/o/c.mp4", 1.5, 8.0, "1:1", src_w=1080, src_h=1920)
    assert _vf_of(plain) == reframe_filter("1:1", 1080, 1920)

def test_render_fingerprint_includes_top_bias():
    # Toggling aware-reframe on an already-rendered clip MUST bust the Phase D warm-skip (a different
    # crop is different bytes) — so the fingerprint depends on the bias.
    from fanops.clip import _render_fingerprint
    base = _render_fingerprint("/s.mp4", 0.0, 10.0, "1:1", 1080, 1920, "")
    biased = _render_fingerprint("/s.mp4", 0.0, 10.0, "1:1", 1080, 1920, "", top_bias=True)
    assert base != biased
    assert _render_fingerprint("/s.mp4", 0.0, 10.0, "1:1", 1080, 1920, "", top_bias=False) == base  # off == today

def test_render_moment_applies_top_bias_when_enabled(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_AWARE_REFRAME", "1")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")          # isolate from the frame probe
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1080, height=1920, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10, end=28, reason="r", state=MomentState.decided))
    captured = {}
    def run(cmd, **kw):
        if not str(cmd[-1]).startswith("-"):
            captured["cmd"] = cmd; out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=run)
    render_moment(led, cfg, "mom_1", aspect=Fmt.r1x1)
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert ":0:" in vf and "/4" in vf                      # the biased crop reached ffmpeg

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

def test_render_moment_records_error_on_empty_output(tmp_path, mocker):
    # ffmpeg RAN (rc=0) but wrote a 0-BYTE mp4 (truncated/failed mux). The SAME file already requires
    # st_size > 0 at the segment-concat (:447) and warm-skip (:619) paths; the single-pass success check
    # must too. An empty clip marked `rendered` advances the moment to `clipped` and blows up later in
    # crosspost media-upload — fail-safe instead: ClipState.error, moment stays `decided` (retriable).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided))
    def empty_run(cmd, **kw):
        if not str(cmd[-1]).startswith("-"):                 # render output path -> write a 0-byte file
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=empty_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.error
    assert clip.id in led.clips
    assert led.moments["mom_1"].state is MomentState.decided   # empty output is NOT a successful clip

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
                          transcript=[talk_seg("hello world", start=0.0, end=3.0),
                                      talk_seg("second line", start=3.0, end=6.0)]))
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


def test_render_null_transcript_start_skips_sub_captions(tmp_path, mocker, monkeypatch):
    # L03: a transcript segment with null start/end must fail-open — clip renders, caption events skipped.
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080,
                          transcript=[{"start": None, "end": 3.0, "text": "bad segment"},
                                      talk_seg("good line", start=3.0, end=6.0)]))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided, hook="hook"))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    ass = next(cfg.clips.glob("*.ass")).read_text(encoding="utf-8")
    assert "good line" in ass and "bad segment" not in ass


def test_render_subs_exclude_junk_segments(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, language="en",
                          transcript=[talk_seg("trusted line here", start=0.0, end=3.0),
                                      {**MUSIC_HALLUC, "start": 3.0, "end": 6.0, "text": "background noise"}]))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided, hook=""))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    ass = next(cfg.clips.glob("*.ass")).read_text(encoding="utf-8")
    assert "trusted line here" in ass and "background noise" not in ass


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


def _render_with_batch_subs(tmp_path, mocker, monkeypatch, *, global_on, batch_burn):
    """Render one transcript-carrying, HOOKLESS moment whose source belongs to a Batch with
    burn_subs=batch_burn, while the GLOBAL cfg.burn_subs is global_on. Returns the -vf string +
    the cfg so callers assert whether the TRANSCRIPT was burned. Hookless so the only on-screen
    text in play is the transcript — isolating the per-batch override resolution."""
    if global_on: monkeypatch.delenv("FANOPS_BURN_SUBS", raising=False)   # conftest forces 0; delenv -> default ON
    else: monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_batch(Batch(id="b_1", name="b", burn_subs=batch_burn))
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, batch_id="b_1",
                          transcript=[talk_seg("hello world", start=0.0, end=3.0)]))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided, hook=""))   # hookless
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    return _vf_of(captured["cmd"]), cfg

def test_batch_burn_subs_true_overrides_global_off(tmp_path, mocker, monkeypatch):
    # GLOBAL burn_subs OFF, but the source's Batch sets burn_subs=True -> transcript IS burned for
    # this talk batch (the override turns subs ON over a global default of OFF).
    vf, cfg = _render_with_batch_subs(tmp_path, mocker, monkeypatch, global_on=False, batch_burn=True)
    assert "subtitles=" in vf                                # batch override forced transcript on
    assert list(cfg.clips.glob("*.ass")), "expected an .ass written from the transcript"

def test_batch_burn_subs_false_overrides_global_on(tmp_path, mocker, monkeypatch):
    # GLOBAL burn_subs ON, but the source's Batch sets burn_subs=False -> transcript is SUPPRESSED for
    # this music batch (lyric subs hurt). Hookless source -> the clip carries no burned text at all.
    vf, cfg = _render_with_batch_subs(tmp_path, mocker, monkeypatch, global_on=True, batch_burn=False)
    assert "subtitles=" not in vf                            # batch override suppressed transcript
    assert vf == reframe_filter("9:16", 1920, 1080)         # plain reframe only
    assert not list(cfg.clips.glob("*.ass"))                # no .ass written

def test_batch_burn_subs_none_falls_back_to_global(tmp_path, mocker, monkeypatch):
    # A Batch with burn_subs=None defers to the global: global ON -> transcript burns; the None case
    # must NOT suppress (proves the override is None-aware, not just truthy-aware).
    vf, _ = _render_with_batch_subs(tmp_path, mocker, monkeypatch, global_on=True, batch_burn=None)
    assert "subtitles=" in vf                                # None -> global ON -> burned


def _fake_run_writing_clip(captured):
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        if not str(cmd[-1]).startswith("-"):     # FLAG last-arg (capability probe) is not an output path
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    return fake_run

def test_render_burns_hook_even_without_transcript(tmp_path, mocker, monkeypatch):
    # The RETENTION HOOK is the default on-screen text and does NOT need a transcript — a moment with
    # a hook burns it (subtitles= chained + .ass written) even when the source has no transcript and
    # burn_subs is OFF. (This is the whole point: the screen shows a hook, not the audio's words.)
    # burn_subs OFF (conftest default for hermeticity) -> no transcript captions
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, transcript=None))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided, hook="wait for the drop"))
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    assert "subtitles=" in _vf_of(captured["cmd"])              # the hook IS burned
    ass = list(cfg.clips.glob("*.ass"))
    assert ass and "wait for the drop" in ass[0].read_text(encoding="utf-8")   # ...carrying the hook text

def test_hook_burn_failed_true_when_textfilter_absent_with_hook(tmp_path, mocker, monkeypatch):
    # V2 M1/F9: a hook was WANTED but ffmpeg can't burn it -> the clip still renders (fail-open) but
    # records hook_burn_failed=True so the silent drop is VISIBLE (vs a clip that looks fine but lost
    # its hook). The flag is set on the persisted Clip (render_moment's own Clip object).
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"), width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.decided, hook="wait for the drop"))
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered and clip.hook_burn_failed is True

def test_hook_burn_failed_false_for_clean_clip(tmp_path, mocker, monkeypatch):
    # No hook + subs off -> nothing to burn -> NOT a failure (a clean clip is intentional, not a drop).
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"), width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.decided, hook=None))
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered and clip.hook_burn_failed is False

def test_hook_burn_failed_true_when_ass_empty_despite_hook(tmp_path, mocker, monkeypatch):
    # The SECOND silent-drop branch (audit M1f): textfilter exists + a hook is present, but build_ass
    # yields empty -> the hook is dropped with no signal. F9 flags this case too, not just toolchain-absent.
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    monkeypatch.setattr(overlay, "build_ass", lambda *a, **k: "")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"), width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.decided, hook="wait for the drop"))
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered and clip.hook_burn_failed is True

def test_render_clean_when_no_hook_and_subs_off(tmp_path, mocker, monkeypatch):
    # No hook AND transcript captions not opted in -> a CLEAN clip: no "subtitles=" in -vf, no .ass.
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")        # explicit OFF
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080,
                          transcript=[{"start": 0.0, "end": 3.0, "text": "hello world"}]))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided, hook=None))
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    assert "subtitles=" not in _vf_of(captured["cmd"])          # nothing to burn -> clean clip
    assert not list(cfg.clips.glob("*.ass"))


def test_render_skips_ffmpeg_when_warm_artifact_matches(tmp_path, mocker, monkeypatch):
    # Phase D: a lock-free pre-warm pass already rendered cid.mp4 + wrote its fingerprint. render_moment
    # must adopt the existing file and SKIP ffmpeg when the intended-render fingerprint matches — this is
    # what keeps the multi-minute transcode out of the ledger lock. It still records the clip + advances
    # the moment, so the in-lock commit pass is fast.
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=10, end=28, reason="r", state=MomentState.decided, hook=None))
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)   # warm: produces mp4 + fingerprint
    assert clip.state is ClipState.rendered
    led.set_moment_state("mom_1", MomentState.decided)              # reset so a 2nd render is attempted
    spy = mocker.patch("fanops.clip.subprocess.run")
    led, clip2 = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    spy.assert_not_called()                                          # warm artifact reused — no ffmpeg
    assert clip2.state is ClipState.rendered and led.moments["mom_1"].state is MomentState.clipped

def test_render_reruns_when_hook_changes_fingerprint(tmp_path, mocker, monkeypatch):
    # The render fingerprint must capture the burned hook: if the hook changes, the warm artifact is
    # STALE and render_moment must RE-RENDER (never silently reuse the old clip — the stale-render class
    # of bug). A blind skip-if-exists would wrongly keep the old hook.
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=10, end=28, reason="r", state=MomentState.decided, hook="first hook"))
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    led.moments["mom_1"].hook = "different hook"                    # hook changed -> warm artifact stale
    led.set_moment_state("mom_1", MomentState.decided)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert "cmd" in captured, "ffmpeg must re-run when the hook changes (stale clip not reused)"
    assert clip.state is ClipState.rendered

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
    tr = [talk_seg("a", start=9.3, end=12.0), talk_seg("b", start=25.0, end=28.4)]
    ss, to = _capture_render_full(tmp_path, mocker, monkeypatch, start=10.0, end=28.0,
                                  duration=120.0, transcript=tr)   # 18s in-band pick
    assert ss == 9.3                                       # start snapped to the line boundary
    assert round(ss + to, 1) == 28.4                       # end snapped to the phrase end

def test_render_moment_snap_ignores_junk_boundaries(tmp_path, mocker, monkeypatch):
    junk = {**MUSIC_HALLUC, "start": 9.4, "end": 9.8, "text": "junk start"}
    good = talk_seg("real speech", start=9.3, end=12.0)
    good_end = talk_seg("phrase end", start=15.0, end=17.2)
    junk_end = {**MUSIC_HALLUC, "start": 16.0, "end": 16.6, "text": "junk end"}
    tr = [junk, good, good_end, junk_end]
    ss, to = _capture_render_full(tmp_path, mocker, monkeypatch, start=10.0, end=16.5,
                                  duration=120.0, transcript=tr)
    assert ss == 9.3                                       # trusted start, not junk 9.4
    assert round(ss + to, 1) == 22.0                       # fit widens to 22s; trusted end 17.2 beyond max_shift

def test_render_moment_song_profile_uses_wider_band(tmp_path, mocker, monkeypatch):
    # a 14s pick on a song source grows to the 18s SONG floor (talk would keep it at 14)
    ss, to = _capture_render_full(tmp_path, mocker, monkeypatch, start=10.0, end=24.0,
                                  duration=120.0, profile="song")
    assert to == 18.0 and 18.0 <= to <= 35.0

# --- P1 T1: strongest-frame cut start (visual_start) --------------------------------------------
# render_moment refines the cut start (after the band, before transcript-snap) onto the strongest
# opening frame within a bounded shift, stamps first_frame_kind/cut_seconds, and leaves audio alone.

from fanops.clip import _vstart_candidate_times

def _run_render_with_probe(captured, *, strong_at=None):
    """subprocess.run side_effect: signalstats probes (cmd ends with '-') return strong stats at
    `strong_at` (near-black elsewhere); the render call (cmd ends with the mp4 path) writes a file."""
    def run(cmd, **kw):
        last = str(cmd[-1])
        if last == "-":                                   # signalstats probe
            t = float(cmd[cmd.index("-ss") + 1])
            strong = strong_at is not None and abs(t - strong_at) < 1e-3
            class R:
                returncode = 0; stderr = ""
                stdout = ("lavfi.signalstats.YMIN=16\nlavfi.signalstats.YAVG=120\nlavfi.signalstats.YMAX=210\n"
                          if strong else "lavfi.signalstats.YMIN=0\nlavfi.signalstats.YAVG=3\nlavfi.signalstats.YMAX=5\n")
            return R()
        if not last.startswith("-"):                      # render output path
            captured["cmd"] = cmd
            out = Path(last); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R2: returncode = 0; stderr = ""; stdout = ""
        return R2()
    return run

# ---- Theme 3: sharpness probe + versioned vstart sidecar (C2/H2 stale-cache trap) ----

def _vstart_key(src_path, start, end):
    import hashlib
    return hashlib.sha256(f"{src_path}|{round(start, 3)}|{round(end, 3)}".encode()).hexdigest()[:16]

def test_probe_frame_strength_returns_sharpness(mocker):
    # The probe now also derives a sharpness proxy from a SECOND Laplacian-convolution pass (its YAVG).
    from fanops.clip import _probe_frame_strength
    def run(cmd, **kw):
        j = " ".join(str(x) for x in cmd)
        class R:
            returncode = 0; stderr = ""
            stdout = ("lavfi.signalstats.YAVG=40\n" if "convolution" in j else
                      "lavfi.signalstats.YMIN=16\nlavfi.signalstats.YAVG=120\nlavfi.signalstats.YMAX=210\n")
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=run)
    assert _probe_frame_strength("/x.mp4", 5.0) == (120.0, 210.0 - 16.0, 40.0)   # luma, contrast, sharpness

def test_probe_frame_strength_sharpness_is_fail_open(mocker):
    # If the Laplacian pass dies, sharpness degrades to None (contrast-only downstream) — never raises.
    from fanops.clip import _probe_frame_strength
    def run(cmd, **kw):
        j = " ".join(str(x) for x in cmd)
        if "convolution" in j:
            raise OSError("laplacian pass failed")
        class R:
            returncode = 0; stderr = ""
            stdout = "lavfi.signalstats.YMIN=16\nlavfi.signalstats.YAVG=120\nlavfi.signalstats.YMAX=210\n"
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=run)
    assert _probe_frame_strength("/x.mp4", 5.0) == (120.0, 194.0, None)

def test_pick_visual_start_rejects_stale_unversioned_sidecar(tmp_path, mocker):
    # C2/H2: a pre-Theme-3 vstart sidecar (no "v") must NOT be adopted — else the source serves the
    # old, sharpness-blind decision forever. Stale -> cache miss -> re-probe + rewrite versioned.
    import json
    from fanops.clip import pick_visual_start
    out = tmp_path / "clips"; out.mkdir()
    src = f"{tmp_path}/s.mp4"; start, end = 10.0, 12.0
    key = _vstart_key(src, start, end)
    (out / f"vstart_{key}.json").write_text(json.dumps({"start": 99.0, "kind": "visual"}))   # stale, no "v"
    def run(cmd, **kw):                                   # weak stats everywhere -> no move from start
        class R:
            returncode = 0; stderr = ""
            stdout = "lavfi.signalstats.YMIN=0\nlavfi.signalstats.YAVG=3\nlavfi.signalstats.YMAX=5\n"
        return R()
    spy = mocker.patch("fanops.clip.subprocess.run", side_effect=run)
    new_start, kind = pick_visual_start(src, start, end, scene_peaks=[], out_dir=out)
    spy.assert_called()                                  # stale sidecar rejected -> probed
    assert new_start != 99.0                             # the bogus cached start was ignored
    assert json.loads((out / f"vstart_{key}.json").read_text())["v"] == 2   # rewritten versioned

def test_pick_visual_start_adopts_versioned_sidecar(tmp_path, mocker):
    import json
    from fanops.clip import pick_visual_start
    out = tmp_path / "clips"; out.mkdir()
    src = f"{tmp_path}/s.mp4"; start, end = 10.0, 12.0
    key = _vstart_key(src, start, end)
    (out / f"vstart_{key}.json").write_text(json.dumps({"v": 2, "start": 11.0, "kind": "visual"}))
    spy = mocker.patch("fanops.clip.subprocess.run")
    new_start, kind = pick_visual_start(src, start, end, scene_peaks=[], out_dir=out)
    spy.assert_not_called()                              # v2 adopted -> no ffmpeg
    assert new_start == 11.0 and kind == "visual"

def test_render_moment_visual_start_moves_cut_and_stamps_provenance(tmp_path, mocker, monkeypatch):
    monkeypatch.delenv("FANOPS_VISUAL_START", raising=False)      # default ON
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10, end=28, reason="r", state=MomentState.decided))
    target = _vstart_candidate_times(10.0, 28.0)[2]
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_run_render_with_probe(captured, strong_at=target))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    assert clip.first_frame_kind == "visual"
    assert clip.cut_seconds is not None and clip.cut_seconds > 0
    cmd = captured["cmd"]
    assert abs(float(cmd[cmd.index("-ss") + 1]) - target) < 1e-3   # cut start moved onto the strong frame
    assert "-c:a" in cmd                                            # audio still encoded -> untouched

def test_visual_start_provenance_honest_with_transcript(tmp_path, mocker, monkeypatch):
    # snap runs BEFORE visual, so first_frame_kind="visual" iff the visual pick is the ACTUAL rendered
    # start — snap can't silently pull it back while the dim still claims "visual" (the P4-poisoning bug).
    monkeypatch.delenv("FANOPS_VISUAL_START", raising=False)
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    tr = [talk_seg("a", start=9.3, end=12.0), talk_seg("b", start=25.0, end=28.4)]
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0, transcript=tr))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10, end=28, reason="r", state=MomentState.decided))
    target = _vstart_candidate_times(9.3, 28.4)[2]               # candidates start from the SNAPPED window
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_run_render_with_probe(captured, strong_at=target))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.first_frame_kind == "visual"
    ss = float(captured["cmd"][captured["cmd"].index("-ss") + 1])
    assert abs(ss - target) < 1e-3                               # rendered start IS the visual pick

def test_render_moment_visual_start_off_does_not_probe(tmp_path, mocker, monkeypatch):
    # FANOPS_VISUAL_START=0 -> no signalstats probe at all, start unchanged, first_frame_kind None.
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")   # isolate visual_start: smart framing is a SEPARATE keyframe prober (default ON, fires when the [framing] extra is present)
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10, end=28, reason="r", state=MomentState.decided))
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert not any("signalstats" in str(c) for c in calls)         # feature off -> no probe
    assert clip.first_frame_kind is None                           # dim not engaged
    rend = [c for c in calls if not str(c[-1]).startswith("-")][0]
    assert float(rend[rend.index("-ss") + 1]) == 10.0              # band/snap start, unchanged

def test_render_logs_legibility_warning_for_overlong_hook(tmp_path, mocker, monkeypatch):
    # P1 T2: an overlong hook logs ONE legibility warning and the clip STILL renders (fail-open).
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")               # isolate from the probe path
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t", start=10, end=28,
                          reason="r", state=MomentState.decided,
                          hook="wait for the absolutely incredible unbelievable final climactic drop here"))
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered                      # never blocked
    assert "hook_legibility" in cfg.log_path.read_text()         # warned once

def test_render_silent_for_legible_hook(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t", start=10, end=28,
                          reason="r", state=MomentState.decided, hook="wait for the drop"))
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "hook_legibility" not in log                          # a clear hook is silent

def test_render_reruns_when_visual_start_changes_fingerprint(tmp_path, mocker, monkeypatch):
    # P1 T4: the chosen visual start flows into cs -> _render_fingerprint, so a DIFFERENT pick must
    # bust the Phase D warm-skip and RE-RENDER (never silently reuse the clip cut at the old start).
    monkeypatch.delenv("FANOPS_VISUAL_START", raising=False)     # ON
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10, end=28, reason="r", state=MomentState.decided))
    a, b = _vstart_candidate_times(10.0, 28.0)[1], _vstart_candidate_times(10.0, 28.0)[3]
    cap1 = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_run_render_with_probe(cap1, strong_at=a))
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    ss1 = float(cap1["cmd"][cap1["cmd"].index("-ss") + 1]); assert abs(ss1 - a) < 1e-3
    for f in cfg.clips.glob("vstart_*.json"): f.unlink()          # clear the cached decision -> re-pick
    led.set_moment_state("mom_1", MomentState.decided)
    cap2 = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_run_render_with_probe(cap2, strong_at=b))
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert "cmd" in cap2, "a changed visual start must re-render (fingerprint busts the warm skip)"
    ss2 = float(cap2["cmd"][cap2["cmd"].index("-ss") + 1])
    assert abs(ss2 - b) < 1e-3 and ss1 != ss2


# ---- MOL-178 (S3): supercut render branch — absolute-seek concat, postable, fail-open ----

def _supercut_moment_led(tmp_path, *, segments, duration=120.0, transcript=None, hook=None):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=duration, transcript=transcript))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="sc",
                          start=segments[0][0], end=segments[-1][1], reason="supercut",
                          state=MomentState.decided, segments=list(segments), hook=hook))
    return cfg, led

def test_supercut_concats_absolute_spans_in_order(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    spans = [(10.0, 15.0), (30.0, 33.0), (50.0, 52.0)]   # 5 + 3 + 2 = 10s assembled
    cfg, led = _supercut_moment_led(tmp_path, segments=spans)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    cmd = captured["cmd"]
    assert "-filter_complex" in cmd
    sss = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-ss"]
    assert sss == ["10.000", "30.000", "50.000"]          # ABSOLUTE seeks, source order
    ts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-t"]
    assert ts == ["5.000", "3.000", "2.000"]
    assert cmd.count("-i") == 3
    assert "concat=n=3:v=1:a=1" in cmd[cmd.index("-filter_complex") + 1]

def test_supercut_is_postable_and_advances_moment(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg, led = _supercut_moment_led(tmp_path, segments=[(10.0, 15.0), (30.0, 35.0)])
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered                    # postable — NOT stitch_draft
    assert led.moments["mom_1"].state is MomentState.clipped   # advances moment (anti-stitch guard)

def test_supercut_cut_seconds_is_sum_of_spans(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    spans = [(10.0, 15.0), (30.0, 33.5), (50.0, 51.5)]
    cfg, led = _supercut_moment_led(tmp_path, segments=spans)
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    _, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.cut_seconds == round(5.0 + 3.5 + 1.5, 3)

def test_supercut_first_frame_kind_none_ok(tmp_path, mocker, monkeypatch):
    monkeypatch.delenv("FANOPS_VISUAL_START", raising=False)   # visual_start ON by default
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg, led = _supercut_moment_led(tmp_path, segments=[(10.0, 22.0), (40.0, 50.0)])
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    _, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.first_frame_kind is None                       # visual_start bypassed on supercut

def test_supercut_subtitles_rebased_to_assembled_timeline(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    spans = [(10.0, 15.0), (30.0, 35.0)]                      # span2 offset = 5s in assembled timeline
    tr = [talk_seg("span two line", start=31.0, end=34.0),
          talk_seg("gap line", start=20.0, end=25.0),          # in the GAP between spans -> dropped
          talk_seg("span one", start=11.0, end=13.0)]
    cfg, led = _supercut_moment_led(tmp_path, segments=spans, transcript=tr, hook="hook")
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    ass = list(cfg.clips.glob("*.ass"))
    assert ass, "expected supercut .ass"
    text = ass[0].read_text(encoding="utf-8")
    assert "gap line" not in text                              # gap transcript dropped
    assert "span two line" in text
    # span-2 line at source 31s -> assembled 31-30+5 = 6s (within the 5-10s assembled window)
    assert ",0:00:06." in text or ",0:00:05." in text        # rebased onto assembled timeline

def test_supercut_fail_open_to_envelope(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    spans = [(10.0, 14.0), (30.0, 34.0)]
    cfg, led = _supercut_moment_led(tmp_path, segments=spans, duration=120.0)
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        if "-filter_complex" in cmd:
            return type("R", (), {"returncode": 1, "stderr": "supercut failed", "stdout": ""})()
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CLIP")
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
    mocker.patch("fanops.clip.subprocess.run", side_effect=run)
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    assert any("-filter_complex" in c for c in calls)         # supercut tried first
    fallback = [c for c in calls if "-vf" in c][-1]           # envelope single-window fallback
    assert "-filter_complex" not in fallback
    ss = float(fallback[fallback.index("-ss") + 1])
    assert 10.0 <= ss <= 14.0                                 # envelope window, not absolute span seek

def test_supercut_subtitle_fail_open_to_hook_only(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    spans = [(10.0, 22.0), (30.0, 40.0)]
    cfg, led = _supercut_moment_led(tmp_path, segments=spans,
                                    transcript=[{"start": 11.0, "end": 13.0, "text": "hi"}], hook="keep hook")
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip({}))
    monkeypatch.setattr(overlay, "build_supercut_ass", lambda *a, **k: (_ for _ in ()).throw(ValueError("rebase fail")))
    led, clip = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    assert clip.state is ClipState.rendered
    ass = list(cfg.clips.glob("*.ass"))
    assert ass and "keep hook" in ass[0].read_text(encoding="utf-8")   # hook-only fallback
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "supercut" in log.lower() or "rebase" in log.lower()

def test_single_window_render_byte_identical(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="10-28",
                          start=10, end=28, reason="r", state=MomentState.decided, segments=[]))
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_fake_run_writing_clip(captured))
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    cmd = captured["cmd"]
    assert "-filter_complex" not in cmd
    assert float(cmd[cmd.index("-ss") + 1]) == 10.0           # fit_window in-band pick unchanged start


def test_native_default_render_has_no_template_overlay(tmp_path, mocker, monkeypatch):
    # P1 T5: the default render is the base REFRAMED clip — no burned template card, no compose layer
    # (the produced MoviePy layer stays opt-in via `fanops compose`). vf is exactly the reframe filter.
    monkeypatch.delenv("FANOPS_VISUAL_START", raising=False)     # ON (default)
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")        # transcript captions OFF (explicit)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=10, end=28, reason="r", state=MomentState.decided, hook=None))
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_run_render_with_probe(captured))
    render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    vf = _vf_of(captured["cmd"])
    assert vf == reframe_filter("9:16", 1920, 1080)              # exactly the reframe — no template/subtitle filter
    assert "subtitles=" not in vf


# ---------------------------------------------------------------------------------------------------
# Reframe dry-run seams: the fingerprint payload split, and the extracted _build_ass_text.
# These GUARD the refactor — they do not test the dry-run itself (that is test_reframe_dryrun.py).
# ---------------------------------------------------------------------------------------------------
from fanops.clip import (_build_ass_text, _render_fingerprint, _render_fingerprint_payload,   # noqa: E402
                         _REFRAME_GEOM_V, fingerprint_of_payload, fingerprint_payload_bytes)

_FP_KW = dict(src_path="/s.mp4", cs=1.0, ce=11.0, aspect_value="9:16", src_w=1920, src_h=1080, ass_text="X")
_FOCUS4 = (0.61, 0.44, 0.30, 0.38)          # subject lock: zooms  -> geom True
_SAL2 = (0.61, 0.44)                        # motion saliency: pans -> geom False
_TRACK = [(0.0, 5.0, 0.3, 0.5, 0.28, 0.4), (5.0, 10.0, 0.7, 0.5, 0.28, 0.4)]


@pytest.mark.parametrize("kw", [
    dict(),                                                                    # centered
    dict(focus=_FOCUS4, content_type="single-speaker-talk"),                   # subject lock
    dict(track=_TRACK, content_type="multi-speaker-talk"),                     # active-speaker track
    dict(focus=_SAL2, content_type=None),                                      # motion saliency
    dict(supercut_segments=[(1.0, 3.0), (7.0, 9.0)]),                          # supercut
    dict(top_bias=True),
])
def test_fingerprint_payload_split_is_hash_compatible(kw):
    """The payload/hash split changed NOTHING: _render_fingerprint is still sha256 over the exact same
    canonical bytes.

    This proves the fingerprint SERIALIZATION is unchanged. It does NOT prove rendered-byte identity —
    that is protected separately by the filter-argument tests, the Layer-1 routing characterization, the
    ASS goldens below, and the E2E suite."""
    payload = _render_fingerprint_payload(**_FP_KW, **kw)
    assert fingerprint_of_payload(payload) == _render_fingerprint(**_FP_KW, **kw)
    assert fingerprint_payload_bytes(payload) == json.dumps(payload, sort_keys=True,
                                                            ensure_ascii=False).encode("utf-8")


def test_fingerprint_CANNOT_see_a_content_type_on_a_saliency_focus():
    """C-2 / D9, stated as a NEGATIVE so nobody mistakes the fingerprint golden for coverage of it.

    `ct` only enters the payload when `geom` is true, and geom is FALSE for a 2-tuple focus. So if a
    refactor made the saliency branch 'helpfully' return a content_type, EVERY saliency clip's
    fingerprint would be UNCHANGED and this family of tests would stay green. The only guards that can
    see it are the Layer-1 tuple vector (test_framing_outcomes) and the resolver's own assertion."""
    base = _render_fingerprint(**_FP_KW, focus=_SAL2, track=None, content_type=None)
    for ct in ("music", "silent", "no-people"):
        assert _render_fingerprint(**_FP_KW, focus=_SAL2, track=None, content_type=ct) == base
    # ... whereas on a 4-tuple focus (geom True) the content_type DOES change the bytes:
    assert _render_fingerprint(**_FP_KW, focus=_FOCUS4, content_type="music") \
        != _render_fingerprint(**_FP_KW, focus=_FOCUS4, content_type=None)
    assert _render_fingerprint_payload(**_FP_KW, focus=_FOCUS4, content_type="music")["geom"] == _REFRAME_GEOM_V


def _ass_corpus(tmp_path, monkeypatch, *, hook=None, transcript=None, burn="1", batch_burn=None):
    monkeypatch.setenv("FANOPS_BURN_SUBS", burn)
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    bid = None
    if batch_burn is not None:
        b = Batch(id="bat_1", name="b", burn_subs=batch_burn); led.add_batch(b); bid = b.id
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "s.mp4"), width=1920, height=1080,
                          duration=120.0, transcript=transcript or [], language="en", batch_id=bid))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t", start=10.0, end=28.0,
                          reason="r", state=MomentState.decided, hook=hook))
    return led, cfg


def test_build_ass_text_is_pure_and_writes_nothing(tmp_path, monkeypatch):
    """The seam exists precisely so payload_new's `ass` can be derived WITHOUT the write that
    _subtitles_vf performs (overlay.write_ass). A second implementation would be free to drift."""
    led, cfg = _ass_corpus(tmp_path, monkeypatch, hook="wait for it")
    monkeypatch.setattr(overlay, "write_ass", lambda *a, **k: pytest.fail("_build_ass_text must not write"))
    text, hbf = _build_ass_text(led, cfg, "mom_1", "clip_1", Fmt.r9x16, clip_start=10.0, clip_end=28.0)
    assert text and "wait for it" in text and hbf is False
    assert not (cfg.clips / "clip_1.ass").exists()


@pytest.mark.parametrize("aspect", [Fmt.r9x16, Fmt.r1x1, Fmt.r16x9])
def test_build_ass_text_golden_per_aspect(tmp_path, monkeypatch, aspect):
    led, cfg = _ass_corpus(tmp_path, monkeypatch, hook="the drop hits here")
    text, _ = _build_ass_text(led, cfg, "mom_1", "c", aspect, clip_start=10.0, clip_end=28.0)
    tw, th = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}[aspect.value]
    assert f"PlayResX: {tw}" in text and f"PlayResY: {th}" in text


def test_build_ass_text_hook_only_transcript_only_and_both(tmp_path, monkeypatch):
    seg = [talk_seg("hello there", start=11.0, end=13.0)]   # trusted_segments needs the full quality metadata
    hook_only, _ = _build_ass_text(*_ass_corpus(tmp_path / "a", monkeypatch, hook="H", burn="0"),
                                   "mom_1", "c", Fmt.r9x16, clip_start=10.0, clip_end=28.0)
    tx_only, _ = _build_ass_text(*_ass_corpus(tmp_path / "b", monkeypatch, transcript=seg),
                                 "mom_1", "c", Fmt.r9x16, clip_start=10.0, clip_end=28.0)
    both, _ = _build_ass_text(*_ass_corpus(tmp_path / "c", monkeypatch, hook="H", transcript=seg),
                              "mom_1", "c", Fmt.r9x16, clip_start=10.0, clip_end=28.0)
    assert hook_only and "H" in hook_only
    assert tx_only and "hello there" in tx_only and "H" not in tx_only.split("Dialogue")[-1]
    assert both and "H" in both and "hello there" in both


def test_build_ass_text_none_when_nothing_wanted_and_is_NOT_a_failure(tmp_path, monkeypatch):
    """The (None, False) that leaves a stale {cid}.ass on disk — D6, the trap the dry-run must survive."""
    led, cfg = _ass_corpus(tmp_path, monkeypatch, hook=None, burn="0")
    text, hbf = _build_ass_text(led, cfg, "mom_1", "c", Fmt.r9x16, clip_start=10.0, clip_end=28.0)
    assert text is None and hbf is False


def test_build_ass_text_flags_a_wanted_but_unburnable_hook(tmp_path, monkeypatch):
    led, cfg = _ass_corpus(tmp_path, monkeypatch, hook="H")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: False)
    text, hbf = _build_ass_text(led, cfg, "mom_1", "c", Fmt.r9x16, clip_start=10.0, clip_end=28.0)
    assert text is None and hbf is True            # WANTED but unburnable -> the F9 flag, unchanged


def test_build_ass_text_batch_burn_subs_override_still_wins(tmp_path, monkeypatch):
    seg = [talk_seg("lyrics", start=11.0, end=13.0)]
    led, cfg = _ass_corpus(tmp_path, monkeypatch, transcript=seg, burn="1", batch_burn=False)
    text, hbf = _build_ass_text(led, cfg, "mom_1", "c", Fmt.r9x16, clip_start=10.0, clip_end=28.0)
    assert text is None and hbf is False           # the music batch opted OUT, and cfg.burn_subs=1 loses


def test_a_stale_ass_on_disk_does_not_affect_the_newly_derived_text(tmp_path, monkeypatch):
    led, cfg = _ass_corpus(tmp_path, monkeypatch, hook="new hook")
    cfg.clips.mkdir(parents=True, exist_ok=True)
    (cfg.clips / "c.ass").write_text("[Script Info]\nOLD STALE HOOK\n")
    text, _ = _build_ass_text(led, cfg, "mom_1", "c", Fmt.r9x16, clip_start=10.0, clip_end=28.0)
    assert "new hook" in text and "OLD STALE HOOK" not in text
