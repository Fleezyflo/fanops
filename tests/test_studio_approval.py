# tests/test_studio_approval.py — Studio batch approval actions (checkpoint 1). Mirrors the stitch
# approval spine (test_studio_stitches.py): multi-select, idempotent, never a 500. Posts born
# awaiting_approval are promoted/rejected here; publish_now stays queued-only (the gate).
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Fmt, Moment, MomentState, Post, Platform, PostState, Source
from fanops.studio import actions

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_FUTURE = "2099-01-01T00:00:00Z"


def _c1_lineage(led):
    """Give `c1` the moment/source rows every post below already names. It never had them — harmless while
    the approve guard read `clip is not None and (...)`, which failed OPEN on a missing clip row; the guard
    now asks `Ledger.can_promote`, which fails CLOSED, so a fixture must describe a lineage that could exist."""
    if "src_1" in led.sources: return
    led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_c1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="mom_c1", path="/c/c1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))


def _seed(cfg, pid, state=PostState.awaiting_approval, when=_FUTURE):
    with Ledger.transaction(cfg) as led:
        _c1_lineage(led)
        led.add_post(Post(id=pid, parent_id="c1", account="a", account_id="1",
                          platform=Platform.instagram, caption="fire", state=state, scheduled_time=when, public_url="dryrun://c1"))


def test_approve_posts_only_selected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, "p1"); _seed(cfg, "p2")
    r = actions.approve_posts(cfg, ["p1"], now=_NOW)
    assert r.ok and r.detail["approved"] == 1
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.queued
    assert led.posts["p2"].state is PostState.awaiting_approval


def test_approve_posts_empty_ids_is_ok_noop(tmp_path):
    cfg = Config(root=tmp_path)
    r = actions.approve_posts(cfg, [], now=_NOW)
    assert r.ok and r.detail["approved"] == 0


def test_reject_posts_marks_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, "p1")
    r = actions.reject_posts(cfg, ["p1"])
    assert r.ok and Ledger.load(cfg).posts["p1"].state is PostState.rejected


def test_reject_posts_reports_only_what_it_discarded(tmp_path):
    # MOL-834: banner count must match audit — Ledger.reject_post no-ops on queued/unknown ids,
    # so detail["rejected"] is len(audited_ids), not the offered selection size.
    cfg = Config(root=tmp_path)
    _seed(cfg, "p1", state=PostState.awaiting_approval)
    _seed(cfg, "p2", state=PostState.queued)
    r = actions.reject_posts(cfg, ["p1", "p2", "nope"])
    assert r.ok and r.detail["rejected"] == 1
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.rejected
    assert led.posts["p2"].state is PostState.queued
    r2 = actions.reject_posts(cfg, ["p2", "nope"])
    assert r2.ok and r2.detail["rejected"] == 0


def test_unapprove_post_sends_back_to_review(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, "p1", state=PostState.queued)
    r = actions.unapprove_post(cfg, "p1")
    assert r.ok and Ledger.load(cfg).posts["p1"].state is PostState.awaiting_approval


def test_publish_now_rejects_awaiting_approval(tmp_path):
    # The gate at the publish boundary: an unapproved post cannot be force-published from the UI.
    cfg = Config(root=tmp_path); _seed(cfg, "p1")
    r = actions.publish_now(cfg, "p1", confirmed=True)
    assert not r.ok and "queued" in r.error


# ---- checkpoint 2: Review approval UI (views + routes) ----
import json
from fanops.accounts import Accounts
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _seed_review(cfg, *, state=PostState.awaiting_approval, pid="p1", when=_FUTURE):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/show.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="drop", transcript_excerpt="go", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id="clip_1", account="a", account_id="1",
                          platform=Platform.instagram, caption="EDIT ME", state=state, scheduled_time=when, public_url="dryrun://clip_1"))


