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


def test_repair_upgrades_fan_all_default_when_persona_now_linked(tmp_path):
    """TikTok parity: fan_all_default from a pre-link cast upgrades to persona-donor picks on repair."""
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "perca.late", "account_id": "ig1", "platforms": ["instagram"], "status": "active",
         "persona": "ig zine", "persona_id": "underground-zine", "integrations": {"instagram": "ig1"}},
        {"handle": "hrmny-blog", "account_id": "tk1", "platforms": ["tiktok"], "status": "active",
         "persona": "", "persona_id": "underground-zine", "integrations": {"tiktok": "tk1"}},
    ]}))
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text(json.dumps({"personas": [
        {"id": "underground-zine", "name": "Z", "voice": "blunt zine voice",
         "content_focus": ["punchlines"], "energy": "high", "hook_angle": "curiosity"},
    ]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.decided, affinities=["perca.late"]))
    led.add_moment(Moment(id="mom_2", parent_id="src_1", content_token="7-14", start=7, end=14, reason="r2",
                          state=MomentState.decided, affinities=["perca.late"]))
    from fanops.models import AccountSelection, SelectionMethod, account_selection_id
    led.add_account_selection(AccountSelection(
        id=account_selection_id("src_1", "perca.late"), source_id="src_1", account="perca.late",
        moment_ids=["mom_1", "mom_2"], method=SelectionMethod.llm))
    led.add_account_selection(AccountSelection(
        id=account_selection_id("src_1", "hrmny-blog"), source_id="src_1", account="hrmny-blog",
        moment_ids=[], method=SelectionMethod.fan_all_default))
    led.save()
    led = repair_casting_selections(Ledger.load(cfg), cfg, Accounts.load(cfg), "src_1")
    sel = led.account_selection_for("src_1", "hrmny-blog")
    assert sel is not None and sel.method is SelectionMethod.migrated
    assert sel.moment_ids == ["mom_1", "mom_2"]
    assert "hrmny-blog" in led.moments["mom_1"].affinities
    assert account_selection_admits(cfg, led, led.moments["mom_1"], "hrmny-blog") is True
    assert account_selection_admits(cfg, led, led.moments["mom_2"], "hrmny-blog") is True



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


def test_crosspost_mints_tiktok_when_owner_in_affinities(tmp_path, mocker):
    # P8 (MOL-149): crosspost gate = affinity_admits only — repair_casting_selections no longer runs inside
    # crosspost_clips; each surface mints iff its account is in moment.affinities (owner-driven).
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.decided, affinities=["perca.late", "hrmny-blog"]))
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
