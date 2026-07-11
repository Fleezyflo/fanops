# tests/test_render_account_cut.py — M2b: an override account's Render is a REAL per-account CUT at its
# own length band (not the global-band shared clip with a hook stamped on it). render_account_cut cuts
# from the SOURCE at the account's band + burns its hook in ONE pass, atomically. crosspost mints a
# band-TAGGED render id when the account's band differs from the global, so @short (8-15s) and @long
# (28-45s) ship genuinely different lengths off the SAME moment — and never collide on one file. An
# account at the global band is byte-identical to today (shared-clip burn_hook_only, un-tagged id).
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt
from fanops.accounts import Accounts
from fanops.clip import render_account_cut
from fanops.crosspost import crosspost_clips, render_spec
from fanops import overlay


# ---------------------------------------------------------------- render_account_cut (unit) ----
def _src_moment(cfg, *, start=10, end=14, dur=120.0):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1080, height=1920, duration=dur))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t",
                          start=start, end=end, reason="r", state=MomentState.clipped))
    return led

def _capturing_run(captured):
    def run(cmd, **kw):
        if not str(cmd[-1]).startswith("-"):           # the output path (not a capability flag)
            captured["cmd"] = cmd
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"CUT")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    return run

def _to_of(cmd):
    return cmd[cmd.index("-to") + 1]

def test_cut_grows_short_window_to_long_band(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")     # isolate the band math from the frame probe
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    out = str(cfg.clips / "r_long.9x16.mp4")
    ok, secs = render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="long", hook="H", out_path=out)
    assert ok is True and Path(out).exists() and secs == 28.0   # P3: returns the realized window seconds
    assert _to_of(captured["cmd"]) == "28.000"          # LONG floor 28 grows the 4s window (output-relative -to)

def test_cut_grows_short_window_to_short_band(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    out = str(cfg.clips / "r_short.9x16.mp4")
    render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="short", hook="H", out_path=out)
    assert _to_of(captured["cmd"]) == "8.000"           # SHORT floor 8

def test_cut_burns_the_hook(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    out = str(cfg.clips / "r.9x16.mp4")
    render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="long", hook="watch this", out_path=out)
    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert "subtitles" in vf                            # the hook .ass was chained into the cut (one pass)

def test_cut_no_textfilter_still_cuts_without_hook(tmp_path, mocker, monkeypatch):
    # fail-open legibility: the toolchain can't burn text -> still cut the RIGHT LENGTH (clean clip), True
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: False)
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    out = str(cfg.clips / "r.9x16.mp4")
    ok, _ = render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="long", hook="H", out_path=out)
    assert ok is True and "subtitles" not in captured["cmd"][captured["cmd"].index("-vf") + 1]

def test_cut_fail_open_on_ffmpeg_absent(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    mocker.patch("fanops.clip.subprocess.run", side_effect=FileNotFoundError("ffmpeg gone"))
    out = str(cfg.clips / "r.9x16.mp4")
    ok, secs = render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="long", hook="H", out_path=out)
    assert ok is False and secs is None and not Path(out).exists()   # fail-open: never raises, never a partial file

def test_cut_fail_open_on_nonzero_rc(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    def bad_run(cmd, **kw):
        class R: returncode = 1; stderr = "boom"; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=bad_run)
    out = str(cfg.clips / "r.9x16.mp4")
    assert render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="long", hook="H", out_path=out) == (False, None)
    assert not Path(out).exists() and not Path(out + ".part").exists()
    assert not Path(out).with_suffix(".ass").exists()       # the .ass render artifact is swept on failure (no leak)

def test_cut_success_leaves_no_artifacts(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    out = str(cfg.clips / "r.9x16.mp4")
    assert render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="long", hook="H", out_path=out) == (True, 28.0)
    assert Path(out).exists() and not Path(out + ".part").exists() and not Path(out).with_suffix(".ass").exists()


# ---------------------------------------------------------------- crosspost wiring (P9: moment-level spec, no per-account cut at mint) ----
def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, aid, **extra):
    return {"handle": handle, "account_id": aid, "platforms": ["instagram"], "status": "active", **extra}

def _seed_clip(led, cfg, *, m_hook=None, m_profile=None, surfaces, batch_id=None):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920,
                          duration=120.0, batch_id=batch_id))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=m_hook, clip_profile=m_profile))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)

