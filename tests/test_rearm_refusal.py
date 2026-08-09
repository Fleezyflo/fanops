"""T3.4 — Studio's five re-arm verbs REFUSE a retired-lineage post at the write, loudly.

Before this, `bulk_send_to_review` and the four retry verbs moved a post under a retired clip/moment back
to `awaiting_approval`/`queued`, and the per-tick heal sweep silently un-did it ~600s later. These pin the
refusal at the write (the sweep is deleted by T3.5), the `skipped_retired` counter, the
`skipped_retired_lineage` breadcrumb, and the deliberate asymmetry: `discard` is a BACKWARD move and stays
ungated. Every case carries a live-lineage negative control so a blanket refusal cannot pass.
"""
import json

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, ErrorKind, Fmt, Moment, MomentState, Platform, Post, PostState, Source)
from fanops.studio import actions

import pytest

_RATE_LIMIT = "postiz 429 rate limited"
_OVERSIZE = "zernio upload 413 entity too large"
_TRANSIENT = "publish failed: NameResolutionError zernio.com"


def _chain(led, tag, *, retire=None, state=PostState.failed, error_reason=_RATE_LIMIT,
           error_kind=ErrorKind.rate_limit):
    """Seed one full (source, moment, clip, post) chain. `retire` marks 'moment' or 'clip' retired — a post
    is NEVER orphaned, because `Ledger.is_suppressed` fails closed and a missing ancestor row would refuse
    for the wrong reason (the cascade in `_delete_moment_cascade` prevents that shape in production)."""
    led.add_source(Source(id=f"s_{tag}", source_path="/v.mp4", duration=10.0))
    led.add_moment(Moment(id=f"m_{tag}", parent_id=f"s_{tag}", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.retired if retire == "moment" else MomentState.clipped))
    led.add_clip(Clip(id=f"c_{tag}", parent_id=f"m_{tag}", path=f"/{tag}.mp4", aspect=Fmt.r9x16,
                      state=ClipState.retired if retire == "clip" else ClipState.captioned))
    led.add_post(Post(id=tag, parent_id=f"c_{tag}", account="a", account_id="1", platform=Platform.instagram,
                      caption="c", state=state, error_reason=error_reason, error_kind=error_kind))
    return tag


def test_bulk_send_to_review_refuses_retired_lineage(tmp_path):
    """A queued post under a RETIRED moment must not go back to Review — its lineage is dead, so re-arming
    it only re-queues work the system already dropped. The live sibling still moves (negative control)."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _chain(led, "live", state=PostState.queued, error_reason=None, error_kind=None)
    _chain(led, "dead", retire="moment", state=PostState.queued, error_reason=None, error_kind=None)
    led.save()
    res = actions.bulk_send_to_review(cfg, ["live", "dead"], reason="test")
    assert res.ok, res
    assert res.detail["moved"] == 1 and res.detail["skipped_retired"] == 1
    after = Ledger.load(cfg).posts
    assert after["live"].state is PostState.awaiting_approval
    assert after["dead"].state is PostState.queued, "the refused post's stored state must be UNCHANGED"


def test_refusal_writes_a_breadcrumb(tmp_path):
    """'Loudly' is the point: a refusal that left no trace would be the same silence the per-tick sweep had.
    The event name matches what `post/run.py` and `actions_approve.py` already emit."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _chain(led, "dead", retire="clip", state=PostState.queued, error_reason=None, error_kind=None)
    led.save()
    assert actions.bulk_send_to_review(cfg, ["dead"], reason="test").ok
    recs = [json.loads(x) for x in cfg.log_path.read_text().splitlines() if x.strip()]
    hit = [r for r in recs if r["outcome"] == "skipped_retired_lineage"]
    assert len(hit) == 1, f"expected ONE refusal breadcrumb, got {recs}"
    assert hit[0]["unit_id"] == "dead" and hit[0]["account"] == "a"