def test_review_bucket_holds_awaiting_not_queued(tmp_path):
    # the editable/review bucket is the APPROVE worklist: awaiting_approval posts show; queued (approved)
    # posts have moved on to the Schedule and must NOT appear here.
    cfg = Config(root=tmp_path); _seed_review(cfg, state=PostState.awaiting_approval, pid="p_await")
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p_appr", parent_id="clip_1", account="a", account_id="1",
                          platform=Platform.instagram, caption="approved", state=PostState.queued, scheduled_time=_FUTURE, public_url="dryrun://p_appr"))
    cards = views.review_buckets(Ledger.load(cfg), Accounts.load(cfg), cfg, now=_NOW)
    editable = [c for c in cards if c.bucket == "editable"]
    pids = {s.post_id for c in editable for s in c.surfaces}
    assert "p_await" in pids and "p_appr" not in pids


def test_awaiting_surface_is_editable_never_imminent(tmp_path):
    # an awaiting post with a PAST stagger-time must still be editable and NOT flagged "shipping now"
    # (it is gated — it cannot ship until approved).
    cfg = Config(root=tmp_path); _seed_review(cfg, when="2020-01-01T00:00:00Z")
    card = [c for c in views.review_buckets(Ledger.load(cfg), Accounts.load(cfg), cfg, now=_NOW) if c.bucket == "editable"][0]
    s = card.surfaces[0]
    assert s.editable is True and s.imminent is False


def test_get_review_renders_checkbox_and_approve_button(tmp_path):
    cfg = Config(root=tmp_path); _seed_review(cfg, pid="p1")
    html = _client(cfg).get("/review?account=all").data
    assert b'name="ids"' in html and b'value="p1"' in html
    assert b"Approve selected" in html and b"Reject selected" in html

def _seed_day_lineage(cfg, *, source_day, mint):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/show.mp4", language="en", created_at=source_day))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="drop", transcript_excerpt="go", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", created_at=mint,
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, scheduled_time=_FUTURE, public_url="dryrun://p1"))

def test_get_review_renders_the_cards_own_minting_day_header(tmp_path):
    # content-lifecycle Phase 3 / MOL-801: the editable bucket emits a running day header keyed on the CARD's
    # minting day. The source was ingested a month earlier, so a source-keyed header would read 2026-06-03.
    cfg = Config(root=tmp_path)
    _seed_day_lineage(cfg, source_day="2026-06-03T08:00:00Z", mint="2026-07-02T09:00:00Z")
    html = _client(cfg).get("/review?view=list").data
    assert b'class="day-head">2026-07-02</h4>' in html      # bare: the card minted this day itself
    assert b'class="day-head">2026-06-03' not in html       # the source's ingest day never heads the group

def test_get_review_renders_a_borrowed_day_with_a_visible_marker(tmp_path):
    # a card with no mint stamp borrows the source's ingest day — and the header SAYS SO. Without the marker
    # the operator cannot tell an inferred day from a real one, which is the original bug in miniature.
    from fanops.studio.views_review import SOURCE_DAY_SUFFIX
    cfg = Config(root=tmp_path)
    _seed_day_lineage(cfg, source_day="2026-06-03T08:00:00Z", mint=None)
    html = _client(cfg).get("/review?view=list").data
    assert b'class="day-head">2026-06-03' + SOURCE_DAY_SUFFIX.encode() + b"</h4>" in html
    assert b'class="day-head">2026-06-03</h4>' not in html   # never rendered as if it were the card's own day

