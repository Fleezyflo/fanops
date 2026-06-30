# tests/test_studio_approval.py — Studio batch approval actions (checkpoint 1). Mirrors the stitch
# approval spine (test_studio_stitches.py): multi-select, idempotent, never a 500. Posts born
# awaiting_approval are promoted/rejected here; publish_now stays queued-only (the gate).
import pytest
pytest.importorskip("flask")
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.studio import actions

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_FUTURE = "2099-01-01T00:00:00Z"


def _seed(cfg, pid, state=PostState.awaiting_approval, when=_FUTURE):
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id=pid, parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="fire", state=state, scheduled_time=when, public_url=f"dryrun://c1"))


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
from fanops.models import Clip, ClipState, Source, Moment, MomentState, Fmt
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
        led.add_post(Post(id=pid, parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="EDIT ME", state=state, scheduled_time=when, public_url=f"dryrun://clip_1"))


def test_review_bucket_holds_awaiting_not_queued(tmp_path):
    # the editable/review bucket is the APPROVE worklist: awaiting_approval posts show; queued (approved)
    # posts have moved on to the Schedule and must NOT appear here.
    cfg = Config(root=tmp_path); _seed_review(cfg, state=PostState.awaiting_approval, pid="p_await")
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p_appr", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="approved", state=PostState.queued, scheduled_time=_FUTURE, public_url=f"dryrun://p_appr"))
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
    html = _client(cfg).get("/review").data
    assert b'name="ids"' in html and b'value="p1"' in html
    assert b"Approve selected" in html and b"Reject selected" in html

def test_get_review_renders_ingest_day_header(tmp_path):
    # content-lifecycle Phase 3: the editable bucket emits a running ingest-day header (source.created_at).
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/show.mp4", language="en", created_at="2026-06-03T08:00:00Z"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="drop", transcript_excerpt="go", state=MomentState.clipped))
        led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c/clip_1.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, scheduled_time=_FUTURE, public_url=f"dryrun://p1"))
    html = _client(cfg).get("/review?view=list").data
    assert b'class="day-head">2026-06-03' in html

def test_review_day_header_re_emits_across_pagination_boundary(tmp_path):
    # content-lifecycle Phase 3 (H8): the editable bucket is day-sorted and the running day-header is emitted
    # per RENDER (ns.day resets each page), so a day SPANNING the 24-card page boundary re-emits its header on
    # page 2. The riskiest Phase-3 surface — previously verified only by code-reading; this locks it live.
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    n_a = views.GRID_PAGE_SIZE + 4                      # day A: 24 cards fill page 1, 4 spill to page 2 (it spans)
    with Ledger.transaction(cfg) as led:
        for day, sid, n in (("2026-06-10T08:00:00Z", "A", n_a), ("2026-06-03T08:00:00Z", "B", 4)):
            led.add_source(Source(id=f"src_{sid}", source_path=f"/v/{sid}.mp4", language="en", created_at=day))
            led.add_moment(Moment(id=f"mom_{sid}", parent_id=f"src_{sid}", content_token="0-7", start=0, end=7,
                                  reason="drop", transcript_excerpt="go", state=MomentState.clipped))
            for i in range(n):
                cid = f"clip_{sid}_{i}"
                led.add_clip(Clip(id=cid, parent_id=f"mom_{sid}", path=f"/c/{cid}.mp4", aspect=Fmt.r9x16, state=ClipState.queued))
                led.add_post(Post(id=f"p_{sid}_{i}", parent_id=cid, account="@a", account_id="1",
                                  platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, scheduled_time=_FUTURE, public_url=f"dryrun://1"))
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
        led.add_post(Post(id="p_future", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, scheduled_time=far, public_url=f"dryrun://p_future"))
    r = actions.approve_posts(cfg, ["p_untimed", "p_future"], now=now)
    assert r.ok
    led = Ledger.load(cfg)
    pu = led.posts["p_untimed"]
    assert pu.state is PostState.queued and pu.scheduled_time is not None
    assert parse_iso(pu.scheduled_time) > now and pu.scheduled_time != now_iso   # a strictly-future suggestion, not now
    assert led.posts["p_future"].scheduled_time == far          # operator's future time preserved across the batch

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

