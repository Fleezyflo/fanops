# tests/test_casting_affinities_repair.py — RF1 drift: Moment.affinities without durable AccountSelection
# made persona-less TikTok surfaces silently DENY at crosspost (the wiring bug, not approvals/UI).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, MomentState, Clip, ClipState, Fmt, Platform)
from fanops.accounts import Accounts
from fanops.casting import repair_casting_selections, account_selection_admits
from fanops.crosspost import crosspost_clips
from fanops.models import SelectionMethod


def _fake_ffmpeg(mocker):
    def fake_run(cmd, **kw):
        from pathlib import Path
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)


def _seed_accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "perca.late", "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "persona": "blunt underground zine", "integrations": {"instagram": "ig1"}, "backends": {"instagram": "postiz"}},
        {"handle": "hrmny-blog", "account_id": "tk1", "platforms": ["tiktok"], "status": "active",
         "persona": "", "integrations": {"tiktok": "tk1"}, "backends": {"tiktok": "zernio"}},
    ]}))


def test_repair_fan_all_default_for_personaless_when_only_affinities_exist(tmp_path):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.decided, affinities=["perca.late"]))
    led.save(); led = Ledger.load(cfg)
    assert not led.selections_of_source("src_1")
    led = repair_casting_selections(led, cfg, Accounts.load(cfg), "src_1")
    sel = led.account_selection_for("src_1", "hrmny-blog")
    assert sel is not None and sel.method is SelectionMethod.fan_all_default
    assert account_selection_admits(cfg, led, led.moments["mom_1"], "hrmny-blog") is True
    assert account_selection_admits(cfg, led, led.moments["mom_1"], "perca.late") is True


def test_crosspost_mints_tiktok_after_affinities_repair(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.decided, affinities=["perca.late"]))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {
        "perca.late/instagram": {"caption": "ig cap", "hashtags": []},
        "hrmny-blog/tiktok": {"caption": "tt cap", "hashtags": []},
    }
    led.add_clip(clip); led.save()
    _fake_ffmpeg(mocker)
    led = crosspost_clips(Ledger.load(cfg), cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    posts = {(p.account, p.platform) for p in led.posts.values()}
    assert ("perca.late", Platform.instagram) in posts
    assert ("hrmny-blog", Platform.tiktok) in posts
