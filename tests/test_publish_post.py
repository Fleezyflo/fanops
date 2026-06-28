# tests/test_publish_post.py — publish_post(cfg, post_id): ship ONE queued post NOW, ignoring its
# (future) schedule, scoped to just that post. The "Publish now" engine behind the Studio button.
# Reuses publish_due's per-post claim->network->finalize core (_publish_one) with the network OUTSIDE
# the ledger flock; returns the final post-state value (or None when nothing was claimable). Setup
# persists to disk (self-loading path) and assertions reload from disk.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.post.run import publish_post, publish_due


def _queued(led, cfg, pid="p1", cid="clip_1", when="2999-01-01T00:00:00Z"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="98432",
                      platform=Platform.instagram, caption="ship it",
                      scheduled_time=when, state=PostState.queued))
    led.save()


def test_publish_post_ships_a_future_scheduled_post_now(tmp_path, monkeypatch):
    # the whole point: a post scheduled for 2999 still publishes when the operator clicks Publish now.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)                      # dryrun
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2999-01-01T00:00:00Z")      # NOT due by schedule
    assert publish_post(cfg, "p1") == "published"
    assert Ledger.load(cfg).posts["p1"].state is PostState.published

def test_publish_post_is_scoped_to_the_target(tmp_path, monkeypatch):
    # other queued posts are UNTOUCHED — Publish now ships only the clicked piece, not the batch.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2999-01-01T00:00:00Z")
    _queued(led, cfg, pid="p2", cid="c2", when="2020-01-01T00:00:00Z")      # already due, but NOT clicked
    publish_post(cfg, "p1")
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p2"].state is PostState.queued                        # untouched

def test_publish_post_unknown_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1")
    assert publish_post(cfg, "nope") is None                                # no such post -> no raise, no change
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued

def test_publish_post_non_queued_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1")
    with Ledger.transaction(cfg) as led:
        led.posts["p1"].state = PostState.published                         # already published on disk
    assert publish_post(cfg, "p1") is None                                  # claim sees non-queued -> no-op
    assert Ledger.load(cfg).posts["p1"].state is PostState.published

def test_publish_post_propagates_fatal_auth(tmp_path, monkeypatch):
    # a bad key must HALT (raise), not silently mark the post failed — same contract as publish_due.
    import fanops.post.run as run
    from fanops.errors import BlotatoAuthError
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1")
    class BoomPoster:
        def publish(self, led, post_id): raise BlotatoAuthError("401 unauthorized")
    monkeypatch.setattr(run, "get_poster", lambda cfg, backend=None: BoomPoster())
    try:
        publish_post(cfg, "p1"); assert False, "expected BlotatoAuthError to propagate"
    except BlotatoAuthError:
        pass


def test_empty_integration_id_is_skipped_not_posted(tmp_path, monkeypatch):
    # CULM-1: a live post whose channel resolves to an EMPTY integration id must NOT be POSTed
    # (it would ship integration:{id:""} -> a silent dead post). It stays queued + breadcrumbs.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when="2000-01-01T00:00:00Z")
    with Ledger.transaction(cfg) as l: l.posts["p1"].account_id = ""           # never-mapped channel reached queued
    monkeypatch.setattr("fanops.post.run.get_poster",
                        lambda cfg, backend=None: (_ for _ in ()).throw(AssertionError("must not POST")))
    out = publish_due(cfg, now="2000-01-02T00:00:00Z")
    assert out["no_integration_id"] == 1 and out["published"] == 0
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued              # stays queued, re-driveable

def test_timeless_queued_post_does_not_auto_publish(tmp_path, monkeypatch):
    # CULM-4: a queued post with NO scheduled_time must NOT auto-publish via publish_due (defense-in-depth
    # on no-auto-publish). It parks (stays queued); publish_post (manual) is unaffected.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)                          # dryrun
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="c1", when=None)                            # queued but NO scheduled_time
    out = publish_due(cfg, now="2030-01-01T00:00:00Z")
    assert out["published"] == 0
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued              # parked, never published


def test_variant_render_uploaded_once_across_two_publishes(tmp_path, monkeypatch):
    # CULM-2: a per-account render's file must be uploaded at most ONCE (cached on Render.media_url),
    # not re-uploaded every approve->publish cycle (approval re-points media_urls to file://<render>).
    from fanops.models import Render
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.setattr("fanops.postiz_lifecycle.ensure_up", lambda cfg: None)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    rid = "render_x"; vf = cfg.clips / "v.mp4"; vf.parent.mkdir(parents=True, exist_ok=True); vf.write_bytes(b"V")
    led.add_render(Render(id=rid, clip_id="c1", account="@a", surface_key="@a|instagram", path=str(vf)))
    led.add_clip(Clip(id="c1", parent_id="mom_1", path=str(vf), state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="98", platform=Platform.instagram,
                      caption="x", state=PostState.queued, scheduled_time="2000-01-01T00:00:00Z",
                      render_id=rid, media_urls=[f"file://{vf}"]))
    led.save()
    calls = {"n": 0}
    up = lambda cfg, backend=None: (lambda c, pth: (calls.__setitem__("n", calls["n"] + 1) or "https://cdn/v.mp4"))
    monkeypatch.setattr("fanops.post.get_media_uploader", up)        # ensure_render_media (media.py) path
    monkeypatch.setattr("fanops.post.run.get_media_uploader", up)    # the legacy run.py direct-upload path
    class FakePoster:
        def publish(self, led, pid): led.posts[pid].state = PostState.submitted; return led
    monkeypatch.setattr("fanops.post.run.get_poster", lambda cfg, backend=None: FakePoster())
    assert publish_post(cfg, "p1") == "published"
    assert Ledger.load(cfg).renders[rid].media_url == "https://cdn/v.mp4"   # cached on the Render
    with Ledger.transaction(cfg) as l:
        l.posts["p1"].state = PostState.queued; l.posts["p1"].media_urls = [f"file://{vf}"]   # simulate a re-approval re-stamp
    publish_post(cfg, "p1")
    assert calls["n"] == 1                                            # uploaded ONCE total, not per cycle