def test_review_day_header_re_emits_across_pagination_boundary(tmp_path):
    # content-lifecycle Phase 3 (H8): the editable bucket is day-sorted and the running day-header is emitted
    # per RENDER (ns.day resets each page), so a day SPANNING the 24-card page boundary re-emits its header on
    # page 2. The riskiest Phase-3 surface — previously verified only by code-reading; this locks it live.
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    n_a = views.GRID_PAGE_SIZE + 4                      # day A: 24 cards fill page 1, 4 spill to page 2 (it spans)
    with Ledger.transaction(cfg) as led:                # each day's posts are MINTED that day (the bucket key)
        for day, sid, n in (("2026-06-10T08:00:00Z", "A", n_a), ("2026-06-03T08:00:00Z", "B", 4)):
            led.add_source(Source(id=f"src_{sid}", source_path=f"/v/{sid}.mp4", language="en", created_at=day))
            led.add_moment(Moment(id=f"mom_{sid}", parent_id=f"src_{sid}", content_token="0-7", start=0, end=7,
                                  reason="drop", transcript_excerpt="go", state=MomentState.clipped))
            for i in range(n):
                cid = f"clip_{sid}_{i}"
                led.add_clip(Clip(id=cid, parent_id=f"mom_{sid}", path=f"/c/{cid}.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
                led.add_post(Post(id=f"p_{sid}_{i}", parent_id=cid, account="a", account_id="1", created_at=day,
                                  platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, scheduled_time=_FUTURE, public_url="dryrun://1"))
    p1 = _client(cfg).get("/review?view=list").data
    p2 = _client(cfg).get(f"/review?view=list&offset={views.GRID_PAGE_SIZE}").data
    assert b'class="day-head">2026-06-10' in p1          # day A (newest) heads page 1
    assert b'class="day-head">2026-06-10' in p2          # day A SPANS the boundary -> its header RE-EMITS on page 2
    assert b'class="day-head">2026-06-03' in p2          # day B begins on page 2, below day A's spill


def test_post_approve_route_promotes_and_drops_from_review(tmp_path):
    cfg = Config(root=tmp_path); _seed_review(cfg, pid="p1")
    r = _client(cfg).post("/posts/approve", data={"ids": ["p1"]})
    assert r.status_code == 200
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued
    # the approved post is no longer in the review worklist
    assert b'value="p1"' not in r.data


def test_post_reject_route_marks_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed_review(cfg, pid="p1")
    r = _client(cfg).post("/posts/reject", data={"ids": ["p1"]})
    assert r.status_code == 200 and Ledger.load(cfg).posts["p1"].state is PostState.rejected


def test_post_unapprove_route_sends_back_to_review(tmp_path):
    cfg = Config(root=tmp_path); _seed_review(cfg, state=PostState.queued, pid="p1")
    r = _client(cfg).post("/posts/unapprove/p1")
    assert r.status_code == 200 and Ledger.load(cfg).posts["p1"].state is PostState.awaiting_approval


def test_awaiting_post_is_editable_before_approval(tmp_path):
    # the operator edits/reschedules BEFORE approving — the editable guard must accept awaiting_approval.
    cfg = Config(root=tmp_path); _seed_review(cfg, pid="p1", when=_FUTURE)
    r = actions.reschedule_post(cfg, "p1", "2099-06-06T12:00:00Z", now=_NOW)
    assert r.ok and Ledger.load(cfg).posts["p1"].scheduled_time == "2099-06-06T12:00:00Z"


def test_unapprove_unknown_post_surfaces_error(tmp_path):
    cfg = Config(root=tmp_path)
    r = _client(cfg).post("/posts/unapprove/nope")
    assert r.status_code == 200 and b"no such post" in r.data   # error banner, not a silent clean re-render


def test_snooze_moves_awaiting_post(tmp_path):
    # Review shows awaiting posts, and the Snooze button fires per clip — it must actually move them
    # (not a silent 0-count no-op now that the editable bucket is awaiting_approval).
    cfg = Config(root=tmp_path); _seed_review(cfg, pid="p1", when=_FUTURE)
    r = actions.snooze_clip(cfg, "clip_1", now=_NOW)
    assert r.ok and r.detail["count"] == 1
    assert Ledger.load(cfg).posts["p1"].scheduled_time != _FUTURE


# ---- P1: approve actions pass a per-post strictly-future suggestion (no silent publish-now) ----
def _approval_now():
    # NOW must be AFTER surface_time's anchor base so the suggestion is genuinely future relative to it; we
    # pass now=NOW into approve, and the suggestion is computed from that same now -> always strictly future.
    return datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

def test_approve_posts_untimed_gets_suggestion_not_now(tmp_path):
    from fanops.timeutil import iso_z, parse_iso
    from datetime import timedelta
    cfg = Config(root=tmp_path); now = _approval_now(); now_iso = iso_z(now)
    _seed_review(cfg, pid="p_untimed", when=None)               # born with NO time
    far = iso_z(now + timedelta(hours=9))
    with Ledger.transaction(cfg) as led:                        # a sibling with a still-future operator time
        led.add_post(Post(id="p_future", parent_id="clip_1", account="a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, scheduled_time=far, public_url="dryrun://p_future"))
    r = actions.approve_posts(cfg, ["p_untimed", "p_future"], now=now)
    assert r.ok
    led = Ledger.load(cfg)
    pu = led.posts["p_untimed"]; pf = led.posts["p_future"]
    assert pu.state is PostState.queued and pf.state is PostState.queued
    assert pu.scheduled_time and pf.scheduled_time
    assert parse_iso(pu.scheduled_time) > now and pu.scheduled_time != now_iso   # strictly-future, not now
    assert parse_iso(pf.scheduled_time) > now
    # MOL-869: post-promote respread — same-account batch is pairwise-distinct (keep-future would lockstep)
    assert pu.scheduled_time != pf.scheduled_time
    gap_min = abs((parse_iso(pu.scheduled_time) - parse_iso(pf.scheduled_time)).total_seconds()) / 60.0
    assert gap_min >= 30.0, f"per-account cadence floor violated: gap_min={gap_min}"

def test_approve_with_hook_untimed_gets_suggestion_not_now(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")        # M3d: approve_with_hook is the OFF-mode moment-restore flow
    from fanops.timeutil import iso_z, parse_iso
    cfg = Config(root=tmp_path); now = _approval_now(); now_iso = iso_z(now)
    _seed_review(cfg, pid="p_untimed", when=None)               # clip has NO hook_removed -> clean approve path
    r = actions.approve_with_hook(cfg, "clip_1", now=now)
    assert r.ok
    pu = Ledger.load(cfg).posts["p_untimed"]
    assert pu.state is PostState.queued and parse_iso(pu.scheduled_time) > now and pu.scheduled_time != now_iso

def test_approve_as_is_untimed_gets_suggestion_not_now(tmp_path):
    from fanops.timeutil import iso_z, parse_iso
    cfg = Config(root=tmp_path); now = _approval_now(); now_iso = iso_z(now)
    _seed_review(cfg, pid="p_untimed", when=None)
    r = actions.approve_as_is(cfg, "clip_1", now=now)
    assert r.ok
    pu = Ledger.load(cfg).posts["p_untimed"]
    assert pu.state is PostState.queued and parse_iso(pu.scheduled_time) > now and pu.scheduled_time != now_iso


# ---- M3b: bulk approve at two scopes — all-accounts-of-a-moment + one-account-across-the-video ----
def _seed_two_accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["instagram"], "status": "active"}]}))

