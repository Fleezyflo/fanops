import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.post.run import publish_due

def _queued(led, cfg, pid="p1", cid="clip_1", when="2026-06-02T18:00:00Z"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="98432",
                      platform=Platform.instagram, caption="ship it",
                      scheduled_time=when, state=PostState.queued))

def test_publishes_only_due_posts(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)  # dryrun
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="due", cid="c_due", when="2020-01-01T00:00:00Z")     # past => due
    _queued(led, cfg, pid="future", cid="c_future", when="2999-01-01T00:00:00Z")  # not due
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["due"].state is PostState.published
    assert led.posts["future"].state is PostState.queued       # held back (FIX F12)

def test_publish_uploads_media_once_and_advances(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="clip_1", when="2020-01-01T00:00:00Z")
    _queued(led, cfg, pid="p2", cid="clip_1", when="2020-01-01T00:00:00Z")  # same clip, 2 posts
    # spy ensure_clip_media to prove one upload per clip
    import fanops.post.run as run
    spy = mocker.spy(run, "ensure_clip_media")
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["p1"].state is PostState.published and led.posts["p2"].state is PostState.published
    assert led.posts["p1"].media_urls[0].startswith("file://")
    # clip_1 media ensured but cached: both posts resolve to the same url
    assert led.clips["clip_1"].media_url and led.posts["p1"].media_urls == led.posts["p2"].media_urls

def test_publish_idempotent_skips_already_submitted(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, when="2020-01-01T00:00:00Z")
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["p1"].state is PostState.published
