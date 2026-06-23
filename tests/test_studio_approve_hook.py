# tests/test_studio_approve_hook.py — CREATE
"""Slice 2 of the removed-hook review: the operator's one-click CHOICE. A clip whose model-written hook
was auto-stripped (Moment.hook_removed set, slice 1) can be approved two ways:
  • approve_with_hook  — RESTORE moment.hook from hook_removed, RE-RENDER so it burns into the mp4
                         (preserving the clip's state + per-surface captions), then approve every
                         awaiting post of the clip.
  • approve_as_is      — approve every awaiting post WITHOUT restoring the hook (ship clean).
render_moment is patched at fanops.clip.render_moment (the action imports it locally, house style) so no
ffmpeg runs; the fake mimics render_moment's real reset (state->rendered, meta_captions wiped) so the
state/captions RESTORE is genuinely exercised."""
import pytest
from datetime import datetime, timezone
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, Post, Platform, PostState, ClipState, MomentState, Fmt
from fanops.studio.actions import approve_with_hook, approve_as_is


@pytest.fixture(autouse=True)
def _cv_off(monkeypatch):
    # M3d: creative_variation now DEFAULTS ON, but the approve-with-hook MOMENT-restore flow is an OFF-mode
    # feature (when ON, per-surface hooks own the burn and the action refuses). This file tests that OFF flow,
    # so pin OFF; the one test asserting the ON refusal sets FANOPS_CREATIVE_VARIATION=1 itself (it overrides).
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
REMOVED = "made it and lost everything"


def _seed(cfg, *, hook_removed=REMOVED, captions=None, post_state=PostState.awaiting_approval):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped, hook=None, hook_removed=hook_removed))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued, meta_captions=(captions or {"@a/instagram": {"caption": "cap"}})))
    led.add_post(Post(id="p_1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="CAP", state=post_state,
                      scheduled_time=None))
    led.save()
    return led


def _fake_render(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
    # mimic render_moment's SUCCESS: overwrite the clip record fresh (state reset, captions wiped) so the
    # action's state/meta_captions RESTORE is what keeps them — exactly the real reconstruct behaviour.
    c = next(c for c in led.clips.values() if c.parent_id == moment_id and c.aspect is aspect)
    new = c.model_copy(update={"state": ClipState.rendered, "meta_captions": {}, "hook_burn_failed": False})
    led.clips[c.id] = new
    return led, new


def _fake_render_err(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
    c = next(c for c in led.clips.values() if c.parent_id == moment_id and c.aspect is aspect)
    err = c.model_copy(update={"state": ClipState.error, "error_reason": "ffmpeg boom"})
    led.clips[c.id] = err
    return led, err


def test_approve_with_hook_restores_renders_and_approves(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg)
    seen = {}
    def _capture(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
        seen["hook_at_render"] = led.moments[moment_id].hook    # the hook must be set BEFORE the burn
        return _fake_render(led, cfg, moment_id, aspect=aspect, **kw)
    mocker.patch("fanops.clip.render_moment", side_effect=_capture)
    res = approve_with_hook(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["approved"] == 1 and res.detail["hook"] is True
    led = Ledger.load(cfg)
    assert led.moments["mom_1"].hook == REMOVED                 # restored
    assert led.moments["mom_1"].hook_removed is None            # cleared once live
    assert led.posts["p_1"].state is PostState.queued           # approved
    assert seen["hook_at_render"] == REMOVED                    # burned with the restored hook
    assert led.clips["clip_1"].meta_captions == {"@a/instagram": {"caption": "cap"}}  # captions PRESERVED across re-render
    assert led.clips["clip_1"].state is ClipState.queued        # captioned/queued state PRESERVED


def test_approve_with_hook_render_failure_rolls_back(tmp_path, mocker):
    # the operator asked for the hook; a failed burn must NOT silently ship clean — roll EVERYTHING back.
    cfg = Config(root=tmp_path); _seed(cfg)
    mocker.patch("fanops.clip.render_moment", side_effect=_fake_render_err)
    res = approve_with_hook(cfg, "clip_1", now=NOW)
    assert res.ok is False and "fail" in res.error.lower()
    led = Ledger.load(cfg)
    assert led.moments["mom_1"].hook is None                    # rolled back
    assert led.moments["mom_1"].hook_removed == REMOVED         # rolled back
    assert led.posts["p_1"].state is PostState.awaiting_approval  # NOT approved


def _fake_render_burnfail(led, cfg, moment_id, *, aspect=Fmt.r9x16, **kw):
    # render SUCCEEDS (state rendered) but the hook could NOT be burned (ffmpeg lacks the text filter) —
    # render_moment flags hook_burn_failed=True, NOT ClipState.error. The clean clip masquerades as fine.
    c = next(c for c in led.clips.values() if c.parent_id == moment_id and c.aspect is aspect)
    new = c.model_copy(update={"state": ClipState.rendered, "meta_captions": {}, "hook_burn_failed": True})
    led.clips[c.id] = new
    return led, new


def test_approve_with_hook_burn_failed_rolls_back(tmp_path, mocker):
    # CRITICAL (ecc review): a render that succeeds but COULDN'T burn the hook must NOT silently ship clean —
    # the operator asked for the hook, so a burn failure rolls back exactly like a render error.
    cfg = Config(root=tmp_path); _seed(cfg)
    mocker.patch("fanops.clip.render_moment", side_effect=_fake_render_burnfail)
    res = approve_with_hook(cfg, "clip_1", now=NOW)
    assert res.ok is False and "burn" in res.error.lower()
    led = Ledger.load(cfg)
    assert led.moments["mom_1"].hook is None                    # rolled back
    assert led.moments["mom_1"].hook_removed == REMOVED         # rolled back
    assert led.posts["p_1"].state is PostState.awaiting_approval  # NOT approved clean


def test_approve_with_hook_no_removed_hook_just_approves(tmp_path, mocker):
    cfg = Config(root=tmp_path); _seed(cfg, hook_removed=None)
    r = mocker.patch("fanops.clip.render_moment", side_effect=_fake_render)
    res = approve_with_hook(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["approved"] == 1
    assert Ledger.load(cfg).posts["p_1"].state is PostState.queued
    r.assert_not_called()                                       # nothing to restore -> no re-render


def test_approve_with_hook_blocked_under_creative_variation(tmp_path, mocker, monkeypatch):
    # creative_variation suppresses the moment-hook burn (per-surface owns it) — never silently ship clean.
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "1")
    cfg = Config(root=tmp_path); _seed(cfg)
    r = mocker.patch("fanops.clip.render_moment", side_effect=_fake_render)
    res = approve_with_hook(cfg, "clip_1", now=NOW)
    assert res.ok is False and "variation" in res.error.lower()
    assert Ledger.load(cfg).posts["p_1"].state is PostState.awaiting_approval  # untouched
    r.assert_not_called()


def test_approve_with_hook_unknown_clip(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = approve_with_hook(cfg, "nope", now=NOW)
    assert res.ok is False and "no such clip" in res.error


def test_approve_as_is_approves_without_restoring(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    res = approve_as_is(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["approved"] == 1 and res.detail["hook"] is False
    led = Ledger.load(cfg)
    assert led.posts["p_1"].state is PostState.queued           # approved
    assert led.moments["mom_1"].hook is None                    # NOT restored
    assert led.moments["mom_1"].hook_removed == REMOVED         # record kept


def test_approve_as_is_no_awaiting_is_clean_noop(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, post_state=PostState.queued)
    res = approve_as_is(cfg, "clip_1", now=NOW)
    assert res.ok is True and res.detail["approved"] == 0       # nothing awaiting -> 0, never a 500
