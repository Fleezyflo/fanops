# P9: crosspost mint stamps moment clip_profile/framing; no variant_hook / deferred render.
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, Moment, Source, ClipState, MomentState, Fmt, Post
from fanops.accounts import Accounts
from fanops.crosspost import crosspost_clips, render_spec


def _seed_accounts(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _seed_clip(led, cfg, *, moment_kw=None, surfaces=("a/instagram",)):
    mkw = dict(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
               state=MomentState.clipped, hook="HOOK")
    if moment_kw:
        mkw.update(moment_kw)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1080, height=1920))
    led.add_moment(Moment(**mkw))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    base = cfg.clips / "clip_1_9x16.mp4"; base.write_bytes(b"BASE")
    clip = Clip(id="clip_1", parent_id="mom_1", path=str(base), aspect=Fmt.r9x16, state=ClipState.captioned)
    clip.meta_captions = {s: {"caption": f"cap {s}", "hashtags": ["#x"]} for s in surfaces}
    led.add_clip(clip)

def _run(cfg, monkeypatch):
    monkeypatch.setenv("FANOPS_ACCOUNT_CASTING", "0")
    led = Ledger.load(cfg)
    return crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")


def test_mint_stamps_moment_profile_no_variant_fields(tmp_path, monkeypatch, mocker):
    mocker.patch("fanops.overlay.burn_hook_only")
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    led = Ledger.load(cfg)
    _seed_clip(led, cfg, moment_kw={"clip_profile": "long", "framing": "top"})
    led.save()
    led = _run(cfg, monkeypatch)
    p = next(iter(led.posts.values()))
    assert p.clip_profile == "long" and p.top_bias is True
    assert p.render_id is None and p.media_urls == []
    assert "variant_key" not in Post.model_fields and "variant_hook" not in Post.model_fields


def test_render_spec_wants_cut_for_any_hook(tmp_path):
    cfg = Config(root=tmp_path)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/x.mp4", aspect=Fmt.r9x16)
    m = Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
               state=MomentState.clipped, hook="H", clip_profile=cfg.clip_profile, framing="center")
    _, wants, prof, top = render_spec(cfg, clip=clip, hook="H", moment=m)
    assert wants is True and prof == cfg.clip_profile and top is False


def test_render_spec_same_id_for_same_moment_spec(tmp_path):
    from fanops.ids import child_id
    cfg = Config(root=tmp_path)
    clip = Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16)
    m = Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
               state=MomentState.clipped, hook="watch this", clip_profile="talk", framing="top")
    rid_a, *_ = render_spec(cfg, clip=clip, hook="watch this", moment=m)
    rid_b, *_ = render_spec(cfg, clip=clip, hook="watch this", moment=m)
    assert rid_a == rid_b == child_id("render", "clip_1", "watch this\x1fband:12-22\x1fframe:top")
