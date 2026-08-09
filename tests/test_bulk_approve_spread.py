"""M4 RED — bulk-approve must spread, never collide.

The operator's verbatim complaint: "the system schedules EVERYTHING on the same date and time."
The collide is unambiguous in the code: `actions_approve._approve_ids_with_render` walks N
selected post-ids in ONE transaction with ONE `now` stamp, calling `suggest_time(cfg, post, now=now)`
per post. For posts whose `surface_time(..., index=0)` short-circuits (`<= now`), the suggestion
becomes `iso_z(now + 1s)` — identical for every such post in the batch. For posts on the same
clip × same account × same platform (re-approval / repost variants) the SHA1 seed collapses to the
same minute too.

These tests pin the FIX-CONTRACT: a bulk-approve of N stale-time posts MUST produce N pairwise-
distinct future times, obeying a per-account minimum gap. After promote, MOL-869 re-spreads each
approved account's queued set so identical future mint times do not lockstep — spread wins.
"""
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
        "a/instagram": {"caption": "a", "hashtags": []},
        "b/instagram": {"caption": "b", "hashtags": []},
    }
    led.add_clip(clip)
    return clip


def _born_posts(led: Ledger, clip: Clip, *, n_per_account: int = 3,
                stale_iso: str | None) -> list[str]:
    """Hand-mint N awaiting_approval posts per account (@a, @b) on the same clip+platform with the
    same stale/missing scheduled_time. Returns the post-ids in deterministic order. This mirrors
    the operator scenario: a backlog of past-due posts the operator selects for bulk Approve."""
    ids: list[str] = []
    for handle, account_id in (("a", "ia"), ("b", "ib")):
        for k in range(n_per_account):
            pid = f"p_{handle.strip('@')}_{k}"
            p = Post(id=pid, parent_id=clip.id, account=handle, account_id=account_id,
                     platform=Platform.instagram, caption="c", state=PostState.awaiting_approval,
                     scheduled_time=stale_iso, media_urls=["file:///clip_1_9x16.mp4"], public_url="dryrun://sweep")
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


def _bare_posts(handle: str, n: int, *, account_id: str = "ia") -> list[Post]:
    """N awaiting_approval Posts for ONE account, ids zero-padded so the allocator's id-sort is the
    numeric order. Built WITHOUT the ledger: suggest_times_for_batch is pure over (id, account), so a
    106-post capacity case needs no clip/source substrate."""
    return [Post(id=f"{handle}_{k:03d}", parent_id="clip_1", account=handle, account_id=account_id,
                 platform=Platform.instagram, caption="c", state=PostState.awaiting_approval,
                 media_urls=["file:///clip_1_9x16.mp4"], public_url="dryrun://sweep") for k in range(n)]


def _per_day(days) -> list[int]:
    """Slot counts per day, in chronological day order."""
    counts: dict = {}
    for d in days:
        counts[d] = counts.get(d, 0) + 1
    return [counts[d] for d in sorted(counts)]


def test_batch_spread_caps_at_ten_per_account_per_day(tmp_path, monkeypatch):
    """MOL-708 RED — the incident, exactly: 106 queued videos for ONE account. Today the cumulative
    walk has no notion of a calendar day, so it runs straight through midnight at cadence and piles
    piles on a single day. The contract: three per operator-local day, then the remainder —
    3x35 + 1 for 106 — with every post preserved."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    from fanops.studio.views_common import suggest_times_for_batch
    posts = _bare_posts("a", 106)
    sched = suggest_times_for_batch(cfg, posts, now=FIXED_DT)
    assert len(sched) == 106, "every post must get a slot — a cap must never DROP a post"
    per_day = _per_day([parse_iso(t).date() for t in sched.values()])
    assert per_day == [3] * 35 + [1], f"daily capacity violated: per_day={per_day}"


def test_daily_cap_keeps_the_gap_distinctness_and_account_isolation(tmp_path, monkeypatch):
    """MOL-708 RED — the cap must not buy its capacity by breaking the M4/M7 invariants: consecutive
    per-account gaps stay >= STEP, timestamps stay pairwise distinct (no shared minute — the other
    half of the incident), and two accounts allocate INDEPENDENTLY (a full day for @a never displaces
    @b, and @b's posts never count against @a's capacity)."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    from fanops.studio.views_common import suggest_times_for_batch
    posts = _bare_posts("a", 12) + _bare_posts("b", 12, account_id="ib")
    sched = suggest_times_for_batch(cfg, posts, now=FIXED_DT)
    assert len(set(sched.values())) == len(sched), f"duplicate slot minute: {sched}"
    for handle in ("a", "b"):
        dts = sorted(parse_iso(sched[p.id]) for p in posts if p.account == handle)
        gaps_min = [(b - a).total_seconds() / 60.0 for a, b in zip(dts, dts[1:])]
        assert all(g >= MIN_PER_ACCOUNT_GAP_MIN for g in gaps_min), f"{handle} gap floor: {gaps_min}"
        # Each account independently gets 3/day then overflow.
        assert _per_day([d.date() for d in dts]) == [3, 3, 3, 3], (
            f"{handle} not independently capped: {[iso_z(d) for d in dts]}")


