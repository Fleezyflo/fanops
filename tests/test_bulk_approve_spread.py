"""M4 RED — bulk-approve must spread, never collide.

The operator's verbatim complaint: "the system schedules EVERYTHING on the same date and time."
The collide is unambiguous in the code: `actions_approve._approve_ids_with_render` walks N
selected post-ids in ONE transaction with ONE `now` stamp, calling `suggest_time(cfg, post, now=now)`
per post. For posts whose `surface_time(..., index=0)` short-circuits (`<= now`), the suggestion
becomes `iso_z(now + 1s)` — identical for every such post in the batch. For posts on the same
clip × same account × same platform (re-approval / repost variants) the SHA1 seed collapses to the
same minute too.

These tests pin the FIX-CONTRACT: a bulk-approve of N stale-time posts MUST produce N pairwise-
distinct future times, obeying a per-account minimum gap. A future operator-set time MUST be
preserved. Today's code FAILS (1) and (2) and is correct on (3); the GREEN implementation must
keep (3) while fixing (1) and (2)."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, PostState, ClipState, MomentState, Fmt,
                           Platform)
from fanops.timeutil import parse_iso, iso_z
from fanops.studio.actions_approve import approve_posts

FIXED_DT = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
FIXED_ISO = iso_z(FIXED_DT)

# Minimum per-account spacing between two consecutive bulk-approved posts. The PRD calls for
# realistic 2-3h cadence on a respread; on a single Approve click the floor is looser because the
# operator may be approving an already-spaced manual schedule. Keep this conservative at 30 min:
# any GREEN that prevents the collide ALSO obeys this floor by construction.
MIN_PER_ACCOUNT_GAP_MIN = 30


def _seed_accounts(cfg: Config) -> None:
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "ib", "platforms": ["instagram"], "status": "active"}]}))


def _seed_clip(led: Ledger) -> Clip:
    """One captioned 9:16 clip — substrate for N awaiting_approval posts. The clip is captioned
    so approval doesn't need to render; this isolates the spread invariant from the render path."""
    led.add_source(Source(id="src_1", source_path="/s.mp4", width=1920, height=1080,
                          duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {
        "@a/instagram": {"caption": "a", "hashtags": []},
        "@b/instagram": {"caption": "b", "hashtags": []},
    }
    led.add_clip(clip)
    return clip


def _born_posts(led: Ledger, clip: Clip, *, n_per_account: int = 3,
                stale_iso: str | None) -> list[str]:
    """Hand-mint N awaiting_approval posts per account (@a, @b) on the same clip+platform with the
    same stale/missing scheduled_time. Returns the post-ids in deterministic order. This mirrors
    the operator scenario: a backlog of past-due posts the operator selects for bulk Approve."""
    ids: list[str] = []
    for handle, account_id in (("@a", "ia"), ("@b", "ib")):
        for k in range(n_per_account):
            pid = f"p_{handle.strip('@')}_{k}"
            p = Post(id=pid, parent_id=clip.id, account=handle, account_id=account_id,
                     platform=Platform.instagram, caption="c", state=PostState.awaiting_approval,
                     scheduled_time=stale_iso, media_urls=[f"file:///clip_1_9x16.mp4"])
            led.add_post(p)
            ids.append(pid)
    return ids


def test_bulk_approve_n_stale_posts_get_n_distinct_times(tmp_path, monkeypatch):
    """RED: select N=6 awaiting_approval posts (3 per account, two accounts, same clip+platform)
    with stale scheduled_time = yesterday. Approve in ONE batch. The 6 resulting scheduled_time
    values MUST be pairwise distinct. Today they collapse to identical iso_z(now+1s) because
    suggest_time short-circuits the seed%50==0 && jitter==0 case to a single deterministic value
    AND _approve_ids_with_render passes the same `now` to every post."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")    # isolate scheduling from the render path
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    stale = iso_z(FIXED_DT - timedelta(days=1))             # yesterday — stale by construction
    ids = _born_posts(led, clip, n_per_account=3, stale_iso=stale)
    led.save()

    res = approve_posts(cfg, ids, now=FIXED_DT)
    assert res.ok is True, f"approve_posts failed: {res.error}"

    reloaded = Ledger.load(cfg)
    times = [reloaded.posts[pid].scheduled_time for pid in ids]
    # CORE INVARIANT: N approved posts -> N pairwise-distinct times. This is what the operator means
    # by "not the same date and time." A collide-by-one violates the contract.
    assert len(set(times)) == len(times), (
        f"bulk-approve collided: {len(ids)} posts produced {len(set(times))} distinct times. "
        f"times={times}")
    # And every time is strictly future (no `<= now` regression).
    for t in times:
        assert parse_iso(t) > FIXED_DT, f"non-future approve time {t} <= {FIXED_ISO}"


def test_bulk_approve_respects_per_account_cadence(tmp_path, monkeypatch):
    """RED: within ONE account's slice of the batch, consecutive approved times must be ≥ 30 min
    apart. Today they all collapse to the same minute or land within 1-2 seconds of each other —
    machine-gun cadence the moment publish_due picks them up."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    stale = iso_z(FIXED_DT - timedelta(days=1))
    ids = _born_posts(led, clip, n_per_account=4, stale_iso=stale)
    led.save()

    res = approve_posts(cfg, ids, now=FIXED_DT)
    assert res.ok is True

    reloaded = Ledger.load(cfg)
    by_account: dict[str, list[datetime]] = {}
    for pid in ids:
        p = reloaded.posts[pid]
        by_account.setdefault(p.account, []).append(parse_iso(p.scheduled_time))
    for handle, dts in by_account.items():
        dts.sort()
        gaps_min = [(b - a).total_seconds() / 60.0 for a, b in zip(dts, dts[1:])]
        assert all(g >= MIN_PER_ACCOUNT_GAP_MIN for g in gaps_min), (
            f"per-account cadence violated for {handle}: gaps_min={gaps_min} "
            f"(MIN_PER_ACCOUNT_GAP_MIN={MIN_PER_ACCOUNT_GAP_MIN}); times={[iso_z(d) for d in dts]}")


def test_bulk_approve_mixed_account_batch_monotonic_per_account(tmp_path, monkeypatch):
    """CHARACTERIZATION (M4 REFACTOR): in a MIXED-ACCOUNT batch, each account's times — taken in
    isolation — are strictly monotonic. The cumulative walk makes the bad path
    (`STEP - (JITTER_MAX-1)` floor dip) unconstructable; this test pins that the property holds when
    accounts interleave in the input order."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    stale = iso_z(FIXED_DT - timedelta(days=1))
    ids = _born_posts(led, clip, n_per_account=5, stale_iso=stale)
    led.save()

    assert approve_posts(cfg, ids, now=FIXED_DT).ok is True
    reloaded = Ledger.load(cfg)
    by_acc: dict[str, list[datetime]] = {}
    for pid in ids:
        p = reloaded.posts[pid]
        by_acc.setdefault(p.account, []).append(parse_iso(p.scheduled_time))
    for handle, dts in by_acc.items():
        assert dts == sorted(dts), (
            f"per-account times not monotonic for {handle}: {[iso_z(d) for d in dts]}")


def test_bulk_approve_collide_path_unconstructable(tmp_path, monkeypatch):
    """CHARACTERIZATION (M4 REFACTOR): the suggest_times_for_batch contract is that for ANY input
    of distinct post ids, the output map has pairwise-distinct ISO-Z values. This is the
    pure-function boundary version of the integration test above — proves the spread engine itself,
    not the wiring."""
    from fanops.studio.views_common import suggest_times_for_batch
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    stale = iso_z(FIXED_DT - timedelta(days=1))
    ids = _born_posts(led, clip, n_per_account=8, stale_iso=stale)
    led.save()
    posts = [Ledger.load(cfg).posts[pid] for pid in ids]
    sched = suggest_times_for_batch(cfg, posts, now=FIXED_DT)
    assert len(sched) == len(ids)
    assert len(set(sched.values())) == len(ids), (
        f"spread engine produced duplicates: {sched}")


def test_bulk_approve_preserves_operator_future_time(tmp_path, monkeypatch):
    """GREEN-CONTRACT (already passes — pinned characterization): a post whose scheduled_time is
    a strictly-FUTURE operator-set value must NOT be rewritten by approval. The existing `keep`
    branch in Ledger.approve_post enforces this; the M4 spread engine MUST preserve it (don't
    silently re-time an operator-edited schedule)."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    future_iso = iso_z(FIXED_DT + timedelta(hours=12))      # strictly-future, operator-chosen
    ids = _born_posts(led, clip, n_per_account=1, stale_iso=future_iso)
    led.save()

    res = approve_posts(cfg, ids, now=FIXED_DT)
    assert res.ok is True

    reloaded = Ledger.load(cfg)
    for pid in ids:
        assert reloaded.posts[pid].scheduled_time == future_iso, (
            f"approval rewrote a future operator-set time: {pid} -> "
            f"{reloaded.posts[pid].scheduled_time} (expected {future_iso})")
