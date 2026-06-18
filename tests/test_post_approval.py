# tests/test_post_approval.py — Post approval gate (checkpoint 1 of the post-approval-lifecycle plan).
# Posts are born `awaiting_approval`; NOTHING publishes until the operator promotes them to `queued`.
# These tests pin the gate's safety contract: publish_due never fires an unapproved post, and the
# ledger transitions are in-lock guarded (a wrong-state call is a clean no-op).
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState, Clip, ClipState
from fanops.timeutil import iso_z

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_PAST = "2020-01-01T00:00:00Z"
_FUTURE = "2099-01-01T00:00:00Z"


def _post(pid="p1", state=PostState.awaiting_approval, when=_PAST):
    return Post(id=pid, parent_id="c1", account="@a", account_id="1",
                platform=Platform.instagram, caption="fire", state=state, scheduled_time=when)


# ---- Task 1: enum members ----
def test_poststate_has_awaiting_approval_and_rejected():
    assert PostState.awaiting_approval.value == "awaiting_approval"
    assert PostState.rejected.value == "rejected"


# ---- Task 3: ledger transitions ----
def test_approve_post_promotes_awaiting_to_queued(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(when=_FUTURE))
        led.approve_post("p1", now_iso=iso_z(_NOW))
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued


def test_approve_post_keeps_future_schedule(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(when=_FUTURE))
        led.approve_post("p1", now_iso=iso_z(_NOW))
    assert Ledger.load(cfg).posts["p1"].scheduled_time == _FUTURE


def test_approve_post_bumps_stale_schedule_to_now(tmp_path):
    # safety: a past stagger-time must NOT machine-gun a backlog onto a live backend at approval.
    cfg = Config(root=tmp_path)
    now_iso = iso_z(_NOW)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(when=_PAST))
        led.approve_post("p1", now_iso=now_iso)
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.queued and p.scheduled_time == now_iso


def test_approve_post_none_schedule_set_to_now(tmp_path):
    # a post with no schedule (None) is due immediately on approval -> stamp now, never leave it unscheduled.
    cfg = Config(root=tmp_path)
    now_iso = iso_z(_NOW)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(when=None))
        led.approve_post("p1", now_iso=now_iso)
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.queued and p.scheduled_time == now_iso


def test_approve_post_naive_future_schedule_preserved(tmp_path):
    # a hand-edited tz-naive FUTURE time must NOT be silently zeroed to now (read as UTC, preserved).
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(when="2099-01-01T00:00:00"))   # naive (no Z)
        led.approve_post("p1", now_iso=iso_z(_NOW))
    assert Ledger.load(cfg).posts["p1"].scheduled_time == "2099-01-01T00:00:00"


def test_approve_post_wrong_state_is_noop(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(state=PostState.published, when=_FUTURE))
        led.approve_post("p1", now_iso=iso_z(_NOW))
    assert Ledger.load(cfg).posts["p1"].state is PostState.published


def test_reject_post_marks_rejected(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post())
        led.reject_post("p1")
    assert Ledger.load(cfg).posts["p1"].state is PostState.rejected


def test_reject_post_only_from_awaiting(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(state=PostState.queued))
        led.reject_post("p1")
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued   # not awaiting -> no-op


def test_unapprove_post_returns_queued_to_awaiting(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(state=PostState.queued, when=_FUTURE))
        led.unapprove_post("p1")
    assert Ledger.load(cfg).posts["p1"].state is PostState.awaiting_approval


def test_unapprove_post_only_from_queued(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(_post(state=PostState.published, when=_FUTURE))
        led.unapprove_post("p1")
    assert Ledger.load(cfg).posts["p1"].state is PostState.published   # terminal -> no-op


# ---- Task 4: the publish gate ----
def test_publish_due_ignores_awaiting_approval(tmp_path):
    # The whole point: a due-by-schedule but UNAPPROVED post must never be submitted.
    from fanops.post.run import publish_due
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m1", path=str(cfg.clips / "c1.mp4"), state=ClipState.queued))
        led.add_post(_post(when=_PAST))               # past schedule, awaiting approval
    led = publish_due(Ledger.load(cfg), cfg, now=iso_z(_NOW))
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.awaiting_approval      # untouched
    assert not (cfg.scheduled / "p1.json").exists()    # dryrun wrote nothing


def test_publish_due_fires_approved_queued(tmp_path):
    # Control: once approved (queued) and due, the same post DOES publish. publish_due upgrades the
    # dryrun poster's `submitted` to `published` in the same pass (run.py), so the terminal state is published.
    from fanops.post.run import publish_due
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m1", path=str(cfg.clips / "c1.mp4"), state=ClipState.queued))
        led.add_post(_post(state=PostState.queued, when=_PAST))
    publish_due(Ledger.load(cfg), cfg, now=iso_z(_NOW))
    assert Ledger.load(cfg).posts["p1"].state is PostState.published