def _awaiting(led, pid, *, clip="clip_1", acct="a", aid="1", batch=None, when=_FUTURE):
    led.add_post(Post(id=pid, parent_id=clip, account=acct, account_id=aid, platform=Platform.instagram,
                      caption="x", state=PostState.awaiting_approval, scheduled_time=when, batch_id=batch, public_url="dryrun://sweep"))

def _seed_review_lineage(cfg):     # two clips on one moment so the route tests render real cards
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        for cid in ("clip_1", "clip_2"):
            led.add_clip(Clip(id=cid, parent_id="mom_1", path=f"/c/{cid}.mp4", aspect=Fmt.r9x16, state=ClipState.queued))

def test_approve_clip_approves_all_surfaces_of_one_moment(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="b", aid="2")
        _awaiting(led, "p_other", clip="clip_2", acct="a", aid="1")
    r = actions.approve_clip(cfg, "clip_1", now=_NOW)
    assert r.ok and r.detail["approved"] == 2 and r.detail["clip_id"] == "clip_1"   # detail carries the scope
    led = Ledger.load(cfg)
    assert led.posts["p_a"].state is PostState.queued and led.posts["p_b"].state is PostState.queued
    assert led.posts["p_other"].state is PostState.awaiting_approval   # a DIFFERENT moment is untouched

