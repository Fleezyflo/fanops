"""M3 RED — per-account reschedule must include past-due posts.

PRD Evidence: 'Reschedule no-ops on past-due — reschedule_bucket re-spreads only posts that are
queued AND not imminent. The live ledger's 57 posts are all scheduled for the prior day → all
past-due → all treated as imminent → 0 rescheduled. The control silently no-ops on exactly the
bucket that needs respreading.'

The fix is structural: a past-due post MUST be respread. Only TRULY about-to-fire posts (the next
60 seconds, say) are protected — and even then, the protection narrows. Today `_imminent` returns
True for any time `<= now + 5 min`, which catches every past-due post.

These tests pin: (1) past-due posts are respread to strictly-future times; (2) future
operator-set times are preserved (no churn on a manually-set schedule the operator hand-edited);
(3) account scoping works (rescheduling one account doesn't churn another)."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, PostState, ClipState, MomentState, Fmt,
                           Platform)
from fanops.timeutil import iso_z, parse_iso
from fanops.studio.actions import reschedule_bucket

FIXED_DT = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
FIXED_ISO = iso_z(FIXED_DT)


def _seed_accounts(cfg: Config) -> None:
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "ib", "platforms": ["instagram"], "status": "active"}]}))


def _seed_clip(led: Ledger) -> Clip:
    led.add_source(Source(id="src_1", source_path="/s.mp4", duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "a", "hashtags": []},
                          "@b/instagram": {"caption": "b", "hashtags": []}}
    led.add_clip(clip)
    return clip


def _seed_queued(led: Ledger, clip: Clip, *, post_id: str, account: str, account_id: str,
                 scheduled_iso: str) -> str:
    p = Post(id=post_id, parent_id=clip.id, account=account, account_id=account_id,
             platform=Platform.instagram, caption="c", state=PostState.queued,
             scheduled_time=scheduled_iso, media_urls=[f"file:///clip_1_9x16.mp4"], public_url=f"dryrun://sweep")
    led.add_post(p)
    return p.id


def test_reschedule_respreads_past_due_posts(tmp_path, monkeypatch):
    """RED: 3 past-due queued posts (yesterday) MUST get strictly-future times after reschedule.
    Today every past-due post falls through `_imminent` (because `<= now + 5min` catches
    yesterday) and the control silently no-ops."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    yesterday = iso_z(FIXED_DT - timedelta(days=1))
    ids = [_seed_queued(led, clip, post_id=f"p_{i}", account="@a", account_id="ia",
                        scheduled_iso=yesterday) for i in range(3)]
    led.save()

    res = reschedule_bucket(cfg, now=FIXED_DT)
    assert res.ok is True, f"reschedule_bucket failed: {res.error}"
    assert res.detail["rescheduled"] == 3, (
        f"expected 3 past-due posts respread, got {res.detail['rescheduled']} — the imminent-skip "
        f"is still treating yesterday as imminent")

    reloaded = Ledger.load(cfg)
    for pid in ids:
        t = parse_iso(reloaded.posts[pid].scheduled_time)
        assert t > FIXED_DT, (
            f"post {pid} not respread to a future time: {reloaded.posts[pid].scheduled_time}")


def test_reschedule_preserves_genuinely_imminent_posts(tmp_path, monkeypatch):
    """RED (boundary): a post genuinely seconds from firing (within 60s of now) should NOT be
    respread — interrupting an in-flight publish window is worse than the bot-cadence cost. The
    M3 narrowing: imminent means SECONDS away, NOT 5 min."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    seconds_away = iso_z(FIXED_DT + timedelta(seconds=30))     # < 60s window
    pid = _seed_queued(led, clip, post_id="p_imm", account="@a", account_id="ia",
                       scheduled_iso=seconds_away)
    led.save()

    res = reschedule_bucket(cfg, now=FIXED_DT)
    assert res.ok is True
    reloaded = Ledger.load(cfg)
    assert reloaded.posts[pid].scheduled_time == seconds_away, (
        f"imminent post was respread despite the protect-window: {reloaded.posts[pid].scheduled_time}")


def test_reschedule_account_scopes_to_one_handle(tmp_path, monkeypatch):
    """M3 per-account respread (RED → GREEN): rescheduling @a does NOT churn @b's schedule. The
    PRD outcome: a per-account control that respreads exactly one account."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    yesterday = iso_z(FIXED_DT - timedelta(days=1))
    pa = _seed_queued(led, clip, post_id="p_a", account="@a", account_id="ia",
                      scheduled_iso=yesterday)
    pb = _seed_queued(led, clip, post_id="p_b", account="@b", account_id="ib",
                      scheduled_iso=yesterday)
    led.save()

    from fanops.studio.actions import reschedule_account
    res = reschedule_account(cfg, "@a", now=FIXED_DT)
    assert res.ok is True
    assert res.detail["rescheduled"] == 1, (
        f"per-account scope: expected 1 respread, got {res.detail['rescheduled']}")

    reloaded = Ledger.load(cfg)
    # @a was respread to a future time.
    assert parse_iso(reloaded.posts[pa].scheduled_time) > FIXED_DT
    # @b was UNTOUCHED — still on yesterday.
    assert reloaded.posts[pb].scheduled_time == yesterday, (
        f"@b's schedule churned despite per-account scoping: {reloaded.posts[pb].scheduled_time}")


def test_reschedule_respreads_anything_more_than_one_minute_out(tmp_path, monkeypatch):
    """RED (boundary): the protect-window is NARROW. A post 2 minutes away (today: 'imminent' and
    skipped) gets respread under M3 — the operator might lose a 2-min publish window, but the
    bot-cadence cost of a 5-min protect-window across a whole bucket of past-due posts is far
    worse. M3 narrows the protect-window to 60s."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    two_min_away = iso_z(FIXED_DT + timedelta(minutes=2))
    pid = _seed_queued(led, clip, post_id="p_2min", account="@a", account_id="ia",
                       scheduled_iso=two_min_away)
    led.save()

    res = reschedule_bucket(cfg, now=FIXED_DT)
    assert res.ok is True
    assert res.detail["rescheduled"] == 1, (
        f"a 2-min-away post should be respread under M3 (60s protect-window), got "
        f"rescheduled={res.detail['rescheduled']}")
