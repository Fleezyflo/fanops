# MOL-789: product_type is declared INPUT at the three Post() mint sites in publishing-service vocab.
# Instagram → Postiz "post" (never Meta AD|FEED|STORY|REELS). TikTok → None (Zernio OpenAPI createPost
# has no post-type enum; TikTokPlatformData.mediaType is video|photo only).
import json
from datetime import datetime, timezone
from fanops.accounts import Accounts
from fanops.config import Config
from fanops.crosspost import crosspost_clips
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source)
from fanops.post.postiz import build_postiz_payload
from fanops.studio import actions


def _seed_pipeline(cfg, mocker, *, platforms, captions):
    import subprocess
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": platforms, "status": "active"}]}))
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned, meta_captions=captions)
    led.add_clip(clip)
    real_run = subprocess.run
    def fake_run(cmd, **kw):
        if not (isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "ffmpeg"):
            return real_run(cmd, **kw)
        from pathlib import Path
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=fake_run)
    return led


def test_mint_surface_post_declares_ig_post_and_tiktok_none(tmp_path, mocker):
    # Mint site 1: crosspost._mint_surface_post via crosspost_clips.
    cfg = Config(root=tmp_path)
    led = _seed_pipeline(cfg, mocker, platforms=["instagram", "tiktok"],
                         captions={"a/instagram": {"caption": "ig", "hashtags": ["#x"]},
                                   "a/tiktok": {"caption": "tt", "hashtags": ["#y"]}})
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    by = {p.platform: p for p in led.posts.values()}
    assert by[Platform.instagram].product_type == "post"
    assert by[Platform.tiktok].product_type is None   # Zernio createPost: no post-type enum


def test_repost_post_declares_ig_post_never_copies_meta_vocab(tmp_path):
    # Mint site 2: studio.actions.repost_post — DECLARE, never copy (source may carry Meta REELS).
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c/clip_1.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="ig_1",
                          platform=Platform.instagram, caption="fire", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z", public_url="https://ig/reel/x",
                          product_type="REELS"))   # legacy Meta vocab on the shipped row
    new_id = actions.repost_post(cfg, "p1").detail["post_id"]
    np = Ledger.load(cfg).posts[new_id]
    assert np.product_type == "post"                 # declared service vocab, not "REELS"


def test_repost_post_tiktok_declares_none(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c/clip_1.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="tt_1",
                          platform=Platform.tiktok, caption="fire", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z", public_url="https://tiktok.com/x",
                          product_type="video"))     # would-be Zernio mediaType — must NOT be copied
    new_id = actions.repost_post(cfg, "p1").detail["post_id"]
    assert Ledger.load(cfg).posts[new_id].product_type is None


def test_crosspost_to_account_declares_ig_post_and_tiktok_none(tmp_path):
    # Mint site 3: studio.actions.crosspost_to_account.
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@b", "account_id": "ig_b", "platforms": ["instagram", "tiktok"], "status": "active",
         "integrations": {"instagram": "ig_b", "tiktok": "tt_b"}}]}))
    cfg.clips.mkdir(parents=True, exist_ok=True)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        cpath = cfg.clips / "c0.mp4"; cpath.write_bytes(b"\x00")
        led.add_clip(Clip(id="clip_0", parent_id="mom_1", path=str(cpath), aspect=Fmt.r9x16,
                          state=ClipState.queued,
                          meta_captions={"b/instagram": {"caption": "reuse", "hashtags": ["#x"]},
                                         "b/tiktok": {"caption": "tt", "hashtags": ["#y"]}}))
    now = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
    ig = actions.crosspost_to_account(cfg, "clip_0", "b", "instagram", now=now)
    tt = actions.crosspost_to_account(cfg, "clip_0", "b", "tiktok", now=now)
    assert ig.ok and tt.ok
    led = Ledger.load(cfg)
    assert led.posts[ig.detail["post_id"]].product_type == "post"
    assert led.posts[tt.detail["post_id"]].product_type is None


def test_minted_ig_row_survives_build_postiz_payload(tmp_path):
    # LANDMINE: minting Meta uppercase would raise in build_postiz_payload. A mint-site-only assert
    # would miss that outage — prove the declared token reaches the validator end-to-end.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c/clip_1.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="ig_1",
                          platform=Platform.instagram, caption="fire", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z", public_url="https://ig/reel/x",
                          product_type="REELS"))
    new_id = actions.repost_post(cfg, "p1").detail["post_id"]
    minted = Ledger.load(cfg).posts[new_id]
    assert minted.product_type == "post"
    payload = build_postiz_payload(
        integration_id=minted.account_id, platform=minted.platform.value, content=minted.caption,
        media_urls=["m1|https://cdn/a.mp4"], scheduled_time="2026-06-02T18:00:00Z",
        post_type=minted.product_type)
    assert payload["posts"][0]["settings"] == {"__type": "instagram", "post_type": "post"}