def test_approve_clip_noop_when_no_awaiting(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg)
    r = actions.approve_clip(cfg, "clip_nope", now=_NOW)
    assert r.ok and r.detail["approved"] == 0

def test_approve_account_approves_one_account_across_clips(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a1", clip="clip_1", acct="a", aid="1")
        _awaiting(led, "p_a2", clip="clip_2", acct="a", aid="1")
        _awaiting(led, "p_b1", clip="clip_1", acct="b", aid="2")
    r = actions.approve_account(cfg, "a", now=_NOW)
    assert r.ok and r.detail["approved"] == 2
    led = Ledger.load(cfg)
    assert led.posts["p_a1"].state is PostState.queued and led.posts["p_a2"].state is PostState.queued
    assert led.posts["p_b1"].state is PostState.awaiting_approval     # a DIFFERENT account is untouched

def test_approve_account_scoped_to_batch(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_b1", clip="clip_1", acct="a", aid="1", batch="B1")
        _awaiting(led, "p_b2", clip="clip_2", acct="a", aid="1", batch="B2")
    r = actions.approve_account(cfg, "a", batch="B1", now=_NOW)
    assert r.ok and r.detail["approved"] == 1
    led = Ledger.load(cfg)
    assert led.posts["p_b1"].state is PostState.queued
    assert led.posts["p_b2"].state is PostState.awaiting_approval     # the OTHER batch is untouched

def test_approve_account_blank_handle_is_noop(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg)
    r = actions.approve_account(cfg, "", now=_NOW)
    assert r.ok and r.detail["approved"] == 0                          # no target -> clean no-op, never a 500

def test_approve_account_untimed_gets_suggestion_not_now(tmp_path):
    from fanops.timeutil import iso_z, parse_iso
    cfg = Config(root=tmp_path); now = _approval_now(); now_iso = iso_z(now); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_u", acct="a", aid="1", when=None)
    r = actions.approve_account(cfg, "a", now=now)
    assert r.ok
    pu = Ledger.load(cfg).posts["p_u"]
    assert pu.state is PostState.queued and parse_iso(pu.scheduled_time) > now and pu.scheduled_time != now_iso

def test_post_approve_clip_route_approves_all_accounts(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="b", aid="2")
    r = _client(cfg).post("/posts/approve-clip/clip_1")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p_a"].state is PostState.queued and led.posts["p_b"].state is PostState.queued

def test_post_approve_account_route_scopes_to_filter(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a1", clip="clip_1", acct="a", aid="1")
        _awaiting(led, "p_a2", clip="clip_2", acct="a", aid="1")
        _awaiting(led, "p_b1", clip="clip_1", acct="b", aid="2")
    r = _client(cfg).post("/posts/approve-account?account=@a")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p_a1"].state is PostState.queued and led.posts["p_a2"].state is PostState.queued
    assert led.posts["p_b1"].state is PostState.awaiting_approval     # the @b surface is NOT in scope

def test_review_renders_bulk_approve_buttons(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="b", aid="2")
    # U6: bare /review is switcher-only; legacy moment cards + approve-clip live on account=all.
    html = _client(cfg).get("/review?account=all&view=list").data
    assert b"approve-clip/clip_1" in html                              # per-card "approve all accounts of this moment"
    # per-account feed: source-level select + composite approve-with-edits (not legacy approve-account)
    html_a = _client(cfg).get("/review?account=@a").data
    assert b"select-source" in html_a and b"approve-with-edits" in html_a


# ---- M3c: compact list mode — a dense, video-less worklist for scanning rich per-account sets ----
def test_compact_view_omits_video_players(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="b", aid="2")
    full = _client(cfg).get("/review?account=all").data
    compact = _client(cfg).get("/review?compact=1").data
    assert b"<video" in full                       # the default view shows the per-account video switcher
    assert b"<video" not in compact                # compact drops the heavy players for a scannable list

def test_compact_view_keeps_bulk_approve(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="b", aid="2")
    html = _client(cfg).get("/review?view=list&compact=1").data
    assert b'name="ids"' in html and b"Approve selected" in html       # bulk approve still works in compact
    assert b"approve-clip/clip_1" in html                              # per-card approve-all still present
    assert b'value="p_a"' in html and b"a" in html and b"b" in html  # every surface is still listed + selectable

def test_compact_action_urls_carry_compact(tmp_path):
    # the mode must PERSIST: action/pagination URLs carry compact=1 so a click doesn't bounce back to full.
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="a", aid="1")
    html = _client(cfg).get("/review?compact=1").data
    assert b"compact=1" in html                                        # carried into the body's action URLs

def test_compact_persists_across_approve_rerender(tmp_path):
    # the htmx re-render after an approve stays compact (the action URL carries compact -> _review_panel reads it)
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="a", aid="1")
        _awaiting(led, "p_b", clip="clip_2", acct="a", aid="1")       # a 2nd card survives after approving clip_1
    r = _client(cfg).post("/posts/approve-clip/clip_1?compact=1")
    assert r.status_code == 200 and b"<video" not in r.data           # the re-render stayed compact

def test_compact_toggle_links_both_ways(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="a", aid="1")
    full = _client(cfg).get("/review?account=all").data
    compact = _client(cfg).get("/review?compact=1").data
    assert b"compact=1" in full and b"Compact" in full                # the full view offers a way INTO compact
    assert b"Full" in compact                                         # the compact view offers a way back to full


# ---- M3d: creative_variation default-ON hides the OFF-mode removed-hook restore choice ----
def _seed_removed_hook_review(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/show.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="drop", state=MomentState.clipped, hook_removed="a stripped hook"))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, scheduled_time=_FUTURE, public_url="dryrun://p1"))

