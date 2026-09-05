# tests/test_models_state_frozen.py — MOL-819: non-owner `.state =` is impossible by construction.
"""Freeze the `state` field on Source / Moment / Clip / Post.

Pydantic `Field(frozen=True)` makes a direct `.state =` raise. It does NOT block
`model_copy(update=…)` — that is how Ledger.set_*_state / retire_clip own the write.
Whole-model `ConfigDict(frozen=True)` was declined after re-measure: non-state attrs
still mutate in place across publish/studio; field-level freeze delivers P2 without
that blast radius.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (
    Clip, ClipState, Moment, MomentState, Platform, Post, PostState, Source, SourceState,
)


def _source() -> Source:
    return Source(id="src_1", source_path="/s/x.mp4")


def _moment() -> Moment:
    return Moment(id="mom_1", parent_id="src_1", start=0.0, end=5.0, reason="r")


def _clip() -> Clip:
    return Clip(id="clip_1", parent_id="mom_1", path="/c/x.mp4")


def _post() -> Post:
    return Post(id="post_1", parent_id="clip_1", account="a", account_id="1",
                platform=Platform.instagram, caption="x")


@pytest.mark.parametrize("factory,attr,value", [
    (_source, "state", SourceState.transcribed),
    (_moment, "state", MomentState.clipped),
    (_clip, "state", ClipState.held),
    (_post, "state", PostState.queued),
])
def test_direct_state_assignment_raises(factory, attr, value):
    """Negative control: must FAIL if Field(frozen=True) is removed from `state`."""
    row = factory()
    with pytest.raises(ValidationError, match="frozen"):
        setattr(row, attr, value)


@pytest.mark.parametrize("cls", [Source, Moment, Clip, Post])
def test_state_field_is_marked_frozen(cls):
    """Structural NC: the field flag itself is the gate — decorative without it."""
    assert cls.model_fields["state"].frozen is True


def test_ledger_set_state_owners_still_work(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(_source())
    led.add_moment(_moment())
    led.add_clip(_clip())
    led.add_post(_post())
    led.set_source_state("src_1", SourceState.transcribed)
    led.set_moment_state("mom_1", MomentState.clipped)
    led.set_clip_state("clip_1", ClipState.captioned)
    led.set_post_state("post_1", PostState.queued)
    assert led.sources["src_1"].state is SourceState.transcribed
    assert led.moments["mom_1"].state is MomentState.clipped
    assert led.clips["clip_1"].state is ClipState.captioned
    assert led.posts["post_1"].state is PostState.queued
    led.retire_clip("clip_1")
    assert led.clips["clip_1"].state is ClipState.retired


def test_set_post_state_published_clears_failure_latches(tmp_path):
    from fanops.models import ErrorKind
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(_source())
    led.add_moment(_moment())
    led.add_clip(_clip())
    p = _post().model_copy(update={"state": PostState.failed, "error_reason": "bad payload",
                                   "error_kind": ErrorKind.bad_payload})
    led.add_post(p)
    led.set_post_state("post_1", PostState.published)
    assert led.posts["post_1"].error_reason is None
    assert led.posts["post_1"].error_kind is None


def test_non_state_assignment_still_allowed():
    """Field-level freeze must not freeze the whole model (Moment.validate_assignment still useful)."""
    m = _moment()
    m.hook = "keep me"
    assert m.hook == "keep me"
    p = _post()
    p.caption = "edited"
    assert p.caption == "edited"
