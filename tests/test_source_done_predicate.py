# tests/test_source_done_predicate.py
"""T2.9 — a source whose clips carry no SOURCE-side work reads `inventory`, not "actionable forever".

The defect this pins: the moments_decided -> inventory demotion used to be vetoed by the complement of a
terminal clip-state set, in which `queued` was NOT terminal. Every clip on the live ledger settles at
`queued` or `retired`, so every source was vetoed forever and `backlog_actionable` could never reach zero.
`_WORK_REMAINING_CLIP` (models.py, beside ClipState) states the veto POSITIVELY instead.

Scope note: the predicate answers the SOURCE-side question only — "does this footage still need work". A
`queued` clip's posts are still in flight and still surface on the post-side fields of the same status line
(`awaiting_approval=`, `published=`, `failed=`, `needs_reconcile=`); demoting the source hides the FOOTAGE
row, never the posts.
"""

import pytest

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Moment, MomentState, Source, SourceState,
                           _NO_SOURCE_WORK_CLIP, _WORK_REMAINING_CLIP)
from fanops.agentstep import write_request
from fanops.pipeline_status import source_backlog, visible_source_ids

SID = "src_1"


def _seed(cfg, *clip_states, moment_state=MomentState.decided):
    """A moments_decided source -> one moment -> one clip per state. Returns the Config unchanged."""
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=SID, source_path="/x.mp4", state=SourceState.moments_decided))
        led.add_moment(Moment(id="m1", parent_id=SID, state=moment_state,
                              content_token="tok", start=0.0, end=5.0, reason="pick"))
        for i, st in enumerate(clip_states):
            led.add_clip(Clip(id=f"clip_{i}", parent_id="m1", state=st,
                              path=f"/x/clip_{i}.mp4", duration=5.0))
    return cfg


def _bucket(cfg):
    """The one source's bucket, read through the canonical projection (never a re-count)."""
    bl = source_backlog(Ledger.load(cfg), cfg)
    return next(r.bucket for r in bl.rows if r.id == SID)


# --- the exhaustiveness guard the amendment demands -------------------------------------------------

def test_every_clip_state_is_explicitly_classified():
    """Adding a ClipState member without classifying it FAILS here. The set this replaced rotted into the
    T2.9 defect precisely because nothing forced it to stay complete when `queued` semantics settled."""
    unclassified = set(ClipState) - (_WORK_REMAINING_CLIP | _NO_SOURCE_WORK_CLIP)
    assert unclassified == set(), (
        f"ClipState member(s) {sorted(s.value for s in unclassified)} are classified by neither "
        "_WORK_REMAINING_CLIP nor _NO_SOURCE_WORK_CLIP — classify them beside the enum in models.py")


def test_the_two_classifications_are_disjoint():
    assert _WORK_REMAINING_CLIP & _NO_SOURCE_WORK_CLIP == frozenset()


def test_work_set_is_exactly_the_four_decided_members():
    """The membership decision itself, pinned: `queued`/`held` are deliberately NOT work on the source."""
    assert _WORK_REMAINING_CLIP == frozenset((ClipState.rendered, ClipState.captions_requested,
                                              ClipState.captioned, ClipState.stitch_draft))
    assert ClipState.queued not in _WORK_REMAINING_CLIP
    assert ClipState.held not in _WORK_REMAINING_CLIP


def test_the_replaced_terminal_set_is_gone():
    """_TERMINAL_CLIP is deleted in this change — its sole consumer was the replaced helper."""
    import fanops.pipeline_status as ps
    assert not hasattr(ps, "_TERMINAL_CLIP")
    assert not hasattr(ps, "_source_has_non_terminal_clip")


# --- the bucket behaviour ---------------------------------------------------------------------------

def test_all_queued_clips_demote_to_inventory(tmp_path):
    """(a) THE LIVE CASE — all 7 sources sat here: moments_decided, every clip queued, no pending gate."""
    cfg = _seed(Config(root=tmp_path), *[ClipState.queued] * 3)
    assert _bucket(cfg) == "inventory"
    bl = source_backlog(Ledger.load(cfg), cfg)
    assert bl.actionable == 0 and bl.inventory == 1


