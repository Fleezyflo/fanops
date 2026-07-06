"""M6 RED — `go_live` must refuse a flip-to-live while any queued post is past-due.

PRD risk pinned: 'Operator flips live, an old past-due queued post fires immediately on the first
daemon tick.' Today's `golive.go_live` gates on accounts validity + ready channels + confirm, but
NOT on the schedule state of the bucket — so a backlog of past-due queued posts becomes a
machine-gun publish the instant the daemon ticks after the flip.

The fix is structural: a new gate that REFUSES the flip if any post in `PostState.queued` has a
scheduled_time <= now. The operator's path forward is explicit — respread first (M3), then flip.
This pins the gate; the GREEN implementation adds it to `go_live`."""
from __future__ import annotations
import json, os, pytest
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, PostState, ClipState, MomentState, Fmt,
                           Platform)
from fanops.timeutil import iso_z
from fanops.studio import golive

FIXED_DT = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
FIXED_ISO = iso_z(FIXED_DT)

_ENV_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY")
_ENV_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}


@pytest.fixture(autouse=True)
def _restore_env():
    yield
    for k, v in _ENV_BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)


def _seed_live_ready(cfg: Config, monkeypatch) -> None:
    """Seed enough that the OTHER go_live gates (accounts valid, ready channel, creds present) all
    pass — so any failure isolates to the past-due gate this test is pinning."""
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"},
         "backends": {"instagram": "postiz"}}]}))


def _seed_clip_and_queued_post(cfg: Config, *, post_id: str, scheduled_iso: str) -> str:
    """Seed one queued post with the given scheduled_time. Returns the post id."""
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"a/instagram": {"caption": "a", "hashtags": []}}
    led.add_clip(clip)
    led.add_post(Post(id=post_id, parent_id=clip.id, account="a", account_id="1",
                      platform=Platform.instagram, caption="c", state=PostState.queued,
                      scheduled_time=scheduled_iso, media_urls=["file:///clip_1_9x16.mp4"], public_url="dryrun://1"))
    led.save()
    return post_id


def test_go_live_refuses_when_any_queued_post_is_past_due(tmp_path, monkeypatch):
    """RED: a past-due queued post (scheduled_time <= now) MUST block the flip. The error message
    must NAME the count so the operator knows what to respread; FANOPS_LIVE must NOT be written."""
    cfg = _clean(monkeypatch, tmp_path); _seed_live_ready(cfg, monkeypatch)
    yesterday_iso = iso_z(FIXED_DT - timedelta(days=1))
    _seed_clip_and_queued_post(cfg, post_id="p_stale", scheduled_iso=yesterday_iso)

    res = golive.go_live(cfg, confirmed=True, now=FIXED_DT)
    assert res.ok is False, f"go_live should refuse with a past-due backlog, got ok=True: {res.detail}"
    assert "past-due" in res.error.lower() or "respread" in res.error.lower(), (
        f"error message must explain the past-due gate, got: {res.error!r}")
    # FANOPS_LIVE was NOT written — flip is atomic, refusal leaves no partial state.
    assert os.environ.get("FANOPS_LIVE") != "1"
    assert "FANOPS_LIVE=1" not in (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else True
    assert cfg.is_live is False


def test_go_live_allows_flip_when_all_queued_are_future(tmp_path, monkeypatch):
    """RED (boundary): a queued post with a strictly-future scheduled_time does NOT block. Same
    setup, the only difference is the scheduled_time — gate must be precise, not over-broad."""
    cfg = _clean(monkeypatch, tmp_path); _seed_live_ready(cfg, monkeypatch)
    tomorrow_iso = iso_z(FIXED_DT + timedelta(days=1))
    _seed_clip_and_queued_post(cfg, post_id="p_future", scheduled_iso=tomorrow_iso)

    res = golive.go_live(cfg, confirmed=True, now=FIXED_DT)
    assert res.ok is True, f"go_live should allow a future-only schedule: {res.error}"
    assert cfg.is_live is True


def test_go_live_ignores_unapproved_posts(tmp_path, monkeypatch):
    """RED (scope): the past-due gate must look ONLY at PostState.queued — an
    awaiting_approval post with a stale scheduled_time is the operator's bucket to clean up via
    Review, not a Go-Live blocker. (publish_due iterates only `queued`, so the live tick can't fire
    these regardless.)"""
    cfg = _clean(monkeypatch, tmp_path); _seed_live_ready(cfg, monkeypatch)
    yesterday_iso = iso_z(FIXED_DT - timedelta(days=1))
    # Manually flip the post back to awaiting_approval so it does NOT belong to the gate's scope.
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"a/instagram": {"caption": "a", "hashtags": []}}
    led.add_clip(clip)
    led.add_post(Post(id="p_unapproved", parent_id=clip.id, account="a", account_id="1",
                      platform=Platform.instagram, caption="c",
                      state=PostState.awaiting_approval, scheduled_time=yesterday_iso,
                      media_urls=["file:///clip_1_9x16.mp4"], public_url="dryrun://p_unapproved"))
    led.save()

    res = golive.go_live(cfg, confirmed=True, now=FIXED_DT)
    assert res.ok is True, (
        f"awaiting_approval with stale time must not block (publish_due never iterates it): {res.error}")


def test_go_live_refusal_is_atomic(tmp_path, monkeypatch):
    """RED: when the past-due gate refuses, NEITHER .env NOR os.environ is written — the flip is
    all-or-nothing. A half-applied flip would let the daemon read FANOPS_LIVE=1 from os.environ on
    its next tick while the disk still says dryrun (the worst kind of split state)."""
    cfg = _clean(monkeypatch, tmp_path); _seed_live_ready(cfg, monkeypatch)
    yesterday_iso = iso_z(FIXED_DT - timedelta(days=1))
    _seed_clip_and_queued_post(cfg, post_id="p_stale", scheduled_iso=yesterday_iso)

    pre_env_disk = (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else ""
    pre_environ_live = os.environ.get("FANOPS_LIVE")
    res = golive.go_live(cfg, confirmed=True, now=FIXED_DT)
    assert res.ok is False

    post_env_disk = (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else ""
    assert post_env_disk == pre_env_disk, f".env mutated despite refusal: {post_env_disk!r}"
    assert os.environ.get("FANOPS_LIVE") == pre_environ_live, "os.environ mutated despite refusal"
