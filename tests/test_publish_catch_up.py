"""publish_due re-slots rows past the doctor's grace window instead of sending them.

The window is backlog_grace_s (two daemon ticks). The slotting owner is
suggest_times_for_batch — the same function Approve's re-spread and Schedule's
Reschedule call. A just-due post still publishes. Dryrun is not a send.
"""
from datetime import datetime, timedelta, timezone

from fanops.config import Config
from fanops.doctor import backlog_grace_s
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Moment, MomentState, Platform, Post, PostState
from fanops.post.run import publish_due
from fanops.timeutil import iso_z, parse_iso

NOW = datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc)
NOW_Z = iso_z(NOW)
_LIVE_URL = "https://www.instagram.com/reel/AAA/"


def _live(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_MAX_POSTS_PER_ACCOUNT_PER_DAY", "2")


def _tick(monkeypatch, seconds=600):
    monkeypatch.setattr("fanops.daemon.installed_interval", lambda cfg: seconds)


def _seed(cfg, pid, when, *, account="a", cid=None):
    cid = cid or f"c_{pid}"
    f = cfg.clips / f"{cid}.mp4"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0.0, end=7.0, reason="worth posting",
                              state=MomentState.clipped))
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="98432",
                          platform=Platform.instagram, caption="ship it", post_type="post",
                          scheduled_time=when, state=PostState.queued,
                          media_urls=["https://h/v.mp4"], public_url="dryrun://x"))


class _Ok:
    def publish(self, led, post_id):
        led.posts[post_id] = led.posts[post_id].model_copy(update={
            "state": PostState.submitted, "submission_id": "s", "public_url": _LIVE_URL})
        return led


def _stub_poster(monkeypatch):
    import fanops.post.run as run
    monkeypatch.setattr(run, "_ensure_media", lambda *a, **k: None)
    monkeypatch.setattr(run, "get_poster", lambda cfg, backend=None: _Ok())


def test_backlog_grace_is_two_ticks():
    assert backlog_grace_s(600) == 1200
    assert backlog_grace_s(100) == 200


def test_within_grace_publishes_and_past_grace_is_respread(tmp_path, monkeypatch):
    _live(monkeypatch)
    _tick(monkeypatch, 100)                                 # grace = 200s
    _stub_poster(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "edge", iso_z(NOW - timedelta(seconds=200)))  # age == grace: still sent
    _seed(cfg, "over", iso_z(NOW - timedelta(seconds=201)))  # one second past the window
    _seed(cfg, "later", iso_z(NOW + timedelta(days=3)))      # not due
    import fanops.studio.views_common as vc
    seen = []
    real = vc.suggest_times_for_batch

    def _spy(cfg, posts, *, now):
        seen.append([p.id for p in posts])
        return real(cfg, posts, now=now)

    monkeypatch.setattr(vc, "suggest_times_for_batch", _spy)
    summary = publish_due(cfg, now=NOW_Z)
    led = Ledger.load(cfg)
    assert summary["published"] == 1 and summary["respread"] == 1 and summary["due"] == 1
    assert led.posts["edge"].state is PostState.published
    assert led.posts["over"].state is PostState.queued
    assert parse_iso(led.posts["over"].scheduled_time) > NOW
    assert led.posts["later"].scheduled_time == iso_z(NOW + timedelta(days=3))
    assert seen == [["over"]]


def test_past_grace_batch_obeys_the_daily_cap_and_does_not_send(tmp_path, monkeypatch):
    _live(monkeypatch)
    _tick(monkeypatch, 600)
    import fanops.post.run as run

    def _no_send(*_a, **_k):
        raise AssertionError("past-grace rows must not be sent")

    monkeypatch.setattr(run, "get_poster", _no_send)
    cfg = Config(root=tmp_path)
    old = iso_z(NOW - timedelta(days=4))
    for i in range(5):
        _seed(cfg, f"p{i}", old)
    summary = publish_due(cfg, now=NOW_Z)
    led = Ledger.load(cfg)
    assert summary["published"] == 0 and summary["due"] == 0 and summary["respread"] == 5
    times = [parse_iso(led.posts[f"p{i}"].scheduled_time) for i in range(5)]
    assert all(t > NOW for t in times)
    assert all(led.posts[f"p{i}"].state is PostState.queued for i in range(5))
    by_day: dict = {}
    for t in times:
        by_day[t.date()] = by_day.get(t.date(), 0) + 1
    assert max(by_day.values()) <= 2


def test_dryrun_past_grace_still_previews(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    _tick(monkeypatch, 600)
    cfg = Config(root=tmp_path)
    old = iso_z(NOW - timedelta(days=4))
    _seed(cfg, "dry", old)
    summary = publish_due(cfg, now=NOW_Z)
    post = Ledger.load(cfg).posts["dry"]
    assert summary["respread"] == 0 and summary["not_distributed"] >= 1 and summary["published"] == 0
    assert post.state is PostState.queued
    assert post.scheduled_time == old
    assert (cfg.scheduled / "dry.json").exists()
