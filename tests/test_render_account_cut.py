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
from fanops.crosspost import crosspost_clips
from fanops.ids import child_id
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
    assert _to_of(captured["cmd"]) == "28.0"            # LONG floor 28 grows the 4s window (output-relative -to)

def test_cut_grows_short_window_to_short_band(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")
    monkeypatch.setattr(overlay, "ffmpeg_has_textfilter", lambda: True)
    cfg = Config(root=tmp_path); led = _src_moment(cfg)
    captured = {}
    mocker.patch("fanops.clip.subprocess.run", side_effect=_capturing_run(captured))
    out = str(cfg.clips / "r_short.9x16.mp4")
    render_account_cut(led, cfg, "mom_1", aspect=Fmt.r9x16, profile="short", hook="H", out_path=out)
    assert _to_of(captured["cmd"]) == "8.0"             # SHORT floor 8

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


# ---------------------------------------------------------------- crosspost wiring (integration) ----
def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, aid, **extra):
    return {"handle": handle, "account_id": aid, "platforms": ["instagram"], "status": "active", **extra}

def _seed_clip(led, cfg, *, m_hook=None, surfaces, batch_id=None):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920,
                          duration=120.0, batch_id=batch_id))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook=m_hook))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)

def _patch_cut(mocker, *, returns=True):
    # isolate from ffmpeg: write the per-account file (so Render.path exists) and report success/failure.
    calls = []
    def cut(led, cfg, moment_id, *, aspect, profile, hook, out_path, top_bias=False):
        calls.append({"profile": profile, "hook": hook, "out_path": out_path, "top_bias": top_bias})
        if returns:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True); Path(out_path).write_bytes(b"ACUT")
        return (returns, 12.0 if returns else None)   # P3: (produced, realized_seconds)
    mocker.patch("fanops.crosspost.render_account_cut", side_effect=cut)
    return calls

def _patch_burn(mocker):
    calls = []
    def burn(base, out, hook, **kw):
        calls.append({"base": base, "out": out, "hook": hook})
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_bytes(b"BURN"); return True
    mocker.patch("fanops.overlay.burn_hook_only", side_effect=burn)
    return calls

def _run(cfg):
    # Slice 2 (burn on approval): the per-account Render materializes at APPROVAL, not at crosspost. Drive the
    # FULL mint->approve path (persist so the approve txn sees the posts) — the cut/framing/provenance SEMANTICS
    # asserted below are unchanged; only the render's timing moved.
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    led.save()
    from fanops.studio.actions_approve import approve_posts
    approve_posts(cfg, [p.id for p in led.posts.values()])
    return Ledger.load(cfg)


def test_override_account_triggers_per_account_cut(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")           # global stays default "talk"
    cut_calls = _patch_cut(mocker); burn_calls = _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("@long", "1", clip_profile="long")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="hook L", surfaces=("@long/instagram",)); led.save()
    led = _run(cfg)
    assert len(cut_calls) == 1 and cut_calls[0]["profile"] == "long" and cut_calls[0]["hook"] == "hook L"
    assert burn_calls == []                                        # the shared-clip burn path was NOT used
    r = next(iter(led.renders.values()))
    assert Path(r.path).read_bytes() == b"ACUT"                    # the post serves the per-account cut

def test_override_account_render_id_is_band_tagged(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    _patch_cut(mocker); _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("@long", "1", clip_profile="long")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", surfaces=("@long/instagram",)); led.save()
    led = _run(cfg)
    rid = next(iter(led.posts.values())).render_id
    assert rid != child_id("render", "clip_1", "H")               # NOT the un-tagged (global-band) id
    post = next(iter(led.posts.values()))
    assert post.clip_profile == "long"                            # provenance reflects the ACTUAL cut length

def test_default_account_uses_shared_clip_burn_byte_identical(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cut_calls = _patch_cut(mocker); burn_calls = _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("@a", "1")])                        # no clip_profile -> global band
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", surfaces=("@a/instagram",)); led.save()
    led = _run(cfg)
    assert cut_calls == [] and len(burn_calls) == 1               # shared-clip burn, no per-account cut
    post = next(iter(led.posts.values()))
    assert post.render_id == child_id("render", "clip_1", "H")    # un-tagged id (byte-identical)
    assert post.clip_profile == "talk"                            # the global profile

def test_same_hook_different_bands_distinct_renders(tmp_path, monkeypatch, mocker):
    # the collision guard: @short and @long with the SAME hook must NOT share one file (different lengths)
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    _patch_cut(mocker); _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("@short", "1", clip_profile="short"),
                         _acct("@long", "2", clip_profile="long")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="SAME",
               surfaces=("@short/instagram", "@long/instagram")); led.save()
    led = _run(cfg)
    rids = {p.render_id for p in led.posts.values()}
    assert len(rids) == 2 and len(led.renders) == 2               # band tag keeps them distinct despite one hook

def test_per_account_cut_fail_open_falls_back_to_shared_burn(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cut_calls = _patch_cut(mocker, returns=False)                 # the per-account cut FAILS
    burn_calls = _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("@long", "1", clip_profile="long")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", surfaces=("@long/instagram",)); led.save()
    led = _run(cfg)
    assert len(cut_calls) == 1 and len(burn_calls) == 1           # tried the cut, fell back to the shared burn
    r = next(iter(led.renders.values()))
    assert Path(r.path).exists()                                  # Render.path invariant: a usable file always exists
    p = next(iter(led.posts.values()))
    assert p.render_id and r.is_account_cut is False              # the render records it is NOT a real cut (fell back)
    assert p.clip_profile == "talk"                              # PROVENANCE TRUTH: shipped the global-band burn, not "long"

def test_successful_cut_records_is_account_cut(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    _patch_cut(mocker, returns=True); _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("@long", "1", clip_profile="long")])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, m_hook="H", surfaces=("@long/instagram",)); led.save()
    led = _run(cfg)
    assert next(iter(led.renders.values())).is_account_cut is True

def test_dedup_hit_reads_truth_not_intent(tmp_path, monkeypatch, mocker):
    # one account on TWO 9:16 platforms (ig+tiktok) -> SAME band-tagged render id -> the 2nd surface DEDUP-hits.
    # With the cut FAILING, BOTH posts must read the render's is_account_cut=False and stamp the global profile —
    # never the "long" lie just because the id was band-tagged (the reviewer's MEDIUM).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    _patch_cut(mocker, returns=False); _patch_burn(mocker)
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [_acct("@long", "1", clip_profile="long")])
    led = Ledger.load(cfg)
    led_obj = led
    led_obj.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920, duration=120.0))
    led_obj.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                              state=MomentState.clipped, hook="H"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": "c", "hashtags": []} for s in ("@long/instagram", "@long/tiktok")}
    led_obj.add_clip(clip); led_obj.save()
    led = _run(cfg)
    assert len(led.renders) == 1                                  # both surfaces share the one (failed-cut) render
    assert {p.clip_profile for p in led.posts.values()} == {"talk"}   # neither post lies about the length
