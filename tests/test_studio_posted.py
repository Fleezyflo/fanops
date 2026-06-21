# tests/test_studio_posted.py — checkpoint 4: the Posted library (all-time shipped posts + metrics) and
# "Post again" reuse. A published post is immutable history; reuse spawns a NEW awaiting_approval post
# from the same clip (re-enters the approval gate). Honors fan-accounts-repost-freely (NOT a supersede).
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState, LIFT_SCORE
from fanops.studio import actions, views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _seed_published(cfg, *, pid="p1", url="https://insta/reel/x", lift=0.42, when="2026-06-01T00:00:00Z"):
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c/clip_1.mp4", state=ClipState.published))
        led.add_post(Post(id=pid, parent_id="clip_1", account="@a", account_id="ig_1",
                          platform=Platform.instagram, caption="fire #hiphop", hashtags=["#hiphop"],
                          state=PostState.published, scheduled_time=when, public_url=url,
                          metrics={LIFT_SCORE: lift}))


# ---- posted_library read-model ----
def test_posted_library_lists_published_with_url_and_lift(tmp_path):
    cfg = Config(root=tmp_path); _seed_published(cfg, pid="p1", url="https://insta/reel/x", lift=0.42)
    # an awaiting post must NOT appear in the Posted library
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p_await", parent_id="clip_1", account="@a", account_id="ig_1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval))
    rows = views.posted_library(Ledger.load(cfg), cfg)
    ids = {r.post_id for r in rows}
    assert "p1" in ids and "p_await" not in ids
    r = [x for x in rows if x.post_id == "p1"][0]
    assert r.public_url == "https://insta/reel/x" and r.lift_score == 0.42


def test_posted_library_newest_first(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_published(cfg, pid="old", when="2026-01-01T00:00:00Z")
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="new", parent_id="clip_1", account="@a", account_id="ig_1",
                          platform=Platform.instagram, caption="y", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z"))
    rows = views.posted_library(Ledger.load(cfg), cfg)
    assert [r.post_id for r in rows][:2] == ["new", "old"]


# ---- repost_post reuse ----
def test_repost_stamps_created_at(tmp_path):
    # content-lifecycle Phase 2: a repost is a fresh birth -> carries a wall-clock AWARE created_at.
    from fanops.timeutil import parse_iso
    cfg = Config(root=tmp_path); _seed_published(cfg, pid="p1")
    new_id = actions.repost_post(cfg, "p1").detail["post_id"]
    np = Ledger.load(cfg).posts[new_id]
    assert np.created_at and parse_iso(np.created_at).tzinfo is not None

def test_repost_creates_fresh_awaiting_post_distinct_id(tmp_path):
    cfg = Config(root=tmp_path); _seed_published(cfg, pid="p1")
    r = actions.repost_post(cfg, "p1")
    assert r.ok
    new_id = r.detail["post_id"]
    led = Ledger.load(cfg)
    assert new_id != "p1"
    np = led.posts[new_id]
    assert np.state is PostState.awaiting_approval and np.parent_id == "clip_1"
    assert np.caption == "fire #hiphop" and np.scheduled_time is None
    assert led.posts["p1"].state is PostState.published   # original untouched


def test_repost_twice_makes_two_distinct_posts(tmp_path):
    cfg = Config(root=tmp_path); _seed_published(cfg, pid="p1")
    a = actions.repost_post(cfg, "p1").detail["post_id"]
    b = actions.repost_post(cfg, "p1").detail["post_id"]
    assert a != b and a != "p1" and b != "p1"


def test_repost_after_reject_stays_distinct(tmp_path):
    # the real path: operator rejects the first repost draft, then requests another. The epoch counter
    # has no state filter, so a rejected repost still counts -> ids stay unique and monotonic.
    cfg = Config(root=tmp_path); _seed_published(cfg, pid="p1")
    a = actions.repost_post(cfg, "p1").detail["post_id"]
    actions.reject_posts(cfg, [a])
    b = actions.repost_post(cfg, "p1").detail["post_id"]
    assert a != b and b != "p1"
    led = Ledger.load(cfg)
    assert led.posts[a].state is PostState.rejected and led.posts[b].state is PostState.awaiting_approval


def test_repost_carries_variation_axis(tmp_path):
    # P2 attribution: a repost must carry the source's variation_axis (else the learning audit loses it).
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c/clip_1.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="ig_1",
                          platform=Platform.instagram, caption="c", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z", variation_axis="caption_angle"))
    new_id = actions.repost_post(cfg, "p1").detail["post_id"]
    assert Ledger.load(cfg).posts[new_id].variation_axis == "caption_angle"


def test_repost_carries_batch_id(tmp_path):
    # Account-First Studio (Face 1 T8): a repost MUST keep the source's batch grouping, else the reposted
    # clip silently drops out of its batch in Review/Schedule. Mirrors the variation_axis carry above.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c/clip_1.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="ig_1",
                          platform=Platform.instagram, caption="c", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z", batch_id="batch_x"))
    new_id = actions.repost_post(cfg, "p1").detail["post_id"]
    assert Ledger.load(cfg).posts[new_id].batch_id == "batch_x"


def test_repost_unknown_post_errors(tmp_path):
    cfg = Config(root=tmp_path)
    r = actions.repost_post(cfg, "nope")
    assert not r.ok and "no such post" in r.error


# ---- routes / nav ----
def test_get_posted_shows_post_and_repost_button(tmp_path):
    cfg = Config(root=tmp_path); _seed_published(cfg, pid="p1", url="https://insta/reel/x")
    html = _client(cfg).get("/posted").data
    assert b"https://insta/reel/x" in html and b"Post again" in html

def test_posted_route_renders_publish_day_header(tmp_path):
    # content-lifecycle Phase 3: the Posted panel groups by PUBLISH day (published_at) with a day header.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="clip_1", parent_id="m1", path="/c/clip_1.mp4", state=ClipState.published))
        led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="ig_1",
                          platform=Platform.instagram, caption="x", state=PostState.published,
                          scheduled_time="2026-06-01T00:00:00Z", published_at="2026-06-05T10:00:00Z"))
    html = _client(cfg).get("/posted").data
    assert b"2026-06-05" in html                                   # the publish-day header, not the schedule day

def test_nav_has_posted_link(tmp_path):
    cfg = Config(root=tmp_path)
    html = _client(cfg).get("/review").data
    assert b"/posted" in html

def test_repost_route_creates_awaiting_post(tmp_path):
    cfg = Config(root=tmp_path); _seed_published(cfg, pid="p1")
    r = _client(cfg).post("/posts/repost/p1")
    assert r.status_code == 200
    awaiting = [p for p in Ledger.load(cfg).posts.values() if p.state is PostState.awaiting_approval]
    assert len(awaiting) == 1 and awaiting[0].parent_id == "clip_1"