def test_review_shows_hook_choice_when_hook_removed(tmp_path):
    # P9: creative_variation is no longer a runtime flag — the moment-hook RESTORE choice shows whenever
    # hook_removed is set (the OFF-mode approve_with_hook flow).
    cfg = Config(root=tmp_path); _seed_removed_hook_review(cfg)
    html = _client(cfg).get("/review?view=list").data
    assert b"Approve with hook" in html and b"hook removed" in html


def test_review_hides_hook_choice_when_creative_variation_on(tmp_path, monkeypatch):
    # Legacy name kept: FANOPS_CREATIVE_VARIATION no longer gates the template (golive hardcodes OFF), so the
    # restore choice remains visible when hook_removed is set.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed_removed_hook_review(cfg)
    html = _client(cfg).get("/review?view=list").data
    assert b"Approve with hook" in html and b"hook removed" in html


def test_approve_posts_large_batch_requires_confirm(tmp_path):
    from fanops.studio.actions_approve import BULK_APPROVE_CONFIRM_AT
    cfg = Config(root=tmp_path)
    ids = [f"p{i}" for i in range(BULK_APPROVE_CONFIRM_AT + 1)]
    with Ledger.transaction(cfg) as led:
        _c1_lineage(led)
        for i, pid in enumerate(ids):
            led.add_post(Post(id=pid, parent_id="c1", account="a", account_id="x",
                              platform=Platform.instagram, caption="c", state=PostState.awaiting_approval))
    res = actions.approve_posts(cfg, ids, confirmed=False)
    assert not res.ok and "approved" in (res.error or "").lower()
    res2 = actions.approve_posts(cfg, ids, confirmed=True)
    assert res2.ok and res2.detail["approved"] == len(ids)