def test_recover_posts_retry_refuses_retired_lineage(tmp_path):
    """The Posted-tab recovery cockpit's retry is a re-arm like any other. A failed post under a RETIRED
    clip stays failed; a failed post on live lineage still recovers."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _chain(led, "live")
    _chain(led, "dead", retire="clip")
    led.save()
    res = actions.recover_posts(cfg, ["live", "dead"], action="retry", reason="test")
    assert res.ok, res
    assert res.detail["retried"] == 1 and res.detail["skipped_retired"] == 1
    after = Ledger.load(cfg).posts
    assert after["live"].state is PostState.queued
    assert after["dead"].state is PostState.failed and after["dead"].error_reason == _RATE_LIMIT


def test_recover_posts_discard_still_works_on_retired_lineage(tmp_path):
    """The asymmetry is deliberate, not an oversight: `discard` moves BACKWARD toward terminal and re-arms
    nothing. Gating it would leave the operator unable to clear noise off a dead lineage."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _chain(led, "dead", retire="moment")
    led.save()
    res = actions.recover_posts(cfg, ["dead"], action="discard", reason="test")
    assert res.ok, res
    assert res.detail["discarded"] == 1 and res.detail["skipped_retired"] == 0
    assert Ledger.load(cfg).posts["dead"].state is PostState.rejected


@pytest.mark.parametrize("verb,reason_text,kind,retire", [
    ("retry_rate_limited_failures", _RATE_LIMIT, ErrorKind.rate_limit, "clip"),
    ("retry_oversize_failures", _OVERSIZE, ErrorKind.oversize, "moment"),
    ("retry_transient_failures", _TRANSIENT, ErrorKind.transient, "clip"),
])
def test_each_retry_verb_refuses_retired_lineage(tmp_path, mocker, verb, reason_text, kind, retire):
    """All three sweep-the-whole-ledger retry verbs carry the same guard. Each run has one retired-lineage
    and one live-lineage candidate, so a verb that refused everything would fail on `retried == 1`."""
    mocker.patch("fanops.post.compress.apply_shrink_to_post", return_value=True)   # only retry_oversize calls it
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _chain(led, "live", error_reason=reason_text, error_kind=kind)
    _chain(led, "dead", retire=retire, error_reason=reason_text, error_kind=kind)
    led.save()
    res = getattr(actions, verb)(cfg)
    assert res.ok, res
    assert res.detail["retried"] == 1 and res.detail["skipped_retired"] == 1
    after = Ledger.load(cfg).posts
    assert after["live"].state is PostState.queued
    assert after["dead"].state is PostState.failed, f"{verb} re-armed a retired lineage"
    assert after["dead"].error_reason == reason_text, "a refused post keeps its failure latch"


def test_stored_retired_post_is_not_revertible(tmp_path):
    """A post whose OWN state is `retired` is refused by the same guard — no `PostState.retired` member was
    added to `_REVIEW_REVERT_BLOCKED`, because the refusal mechanism owns this, not a frozenset."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _chain(led, "self", state=PostState.retired, error_reason=None, error_kind=None)
    led.save()
    res = actions.bulk_send_to_review(cfg, ["self"], reason="test")
    assert res.ok, res
    assert res.detail["moved"] == 0 and res.detail["skipped_retired"] == 1
    assert Ledger.load(cfg).posts["self"].state is PostState.retired


def test_retry_oversize_does_not_transcode_a_refused_post(tmp_path, mocker):
    """The guard sits BEFORE the shrink, not before the state write: `apply_shrink_to_post` transcodes and
    rewrites `media_urls` + the render row, so guarding after it would commit a write on a REFUSED re-arm."""
    shrink = mocker.patch("fanops.post.compress.apply_shrink_to_post", return_value=True)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _chain(led, "dead", retire="clip", error_reason=_OVERSIZE, error_kind=ErrorKind.oversize)
    led.save()
    res = actions.retry_oversize_failures(cfg)
    assert res.ok and res.detail["skipped_retired"] == 1 and res.detail["retried"] == 0
    assert shrink.call_count == 0, "a refused re-arm must not transcode or mutate its media"