def _awaiting(led, pid, *, clip="clip_1", acct="@a", aid="1", batch=None, when=_FUTURE):
    led.add_post(Post(id=pid, parent_id=clip, account=acct, account_id=aid, platform=Platform.instagram,
                      caption="x", state=PostState.awaiting_approval, scheduled_time=when, batch_id=batch, public_url=f"dryrun://sweep"))

def _seed_review_lineage(cfg):     # two clips on one moment so the route tests render real cards
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_1", source_path="/v/s.mp4", language="en"))
        led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", state=MomentState.clipped))
        for cid in ("clip_1", "clip_2"):
            led.add_clip(Clip(id=cid, parent_id="mom_1", path=f"/c/{cid}.mp4", aspect=Fmt.r9x16, state=ClipState.queued))

def test_approve_clip_approves_all_surfaces_of_one_moment(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="@a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="@b", aid="2")
        _awaiting(led, "p_other", clip="clip_2", acct="@a", aid="1")
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
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a1", clip="clip_1", acct="@a", aid="1")
        _awaiting(led, "p_a2", clip="clip_2", acct="@a", aid="1")
        _awaiting(led, "p_b1", clip="clip_1", acct="@b", aid="2")
    r = actions.approve_account(cfg, "@a", now=_NOW)
    assert r.ok and r.detail["approved"] == 2
    led = Ledger.load(cfg)
    assert led.posts["p_a1"].state is PostState.queued and led.posts["p_a2"].state is PostState.queued
    assert led.posts["p_b1"].state is PostState.awaiting_approval     # a DIFFERENT account is untouched

def test_approve_account_scoped_to_batch(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_b1", clip="clip_1", acct="@a", aid="1", batch="B1")
        _awaiting(led, "p_b2", clip="clip_2", acct="@a", aid="1", batch="B2")
    r = actions.approve_account(cfg, "@a", batch="B1", now=_NOW)
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
    cfg = Config(root=tmp_path); now = _approval_now(); now_iso = iso_z(now); _seed_two_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_u", acct="@a", aid="1", when=None)
    r = actions.approve_account(cfg, "@a", now=now)
    assert r.ok
    pu = Ledger.load(cfg).posts["p_u"]
    assert pu.state is PostState.queued and parse_iso(pu.scheduled_time) > now and pu.scheduled_time != now_iso

def test_post_approve_clip_route_approves_all_accounts(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="@a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="@b", aid="2")
    r = _client(cfg).post("/posts/approve-clip/clip_1")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p_a"].state is PostState.queued and led.posts["p_b"].state is PostState.queued

def test_post_approve_account_route_scopes_to_filter(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a1", clip="clip_1", acct="@a", aid="1")
        _awaiting(led, "p_a2", clip="clip_2", acct="@a", aid="1")
        _awaiting(led, "p_b1", clip="clip_1", acct="@b", aid="2")
    r = _client(cfg).post("/posts/approve-account?account=@a")
    assert r.status_code == 200
    led = Ledger.load(cfg)
    assert led.posts["p_a1"].state is PostState.queued and led.posts["p_a2"].state is PostState.queued
    assert led.posts["p_b1"].state is PostState.awaiting_approval     # the @b surface is NOT in scope

def test_review_renders_bulk_approve_buttons(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="@a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="@b", aid="2")
    html = _client(cfg).get("/review?view=list").data
    assert b"approve-clip/clip_1" in html                              # per-card "approve all accounts of this moment"
    # the one-account-across-the-video button appears only when an account filter is active
    html_a = _client(cfg).get("/review?view=list&account=@a").data
    assert b"approve-account" in html_a and b"Approve all @a" in html_a


# ---- M3c: compact list mode — a dense, video-less worklist for scanning rich per-account sets ----
def test_compact_view_omits_video_players(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="@a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="@b", aid="2")
    full = _client(cfg).get("/review").data
    compact = _client(cfg).get("/review?compact=1").data
    assert b"<video" in full                       # the default view shows the per-account video switcher
    assert b"<video" not in compact                # compact drops the heavy players for a scannable list

def test_compact_view_keeps_bulk_approve(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="@a", aid="1")
        _awaiting(led, "p_b", clip="clip_1", acct="@b", aid="2")
    html = _client(cfg).get("/review?view=list&compact=1").data
    assert b'name="ids"' in html and b"Approve selected" in html       # bulk approve still works in compact
    assert b"approve-clip/clip_1" in html                              # per-card approve-all still present
    assert b'value="p_a"' in html and b"@a" in html and b"@b" in html  # every surface is still listed + selectable

def test_compact_action_urls_carry_compact(tmp_path):
    # the mode must PERSIST: action/pagination URLs carry compact=1 so a click doesn't bounce back to full.
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="@a", aid="1")
    html = _client(cfg).get("/review?compact=1").data
    assert b"compact=1" in html                                        # carried into the body's action URLs

def test_compact_persists_across_approve_rerender(tmp_path):
    # the htmx re-render after an approve stays compact (the action URL carries compact -> _review_panel reads it)
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="@a", aid="1")
        _awaiting(led, "p_b", clip="clip_2", acct="@a", aid="1")       # a 2nd card survives after approving clip_1
    r = _client(cfg).post("/posts/approve-clip/clip_1?compact=1")
    assert r.status_code == 200 and b"<video" not in r.data           # the re-render stayed compact

def test_compact_toggle_links_both_ways(tmp_path):
    cfg = Config(root=tmp_path); _seed_two_accounts(cfg); _seed_review_lineage(cfg)
    with Ledger.transaction(cfg) as led:
        _awaiting(led, "p_a", clip="clip_1", acct="@a", aid="1")
    full = _client(cfg).get("/review").data
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
        led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.awaiting_approval, scheduled_time=_FUTURE, public_url=f"dryrun://p1"))

def test_review_hides_hook_choice_when_creative_variation_on(tmp_path, monkeypatch):
    # default ON: per-surface hooks own the burn + approve_with_hook refuses, so the moment-restore choice
    # is HIDDEN; the generic 'Approve all accounts' is the approve path instead.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed_removed_hook_review(cfg)
    html = _client(cfg).get("/review?view=list").data
    assert b"Approve with hook" not in html and b"hook removed" not in html
    assert b"Approve all accounts" in html

def test_review_shows_hook_choice_when_creative_variation_off(tmp_path, monkeypatch):
    # pinned OFF: the shared clip ships clean with the stripped hook, so the restore badge + choice show.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    cfg = Config(root=tmp_path); _seed_removed_hook_review(cfg)
    html = _client(cfg).get("/review?view=list").data
    assert b"Approve with hook" in html and b"hook removed" in html


def test_approve_posts_large_batch_requires_confirm(tmp_path):
    from fanops.studio.actions_approve import BULK_APPROVE_CONFIRM_AT
    cfg = Config(root=tmp_path)
    ids = [f"p{i}" for i in range(BULK_APPROVE_CONFIRM_AT + 1)]
    with Ledger.transaction(cfg) as led:
        for i, pid in enumerate(ids):
            led.add_post(Post(id=pid, parent_id="c1", account="@a", account_id="x",
                              platform=Platform.instagram, caption="c", state=PostState.awaiting_approval))
    res = actions.approve_posts(cfg, ids, confirmed=False)
    assert not res.ok and "approved" in (res.error or "").lower()
    res2 = actions.approve_posts(cfg, ids, confirmed=True)
    assert res2.ok and res2.detail["approved"] == len(ids)