# ---- MOL-797: the platform-cap drop in bulk approve is COUNTED and SHOWN, never silent ----
# _approve_ids_with_render refuses a post whose realized cut exceeds PLATFORM_MAX_SECONDS. The refusal is
# right — the platform would reject the upload — but it used to `continue` with no counter and no detail
# key, so ticking N came back "Approved N-1" with nothing accounting for the missing one. These lock the
# REPORTING; the cap's value, its policy and when it fires are deliberately untouched.
_OVER_IG_CAP_S = 120.0                 # Instagram's cap is 90s (models.PLATFORM_MAX_SECONDS)
_CAP_COPY = "longer than the platform allows"

def _seed_cap_lineage(cfg, *, hook_removed=None):
    """clip_fits: no cut_seconds -> the 7s moment envelope, under every cap. clip_long: a realized 120s cut."""
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped, hook_removed=hook_removed))
        led.add_clip(Clip(id="clip_fits", parent_id="mom_1", path="/c/clip_fits.mp4", aspect=Fmt.r9x16,
                          state=ClipState.queued))
        led.add_clip(Clip(id="clip_long", parent_id="mom_1", path="/c/clip_long.mp4", aspect=Fmt.r9x16,
                          state=ClipState.queued, cut_seconds=_OVER_IG_CAP_S))

