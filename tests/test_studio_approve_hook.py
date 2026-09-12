# tests/test_studio_approve_hook.py — CREATE
"""Slice 2 of the removed-hook review: the operator's one-click CHOICE.

render_moment is NOT patched. Unit CI has no ffmpeg: a restored hook with a missing source
fails the off-lock pre-warm and leaves posts awaiting. Success-path restore is the real
ffmpeg path (not a SUT fake)."""
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.actions import approve_with_hook, approve_as_is
import pytest


@pytest.fixture(autouse=True)
def _cv_off(monkeypatch):
    # M3d: creative_variation now DEFAULTS ON, but the approve-with-hook MOMENT-restore flow is an OFF-mode
    # feature (when ON, per-surface hooks own the burn and the action refuses). This file tests that OFF flow,
    # so pin OFF; the one test asserting the ON path sets FANOPS_CREATIVE_VARIATION=1 itself (it overrides).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
REMOVED = "made it and lost everything"


def _seed(cfg, *, hook_removed=REMOVED, captions=None, post_state=PostState.awaiting_approval,
          source_path="/s.mp4"):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=source_path, language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped, hook=None, hook_removed=hook_removed))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued, meta_captions=(captions or {"a/instagram": {"caption": "cap"}})))
    led.add_post(Post(id="p_1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption="CAP", state=post_state,
                      scheduled_time=None, public_url="dryrun://p_1"))
    led.save()
    return led


def test_approve_with_hook_warm_failure_aborts_without_approving(tmp_path):
    # Missing source: off-lock pre-warm fails. Must NOT approve, must NOT restore the hook.
    cfg = Config(root=tmp_path)
    _seed(cfg, source_path=str(tmp_path / "missing.mp4"))
    res = approve_with_hook(cfg, "clip_1", now=NOW)
    assert res.ok is False
    assert "retry" in (res.error or "").lower() or "fail" in (res.error or "").lower()
    led = Ledger.load(cfg)
    assert led.posts["p_1"].state is PostState.awaiting_approval
    assert led.moments["mom_1"].hook is None
    assert led.moments["mom_1"].hook_removed == REMOVED


def test_approve_with_hook_no_removed_hook_just_approves(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, hook_removed=None)
    res = approve_with_hook(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["approved"] == 1
    assert Ledger.load(cfg).posts["p_1"].state is PostState.queued


def test_approve_with_hook_does_not_refuse_when_creative_variation_on(tmp_path, monkeypatch):
    # P9: approve_with_hook no longer refuses when FANOPS_CREATIVE_VARIATION=1 — owner-moment restore always runs.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path)
    _seed(cfg, source_path=str(tmp_path / "missing.mp4"))
    res = approve_with_hook(cfg, "clip_1", now=NOW)
    assert res.ok is False
    assert "creative" not in (res.error or "").lower()
    assert Ledger.load(cfg).posts["p_1"].state is PostState.awaiting_approval


def test_approve_with_hook_unknown_clip(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg)
    res = approve_with_hook(cfg, "nope", now=NOW)
    assert res.ok is False and "no such clip" in res.error


def test_approve_as_is_approves_without_restoring(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg)
    res = approve_as_is(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["approved"] == 1 and res.detail["hook"] is False
    led = Ledger.load(cfg)
    assert led.posts["p_1"].state is PostState.queued
    assert led.moments["mom_1"].hook is None
    assert led.moments["mom_1"].hook_removed == REMOVED


def test_approve_as_is_no_awaiting_is_clean_noop(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, post_state=PostState.queued)
    res = approve_as_is(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["approved"] == 0