def test_daily_cap_boundary_is_operator_local_not_utc(tmp_path, monkeypatch):
    """MOL-708 RED — the day boundary is OPERATOR-LOCAL midnight (cfg.operator_tz), matching
    publish_buckets' operator-local hour/weekday. Pinned with a +04:00 zone where the two readings
    genuinely disagree: from 16:00 local the first ten slots fill the LOCAL day while all twelve
    still share ONE UTC date — so a UTC-bucketed cap would report 12-on-one-day and let an 11th
    through, and a local-bucketed cap correctly reports 10 + 2."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "Asia/Dubai")          # UTC+4, no DST
    from zoneinfo import ZoneInfo
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    assert cfg.operator_tz == "Asia/Dubai", "FANOPS_OPERATOR_TZ not reading through cfg"
    from fanops.studio.views_common import suggest_times_for_batch
    posts = _bare_posts("a", 12)
    sched = suggest_times_for_batch(cfg, posts, now=FIXED_DT)       # 12:00Z == 16:00 local
    zone = ZoneInfo("Asia/Dubai")
    local_days = [parse_iso(t).astimezone(zone).date() for t in sched.values()]
    assert _per_day(local_days) == [3, 3, 3, 3], (
        f"operator-local capacity violated: {sorted(sched.values())}")
    utc_days = _per_day([parse_iso(t).date() for t in sched.values()])
    assert utc_days[0] > 3, (
        "test is not discriminating: the +04:00 case must put >3 slots on ONE UTC date, "
        f"otherwise a UTC-bucketed cap would pass it too (utc_days={utc_days})")


def test_occupancy_at_capacity_pushes_the_whole_batch_to_the_next_day(tmp_path, monkeypatch):
    """MOL-710 RED (allocator boundary): the cap only ever saw the batch in hand. Hand the allocator ten
    posts already sitting on today for this account and the incoming batch must skip today entirely —
    without `occupied` all three would land on a day that is already full."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    from fanops.studio.views_common import suggest_times_for_batch
    held = _bare_posts("a", 10)                                  # already queued, all on FIXED_DT's day
    for k, p in enumerate(held):
        held[k] = p.model_copy(update={"state": PostState.queued,
                                       "scheduled_time": iso_z(FIXED_DT + timedelta(minutes=31 * (k + 1)))})
    incoming = [Post(id=f"a_new_{k}", parent_id="clip_1", account="a", account_id="ia",
                     platform=Platform.instagram, caption="c", state=PostState.awaiting_approval,
                     media_urls=["file:///clip_1_9x16.mp4"], public_url="dryrun://sweep") for k in range(3)]
    sched = suggest_times_for_batch(cfg, incoming, now=FIXED_DT, occupied=held)
    days = {parse_iso(t).date() for t in sched.values()}
    assert FIXED_DT.date() not in days, (
        f"batch landed on a day already at capacity: {sorted(sched.values())}")
    assert len(days) == 1 and len(sched) == 3            # all three on the SAME next open day, none dropped


