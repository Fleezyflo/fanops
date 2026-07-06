# P9: approval promotes awaiting->queued only; owner-moment clip is pre-rendered at crosspost.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt, PostState
from fanops.accounts import Accounts
from fanops.crosspost import crosspost_clips
from fanops.studio.actions_approve import approve_posts


def _accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [{"handle": "@a", "account_id": "1",
                                                            "platforms": ["instagram"], "status": "active"}]}))

def _seed(led, cfg):
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook="H", clip_profile="long", framing="top"))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    p = cfg.clips / "clip_1_9x16.mp4"; p.write_bytes(b"X")
    c = Clip(id="clip_1", parent_id="mom_1", path=str(p), aspect=Fmt.r9x16, state=ClipState.captioned)
    c.meta_captions = {"@a/instagram": {"caption": "c", "hashtags": []}}
    led.add_clip(c)


def test_approve_queues_without_render_warm(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    warm = mocker.patch("fanops.crosspost.render_account_cut")
    cfg = Config(root=tmp_path); _accounts(cfg)
    led = Ledger.load(cfg); _seed(led, cfg); led.save()
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z"); led.save()
    pid = next(iter(led.posts))
    assert led.posts[pid].clip_profile == "long"
    approve_posts(cfg, [pid])
    led2 = Ledger.load(cfg)
    assert led2.posts[pid].state is PostState.queued
    assert led2.posts[pid].clip_profile == "long"
    assert led2.renders == {}
    warm.assert_not_called()


def test_approve_preserves_mint_clip_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    cfg = Config(root=tmp_path); _accounts(cfg)
    led = Ledger.load(cfg); _seed(led, cfg); led.save()
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z"); led.save()
    p = next(iter(led.posts.values()))
    approve_posts(cfg, [p.id])
    assert Ledger.load(cfg).posts[p.id].clip_profile == "long"
