# tests/test_publish_post.py — publish_post(cfg, post_id): ship ONE queued post NOW, ignoring its
# (future) schedule, scoped to just that post. The "Publish now" engine behind the Studio button.
# Reuses publish_due's per-post claim->network->finalize core (_publish_one) with the network OUTSIDE
# the ledger flock; returns the final post-state value (or None when nothing was claimable). Setup
# persists to disk (self-loading path) and assertions reload from disk.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.post.run import publish_post


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
    monkeypatch.setattr(run, "get_poster", lambda cfg: BoomPoster())
    try:
        publish_post(cfg, "p1"); assert False, "expected BlotatoAuthError to propagate"
    except BlotatoAuthError:
        pass