def test_window_day_cap_overflow_does_not_stack_at_open(tmp_path, monkeypatch):
    """Re-spread / batch overflow with daily_window: after the 10/day cap, _roll_into_window snaps
    candidates to local open (09:00). Cursor must sync to that rolled `t` or every overflow slot
    collapses onto the same 09:00:00 (operator calendar piles on days 2+)."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "Asia/Dubai")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active",
         "daily_window": [9, 23]}]}))
    assert cfg.account_window("a") == (9, 23)
    from zoneinfo import ZoneInfo
    from collections import Counter
    from fanops.studio.views_common import suggest_times_for_batch, _DAILY_ACCOUNT_CAP
    n = _DAILY_ACCOUNT_CAP + 8
    posts = _bare_posts("a", n)
    sched = suggest_times_for_batch(cfg, posts, now=FIXED_DT)
    assert len(sched) == n and len(set(sched.values())) == n, (
        f"duplicate ISO after window+cap overflow: {sorted(sched.values())}")
    zone = ZoneInfo("Asia/Dubai")
    by_day: dict[str, list] = {}
    for t in sched.values():
        local = parse_iso(t).astimezone(zone)
        by_day.setdefault(local.date().isoformat(), []).append(local)
    assert len(by_day) >= 2, f"expected overflow onto a second local day: {sorted(by_day)}"
    for day, dts in sorted(by_day.items()):
        open_hits = sum(1 for d in dts if d.hour == 9 and d.minute == 0 and d.second == 0)
        assert open_hits <= 1, f"{day}: stacked at 09:00:00 ({open_hits} posts)"
        dts_sorted = sorted(dts)
        gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(dts_sorted, dts_sorted[1:])]
        assert all(g >= MIN_PER_ACCOUNT_GAP_MIN for g in gaps), f"{day} gaps: {gaps}"
    overflow_day = sorted(by_day)[1]
    hm = Counter(d.strftime("%H:%M:%S") for d in by_day[overflow_day])
    assert hm.get("09:00:00", 0) <= 1, f"overflow pile: {hm}"


def test_multi_account_overflow_opens_are_not_lockstep(tmp_path, monkeypatch):
    """Cross-account: per-account anchors must survive _roll_into_window. Zeroing open to :00 made
    every handle share the identical 09:00:00 on overflow days (operator: 'every account posting
    at the same second')."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "America/New_York")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "ia", "platforms": ["instagram"], "status": "active",
         "daily_window": [9, 23]},
        {"handle": "@b", "account_id": "ib", "platforms": ["instagram"], "status": "active",
         "daily_window": [9, 23]},
        {"handle": "@c", "account_id": "ic", "platforms": ["instagram"], "status": "active",
         "daily_window": [9, 23]},
        {"handle": "@d", "account_id": "id", "platforms": ["instagram"], "status": "active",
         "daily_window": [9, 23]}]}))
    from zoneinfo import ZoneInfo
    from fanops.studio.views_common import suggest_times_for_batch, _DAILY_ACCOUNT_CAP
    posts = (_bare_posts("a", _DAILY_ACCOUNT_CAP + 2)
             + _bare_posts("b", _DAILY_ACCOUNT_CAP + 2, account_id="ib")
             + _bare_posts("c", _DAILY_ACCOUNT_CAP + 2, account_id="ic")
             + _bare_posts("d", _DAILY_ACCOUNT_CAP + 2, account_id="id"))
    sched = suggest_times_for_batch(cfg, posts, now=FIXED_DT)
    assert len(set(sched.values())) == len(sched), f"batch not pairwise-distinct: {sorted(sched.values())}"
    zone = ZoneInfo("America/New_York")
    days = sorted({parse_iso(t).astimezone(zone).date() for t in sched.values()})
    assert len(days) >= 2
    overflow = days[1]
    opens = {}
    for p in posts:
        local = parse_iso(sched[p.id]).astimezone(zone)
        if local.date() != overflow:
            continue
        cur = opens.get(p.account)
        if cur is None or local < cur:
            opens[p.account] = local
    assert len(opens) == 4, opens
    # Not all four first-of-day slots share the same wall-clock minute
    stamps = {dt.replace(second=0, microsecond=0) for dt in opens.values()}
    assert len(stamps) >= 2, f"all accounts opened lockstep: {opens}"



def test_same_day_consecutive_gaps_at_most_six_hours(tmp_path, monkeypatch):
    """Operator: no more than nine hours between consecutive same-account posts on a day."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_OPERATOR_TZ", "America/New_York")
    monkeypatch.setenv("FANOPS_REALISTIC_CADENCE", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    from zoneinfo import ZoneInfo
    from fanops.studio.views_common import suggest_times_for_batch, _DAILY_ACCOUNT_CAP, _MAX_GAP_MIN
    posts = _bare_posts("a", _DAILY_ACCOUNT_CAP)
    sched = suggest_times_for_batch(cfg, posts, now=FIXED_DT)
    zone = ZoneInfo("America/New_York")
    dts = sorted(parse_iso(sched[p.id]).astimezone(zone) for p in posts)
    assert len(dts) == _DAILY_ACCOUNT_CAP
    gaps_min = [(b - a).total_seconds() / 60.0 for a, b in zip(dts, dts[1:])]
    assert all(g <= _MAX_GAP_MIN for g in gaps_min), f"max-gap 6h violated: {gaps_min}"
    assert all(d.date() == dts[0].date() for d in dts), "cap-sized batch must fit one local day"


def test_an_untimed_or_garbage_occupied_post_holds_no_day(tmp_path, monkeypatch):
    """MOL-710 — occupancy is per-DAY, so a post with no (or an unparseable) scheduled_time occupies
    nothing: it has not claimed a slot. Fail-safe, and it keeps a torn row from silently eating capacity."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    from fanops.studio.views_common import _occupancy_by_day
    held = _bare_posts("a", 3)
    held[0].scheduled_time = None
    held[1].scheduled_time = "garbage"
    held[2].scheduled_time = iso_z(FIXED_DT)
    occ = _occupancy_by_day(held, cfg)
    assert occ == {("a", FIXED_DT.date().isoformat()): 1}, f"unexpected occupancy: {occ}"
    assert _occupancy_by_day(None, cfg) == {}            # default/None is empty, not a crash