def test_queued_and_retired_mix_demotes_to_inventory(tmp_path):
    """The live ledger's exact clip-state census per source: {queued, retired} and nothing else."""
    cfg = _seed(Config(root=tmp_path), ClipState.queued, ClipState.retired, ClipState.queued)
    assert _bucket(cfg) == "inventory"


@pytest.mark.parametrize("state", sorted(_WORK_REMAINING_CLIP, key=lambda s: s.value))
def test_each_work_state_keeps_the_source_actionable(tmp_path, state):
    """(b)+(c) generalised: every member of the work set vetoes the demotion, on its own."""
    cfg = _seed(Config(root=tmp_path / state.value), ClipState.queued, state)
    assert _bucket(cfg) == "actionable"


@pytest.mark.parametrize("state", sorted(_NO_SOURCE_WORK_CLIP, key=lambda s: s.value))
def test_no_non_work_state_vetoes_the_demotion(tmp_path, state):
    """(d) generalised: `held` — and every other non-work member — leaves the source `inventory`."""
    cfg = _seed(Config(root=tmp_path / state.value), state)
    assert _bucket(cfg) == "inventory"


def test_captioned_clip_under_a_retired_moment_is_not_work(tmp_path):
    """(e) the T3.1 guard: suppressed lineage is inert — crosspost._seed_clips already refuses to seed it,
    so counting it as source work would be a lie."""
    cfg = _seed(Config(root=tmp_path), ClipState.captioned, moment_state=MomentState.retired)
    assert _bucket(cfg) == "inventory"


def test_orphan_clip_is_not_work(tmp_path):
    """is_suppressed fails CLOSED, and this is the correct direction: an orphan is not pipeline work.
    (A clip whose moment is missing is also unreachable from the source, so it never counted anyway.)"""
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=SID, source_path="/x.mp4", state=SourceState.moments_decided))
        led.add_clip(Clip(id="clip_orphan", parent_id="m_missing", state=ClipState.captioned,
                          path="/x/o.mp4", duration=5.0))
    assert _bucket(cfg) == "inventory"


def test_pending_gate_wins_regardless_of_clip_states(tmp_path):
    """(f) precedence unchanged: a pending gate buckets blocked_on_gates even with only queued clips."""
    cfg = _seed(Config(root=tmp_path), ClipState.queued)
    write_request(cfg, kind="captions", key="clip_0", payload={"clip_id": "clip_0"})
    assert _bucket(cfg) == "blocked_on_gates"


def test_another_sources_open_clip_does_not_rescue_this_one(tmp_path):
    """The helper is per-source: a captioned clip hanging off a DIFFERENT source must not veto here."""
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id=SID, source_path="/x.mp4", state=SourceState.moments_decided))
        led.add_moment(Moment(id="m1", parent_id=SID, state=MomentState.decided,
                              content_token="tok", start=0.0, end=5.0, reason="pick"))
        led.add_clip(Clip(id="clip_0", parent_id="m1", state=ClipState.queued, path="/x/0.mp4", duration=5.0))
        led.add_source(Source(id="src_2", source_path="/y.mp4", state=SourceState.moments_decided))
        led.add_moment(Moment(id="m2", parent_id="src_2", state=MomentState.decided,
                              content_token="tok2", start=0.0, end=5.0, reason="pick"))
        led.add_clip(Clip(id="clip_1", parent_id="m2", state=ClipState.captioned, path="/y/1.mp4", duration=5.0))
    bl = source_backlog(Ledger.load(cfg), cfg)
    by_id = {r.id: r.bucket for r in bl.rows}
    assert by_id == {SID: "inventory", "src_2": "actionable"}


# --- the two consumers must never disagree ----------------------------------------------------------

@pytest.mark.parametrize("clip_state,expected_visible", [(ClipState.queued, False),
                                                         (ClipState.captioned, True),
                                                         (ClipState.stitch_draft, True),
                                                         (ClipState.held, False)])
def test_listing_and_census_agree_on_the_same_fixture(tmp_path, clip_state, expected_visible):
    """(g) `visible_source_ids` and `_source_bucket` share ONE helper deliberately — they ask the same
    sub-question, and splitting them would mint exactly the disagreement pair this track removes."""
    cfg = _seed(Config(root=tmp_path / clip_state.value), clip_state)
    led = Ledger.load(cfg)
    assert (SID in visible_source_ids(led, cfg)) is expected_visible
    assert (_bucket(cfg) != "inventory") is expected_visible