def _run_crosspost(cfg, mocker):
    cfg.clips.mkdir(parents=True, exist_ok=True)
    out = cfg.clips / "r.mp4"; out.write_bytes(b"R")
    rendered = Clip(id="clip_mom_1_9x16", parent_id="mom_1", path=str(out), aspect=Fmt.r9x16, state=ClipState.rendered)
    mocker.patch("fanops.crosspost.render_moment", return_value=(Ledger.load(cfg), rendered))
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    led.save()
    return Ledger.load(cfg)

def test_crosspost_stamps_moment_profile_on_post(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("long", "1", clip_profile="long")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="hook L", m_profile="long", surfaces=("long/instagram",)); led.save()
    led = _run_crosspost(cfg, mocker)
    p = next(iter(led.posts.values()))
    assert p.clip_profile == "long"

def test_crosspost_default_profile_from_moment(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("a", "1")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", surfaces=("a/instagram",)); led.save()
    led = _run_crosspost(cfg, mocker)
    assert next(iter(led.posts.values())).clip_profile == "talk"

def test_render_spec_band_tagged_when_profile_differs(tmp_path):
    cfg = Config(root=tmp_path)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    m_long = Moment(id="mom_1", parent_id="src_1", start=0, end=7, reason="r", clip_profile="long", hook="H")
    m_talk = Moment(id="mom_1", parent_id="src_1", start=0, end=7, reason="r", clip_profile="talk", hook="H")
    rid_long, wants_cut, profile, _ = render_spec(cfg, clip=clip, hook="H", moment=m_long)
    rid_talk, _, _, _ = render_spec(cfg, clip=clip, hook="H", moment=m_talk)
    assert wants_cut is True and profile == "long" and rid_long != rid_talk

def test_same_moment_same_profile_one_render(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("short", "1", clip_profile="short"), _acct("long", "2", clip_profile="long")])
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook="SAME", clip_profile="long"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_16x9.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r16x9, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": "c", "hashtags": []} for s in ("short/instagram", "long/instagram")}
    led.add_clip(clip); led.save()
    calls = []
    def _rm(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
        calls.append(1)
        rc = Clip(id="clip_mom_1_9x16", parent_id="mom_1", path=str(cfg.clips / "r.mp4"),
                  aspect=aspect, state=ClipState.rendered)
        led.clips[rc.id] = rc
        return led, rc
    mocker.patch("fanops.crosspost.render_moment", side_effect=_rm)
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    assert len(calls) == 1
    assert {p.clip_profile for p in led.posts.values()} == {"long"}

def test_render_moment_file_fail_open_burn(tmp_path, mocker):
    from fanops.crosspost import render_moment_file
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0, end=7, reason="r", clip_profile="long", hook="H"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    led.add_clip(clip)
    from fanops.models import Post, Platform, PostState
    post = Post(id="p1", parent_id="clip_1", account="long", account_id="1", platform=Platform.instagram,
                caption="c", state=PostState.awaiting_approval)
    mocker.patch("fanops.crosspost.render_account_cut", return_value=(False, None))
    def _burn(base, out, hook, **kw):
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"BURN"); return True
    mocker.patch("fanops.overlay.burn_hook_only", side_effect=_burn)
    plan = render_moment_file(led, cfg, post=post, target_clip=clip, src=led.sources["src_1"])
    assert plan.produced is False and Path(plan.vpath).exists()

def test_render_moment_file_cut_success(tmp_path, mocker):
    from fanops.crosspost import render_moment_file
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0, end=7, reason="r", clip_profile="long", hook="H"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    led.add_clip(clip)
    from fanops.models import Post, Platform, PostState
    post = Post(id="p1", parent_id="clip_1", account="long", account_id="1", platform=Platform.instagram,
                caption="c", state=PostState.awaiting_approval)
    def _cut(led, cfg, moment_id, *, aspect, profile, hook, out_path, top_bias=False):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True); Path(out_path).write_bytes(b"ACUT")
        return (True, 12.0)
    mocker.patch("fanops.crosspost.render_account_cut", side_effect=_cut)
    plan = render_moment_file(led, cfg, post=post, target_clip=clip, src=led.sources["src_1"])
    assert plan.produced is True and Path(plan.vpath).read_bytes() == b"ACUT"

def test_posts_share_moment_profile_not_account_override(tmp_path, mocker):
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("long", "1", clip_profile="long")])
    led = Ledger.load(cfg)
    led_obj = led
    led_obj.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led_obj.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                              state=MomentState.clipped, hook="H", clip_profile="talk"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": "c", "hashtags": []} for s in ("long/instagram", "long/tiktok")}
    led_obj.add_clip(clip); led_obj.save()
    led = _run_crosspost(cfg, mocker)
    assert {p.clip_profile for p in led.posts.values()} == {"talk"}