def test_a_second_bulk_approve_cannot_refill_a_full_day(tmp_path, monkeypatch):
    """MOL-710 RED (the real hole, end to end): approve ten posts, then approve ten MORE. The second
    batch's allocator never saw the first batch's now-queued posts, so both landed ten-on-one-day = 20.
    Contract: across both approvals no operator-local day holds more than ten posts for the account, and
    all twenty are still approved."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    stale = iso_z(FIXED_DT - timedelta(days=1))
    def _seed(prefix: str) -> list[str]:
        ids = []
        for k in range(10):
            pid = f"p_{prefix}_{k:02d}"
            led.add_post(Post(id=pid, parent_id=clip.id, account="a", account_id="ia",
                              platform=Platform.instagram, caption="a", state=PostState.awaiting_approval,
                              scheduled_time=stale, media_urls=["file:///clip_1_9x16.mp4"],
                              public_url="dryrun://sweep"))
            ids.append(pid)
        return ids
    first, second = _seed("one"), _seed("two")
    led.save()

    assert approve_posts(cfg, first, now=FIXED_DT).ok is True
    assert approve_posts(cfg, second, now=FIXED_DT).ok is True

    reloaded = Ledger.load(cfg)
    queued = [p for p in reloaded.posts.values() if p.state is PostState.queued and p.account == "a"]
    assert len(queued) == 20, f"an approval was lost: {len(queued)} queued of 20"
    per_day = _per_day([parse_iso(p.scheduled_time).date() for p in queued])
    assert max(per_day) <= 3, f"a second approve refilled a full day: per_day={per_day}"
    assert per_day == [3] * 6 + [2], f"expected 3/day packing across days: per_day={per_day}"


def _assert_account_spread(cfg, ids, *, min_gap_min: int) -> None:
    """Per-account pairwise-distinct times with consecutive gaps >= min_gap_min."""
    reloaded = Ledger.load(cfg)
    by_account: dict[str, list[datetime]] = {}
    for pid in ids:
        p = reloaded.posts[pid]
        by_account.setdefault(p.account, []).append(parse_iso(p.scheduled_time))
    for handle, dts in by_account.items():
        dts.sort()
        assert len(set(dts)) == len(dts), f"{handle} collided: {[iso_z(d) for d in dts]}"
        gaps_min = [(b - a).total_seconds() / 60.0 for a, b in zip(dts, dts[1:])]
        assert all(g >= min_gap_min for g in gaps_min), (
            f"{handle} gap floor {min_gap_min} violated: gaps_min={gaps_min}; "
            f"times={[iso_z(d) for d in dts]}")


def test_bulk_approve_spread_wins_over_identical_futures(tmp_path, monkeypatch):
    """MOL-869: approve N posts on one account that share the SAME future mint time. Ledger.approve_post
    keeps those futures (lockstep); the post-promote suggest_times_for_batch pass must overwrite so the
    queued set is pairwise-distinct with gaps >= 30m (realistic off)."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    future_iso = iso_z(FIXED_DT + timedelta(hours=12))      # identical futures — keep would lockstep
    ids = []
    for k in range(4):
        pid = f"p_a_{k}"
        led.add_post(Post(id=pid, parent_id=clip.id, account="a", account_id="ia",
                          platform=Platform.instagram, caption="a", state=PostState.awaiting_approval,
                          scheduled_time=future_iso, media_urls=["file:///clip_1_9x16.mp4"],
                          public_url="dryrun://sweep"))
        ids.append(pid)
    led.save()

    assert approve_posts(cfg, ids, now=FIXED_DT).ok is True
    _assert_account_spread(cfg, ids, min_gap_min=MIN_PER_ACCOUNT_GAP_MIN)


def test_bulk_approve_spread_wins_realistic_cadence(tmp_path, monkeypatch):
    """MOL-869: same identical-future lockstep, with FANOPS_REALISTIC_CADENCE on — gaps >= 120m."""
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    monkeypatch.setenv("FANOPS_REALISTIC_CADENCE", "1")
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    led = Ledger.load(cfg)
    clip = _seed_clip(led)
    future_iso = iso_z(FIXED_DT + timedelta(hours=12))
    ids = []
    for k in range(3):
        pid = f"p_a_r_{k}"
        led.add_post(Post(id=pid, parent_id=clip.id, account="a", account_id="ia",
                          platform=Platform.instagram, caption="a", state=PostState.awaiting_approval,
                          scheduled_time=future_iso, media_urls=["file:///clip_1_9x16.mp4"],
                          public_url="dryrun://sweep"))
        ids.append(pid)
    led.save()

    assert approve_posts(cfg, ids, now=FIXED_DT).ok is True
    _assert_account_spread(cfg, ids, min_gap_min=120)
