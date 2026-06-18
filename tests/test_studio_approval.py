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
                          platform=Platform.instagram, caption="fire", state=state, scheduled_time=when))


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
                          platform=Platform.instagram, caption="EDIT ME", state=state, scheduled_time=when))


def test_review_bucket_holds_awaiting_not_queued(tmp_path):
    # the editable/review bucket is the APPROVE worklist: awaiting_approval posts show; queued (approved)
    # posts have moved on to the Schedule and must NOT appear here.
    cfg = Config(root=tmp_path); _seed_review(cfg, state=PostState.awaiting_approval, pid="p_await")
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p_appr", parent_id="clip_1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="approved", state=PostState.queued, scheduled_time=_FUTURE))
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
