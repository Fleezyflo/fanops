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
