# tests/test_publish_queue.py — Track B: the manual publish-queue. The zero-dependency free path:
# list ready clips + captions, the operator posts by hand and marks them posted. No external service.
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState
from fanops.studio import views, actions

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _seed(cfg, when="2020-01-01T00:00:00Z", state=PostState.queued):
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="c1", parent_id="m1", path=str(cfg.clips / "c1.mp4"), state=ClipState.queued))
    led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="fire caption", state=state, scheduled_time=when, public_url="dryrun://p1"))
    led.save()


# ---- views.publish_queue ----
def test_lists_due_queued_post(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    r = views.publish_queue(cfg, now=_NOW)[0]
    assert r["post_id"] == "p1" and r["caption"] == "fire caption" and r["platform"] == "instagram" and r["due"] is True

def test_flags_not_due(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, when="2099-01-01T00:00:00Z")
    assert views.publish_queue(cfg, now=_NOW)[0]["due"] is False

def test_excludes_terminal_published(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.published)
    assert views.publish_queue(cfg, now=_NOW) == []

def test_lists_manually_resolvable_states(tmp_path):
    # ecc holistic audit GAP 1: the Publish tab must surface every state mark_published accepts
    # (failed/error/needs_reconcile), not only queued — else those posts are a UI dead end.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for st in (PostState.queued, PostState.failed, PostState.error, PostState.needs_reconcile):
        led.add_post(Post(id=f"p_{st.value}", parent_id="c1", account="a", account_id="1",
                          platform=Platform.instagram, caption="x", state=st,
                          scheduled_time="2020-01-01T00:00:00Z", public_url="dryrun://c1"))
    led.save()
    assert {r["post_id"] for r in views.publish_queue(cfg, now=_NOW)} == {
        "p_queued", "p_failed", "p_error", "p_needs_reconcile"}

def test_queue_row_carries_state(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.failed)
    assert views.publish_queue(cfg, now=_NOW)[0]["state"] == "failed"


# ---- actions.mark_published ----
def test_mark_published_sets_state_and_url(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = actions.mark_published(cfg, "p1", url="https://insta/p/abc")
    assert res.ok
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.published and p.public_url == "https://insta/p/abc"

def test_mark_published_unknown_errors(tmp_path):
    assert not actions.mark_published(Config(root=tmp_path), "nope").ok

def test_mark_published_rejects_already_published(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.published)
    res = actions.mark_published(cfg, "p1")
    assert not res.ok and "publish" in (res.error or "").lower()

def test_mark_published_accepts_error_state(tmp_path):
    # ecc:python-review: an `error`-state post (recoverable, like `failed`) must be markable, not stranded.
    # R1/D9: mark_published now REQUIRES a non-empty url (operator says "I posted by hand" -> they MUST
    # paste the permalink); passing a real https url here is the canonical happy path.
    cfg = Config(root=tmp_path); _seed(cfg, state=PostState.error)
    assert actions.mark_published(cfg, "p1", url="https://www.instagram.com/p/abc/").ok
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.published
    assert p.public_url == "https://www.instagram.com/p/abc/"

def test_unscheduled_post_sorts_last(tmp_path):
    # ecc:python-review: a None scheduled_time must sort AFTER a future-dated post, not as most urgent.
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_post(Post(id="future", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="f", state=PostState.queued, scheduled_time="2099-01-01T00:00:00Z", public_url="dryrun://future"))
    led.add_post(Post(id="none", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="n", state=PostState.queued, scheduled_time=None, public_url="dryrun://none"))
    led.save()
    ids = [r["post_id"] for r in views.publish_queue(cfg, now=_NOW)]
    assert ids.index("future") < ids.index("none")


# ---- CLI ----
def test_cli_publish_queue_prints(tmp_path, monkeypatch, capsys):
    from fanops.cli import main
    cfg = Config(root=tmp_path); _seed(cfg); monkeypatch.chdir(tmp_path)
    assert main(["publish-queue"]) == 0
    out = capsys.readouterr().out
    assert "p1" in out and "fire caption" in out


# ---- Studio ----
def test_publish_route_renders(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/publish")
    assert r.status_code == 200 and b"p1" in r.data

def test_publish_posted_route_marks(tmp_path):
    from fanops.studio.app import create_app
    cfg = Config(root=tmp_path); _seed(cfg)
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().post("/publish/posted/p1", data={"url": "https://x/p/1"})
    assert r.status_code == 200
    assert Ledger.load(cfg).posts["p1"].state is PostState.published