def test_approve_counts_the_post_the_platform_cap_dropped(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_fits", clip="clip_fits", acct="a", aid="1")
        _awaiting(led, "p_long", clip="clip_long", acct="a", aid="1")
    r = actions.approve_posts(cfg, ["p_fits", "p_long"], now=_NOW)
    assert r.ok and r.detail["approved"] == 1 and r.detail["cut_over_cap"] == 1
    led = Ledger.load(cfg)
    assert led.posts["p_fits"].state is PostState.queued
    assert led.posts["p_long"].state is PostState.awaiting_approval   # the cap's BEHAVIOUR is unchanged

def test_approve_reports_zero_dropped_when_nothing_is_over_cap(tmp_path):
    # negative control: the counter must not fire on an ordinary approve, or the banner cries wolf forever.
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_fits", clip="clip_fits", acct="a", aid="1")
    r = actions.approve_posts(cfg, ["p_fits"], now=_NOW)
    assert r.ok and r.detail["approved"] == 1 and r.detail["cut_over_cap"] == 0

def test_approve_route_tells_the_operator_the_cap_dropped_one(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_fits", clip="clip_fits", acct="a", aid="1")
        _awaiting(led, "p_long", clip="clip_long", acct="a", aid="1")
    html = _client(cfg).post("/posts/approve", data={"ids": ["p_fits", "p_long"]}).data.decode()
    assert "Approved 1" in html and "1 skipped" in html and _CAP_COPY in html

def test_approve_route_still_reports_a_drop_that_took_the_whole_tick(tmp_path):
    """The branch that would otherwise stay silent: `approved_scheduled` is only set when >=1 post promoted,
    so an all-dropped tick falls to the plain `approved is defined` copy — which read 'Approved 0 — on
    schedule.' and named nothing at all."""
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_long", clip="clip_long", acct="a", aid="1")
    html = _client(cfg).post("/posts/approve", data={"ids": ["p_long"]}).data.decode()
    assert "1 skipped" in html and _CAP_COPY in html

def test_approve_route_says_nothing_about_a_cap_when_nothing_was_dropped(tmp_path):
    # the negative control at the SURFACE: a clean approve renders no skip clause (a clause that always
    # renders proves nothing about the one that matters).
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_fits", clip="clip_fits", acct="a", aid="1")
    html = _client(cfg).post("/posts/approve", data={"ids": ["p_fits"]}).data.decode()
    assert "Approved 1" in html and _CAP_COPY not in html


# ---- MOL-832: the cap is ONE predicate, and EVERY approve route asks it ----
# `approve_with_hook` guarded retired lineage and then called `led.approve_post` with no cap check at all,
# so the same post on the same ledger was admissible or not purely by which button the operator pressed:
# /posts/approve refused it, /posts/approve-with-hook queued it. Both routes now go through
# `_over_cap_refusal`, the sole owner. These tests run the SAME fixture through both — a single-route test
# is exactly what let the two drift apart. The cap's value, policy and trigger stay untouched (MOL-797).
def _via_bulk(cfg, clip):        return actions.approve_posts(cfg, ["p_cap"], now=_NOW)
def _via_hook(cfg, clip):        return actions.approve_with_hook(cfg, clip, now=_NOW)
_ROUTES = pytest.mark.parametrize("route", [_via_bulk, _via_hook], ids=["bulk", "with_hook"])

@_ROUTES
def test_every_approve_route_refuses_the_same_over_cap_post(tmp_path, route):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_cap", clip="clip_long", acct="a", aid="1")
    r = route(cfg, "clip_long")
    assert r.ok and r.detail["approved"] == 0 and r.detail["cut_over_cap"] == 1
    assert Ledger.load(cfg).posts["p_cap"].state is PostState.awaiting_approval

@_ROUTES
def test_every_approve_route_still_admits_the_same_under_cap_post(tmp_path, route):
    # the negative control the parity test needs: a route that refused everything would pass the test above.
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_cap", clip="clip_fits", acct="a", aid="1")
    r = route(cfg, "clip_fits")
    assert r.ok and r.detail["approved"] == 1 and r.detail["cut_over_cap"] == 0
    assert Ledger.load(cfg).posts["p_cap"].state is PostState.queued


def _fake_burn(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
    """render_moment stand-in (the action imports it locally, so patch `fanops.clip.render_moment`) — no
    ffmpeg, and a clean rendered clip so the hook restore proceeds instead of rolling back."""
    c = next(c for c in led.clips.values() if c.parent_id == moment_id and c.aspect is aspect)
    return led, c.model_copy(update={"state": ClipState.rendered, "hook_burn_failed": False})

def test_approve_with_hook_route_tells_the_operator_the_cap_dropped_one(tmp_path, mocker):
    # the banner branch MOL-797 could not reach: a with-hook result renders `detail.hook` copy, which named
    # no drop at all — so this button could refuse the cut and report only "Approved 0 with hook restored".
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg, hook_removed="lost it all")
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_cap", clip="clip_long", acct="a", aid="1")
    mocker.patch("fanops.clip.render_moment", side_effect=_fake_burn)
    html = _client(cfg).post("/posts/approve-with-hook/clip_long").data.decode()
    assert "1 skipped" in html and _CAP_COPY in html

def test_approve_with_hook_route_says_nothing_about_a_cap_when_nothing_was_dropped(tmp_path, mocker):
    # the same negative control at the with-hook surface.
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg, hook_removed="lost it all")
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_cap", clip="clip_fits", acct="a", aid="1")
    mocker.patch("fanops.clip.render_moment", side_effect=_fake_burn)
    html = _client(cfg).post("/posts/approve-with-hook/clip_fits").data.decode()
    assert "Approved 1 with hook restored" in html and _CAP_COPY not in html

def test_approve_with_hook_does_not_spend_the_removed_hook_on_a_fully_dropped_clip(tmp_path, mocker):
    # the cap is asked BEFORE the restore: a clip whose every post is over cap must not re-cut, and must
    # keep `hook_removed` intact so the operator can still act on it after the cut is fixed.
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_cap_lineage(cfg, hook_removed="lost it all")
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_cap", clip="clip_long", acct="a", aid="1")
    burn = mocker.patch("fanops.clip.render_moment", side_effect=_fake_burn)
    r = actions.approve_with_hook(cfg, "clip_long", now=_NOW)
    assert r.ok and r.detail["approved"] == 0 and r.detail["cut_over_cap"] == 1
    assert burn.call_count == 1          # the off-lock pre-warm only — no in-transaction re-cut
    led = Ledger.load(cfg)
    assert led.moments["mom_1"].hook_removed == "lost it all" and led.moments["mom_1"].hook is None
