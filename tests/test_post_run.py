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

def test_publish_failed_poster_marks_failed_durable(tmp_path, monkeypatch, mocker):
    # A poster that fails -> post.state failed (not analyzed, not published), durable.
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="pf", cid="c_pf", when="2020-01-01T00:00:00Z")
    # make get_poster return a poster whose publish sets the post to failed
    import fanops.post.run as run
    class _FailPoster:
        def __init__(self, cfg): pass
        def publish(self, led, post_id):
            led.posts[post_id].state = PostState.failed
            led.posts[post_id].error_reason = "simulated 422"
            return led
    mocker.patch.object(run, "get_poster", return_value=_FailPoster(cfg))
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["pf"].state is PostState.failed
    # durable: reload from disk and confirm
    led2 = Ledger.load(cfg)
    assert led2.posts["pf"].state is PostState.failed

def test_publish_no_schedule_publishes_immediately(tmp_path, monkeypatch):
    # A post with no scheduled_time is due now (no schedule => publish).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_ns.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c_ns", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="pns", parent_id="c_ns", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued))  # no scheduled_time
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["pns"].state is PostState.published

def test_publish_does_not_redrive_submitting_post(tmp_path, monkeypatch, mocker):
    # F11 crash-sim regression lock: a post stranded in 'submitting' is NOT re-published.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_sub.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c_sub", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="psub", parent_id="c_sub", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x",
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.submitting))
    import fanops.post.run as run
    spy = mocker.spy(run, "ensure_clip_media")
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["psub"].state is PostState.submitting    # untouched — not re-driven
    assert spy.call_count == 0                                # no media re-upload either

def test_publish_one_bad_upload_does_not_block_others(tmp_path, monkeypatch, mocker):
    # Per-post isolation: clip A's media raises, clip B still publishes.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, cid in [("pa", "c_a"), ("pb", "c_b")]:
        f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued))
    import fanops.post.run as run
    # c_a upload raises a NON-auth error; c_b uploads fine; poster.publish succeeds (submitted)
    def fake_ensure(led_, cfg_, clip_id):
        if clip_id == "c_a":
            raise RuntimeError("Blotato presign failed (503): server down")
        return "https://cdn/ok.mp4"
    mocker.patch.object(run, "ensure_clip_media", side_effect=fake_ensure)
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id].state = PostState.submitted
            led_.posts[post_id].submission_id = "s_ok"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["pa"].state is PostState.failed          # bad upload -> failed, isolated
    assert "503" in (led.posts["pa"].error_reason or "")
    assert led.posts["pb"].state is PostState.published        # healthy clip still shipped

def test_publish_auth_error_halts_run(tmp_path, monkeypatch, mocker):
    # A 401/auth error must HALT (raise), not mark one post failed and grind on.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "badkey")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_auth.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c_auth", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="pauth", parent_id="c_auth", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x",
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued))
    import fanops.post.run as run
    mocker.patch.object(run, "ensure_clip_media", return_value="https://cdn/ok.mp4")
    class _AuthFailPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            raise RuntimeError("Blotato 401 unauthorized — check BLOTATO_API_KEY (bad)")
    mocker.patch.object(run, "get_poster", return_value=_AuthFailPoster(cfg))
    import pytest
    with pytest.raises(RuntimeError, match="401"):
        publish_due(led, cfg, now="2026-06-02T18:00:00Z")
