# tests/test_post_state_sets.py
"""MOL-802 — the POST-side state sets are owned by PostState, and no member escapes classification.

The companion to T2.9's clip-side guard (`test_source_done_predicate`, which owns `_WORK_REMAINING_CLIP` /
`_NO_SOURCE_WORK_CLIP` beside ClipState). Three sets partitioned PostState from inside their READERS —
`_LIVE_POST_STATES` and `_PROTECTED_POST_STATES` on `Ledger`, `_REVIEW_REVERT_BLOCKED` in `studio.actions`
— with nothing forcing any of them to stay complete. That is the exact shape of the defect T2.9 fixed: a
reader has no reason to revisit its set when a new enum member lands, so the new member silently inherits
whatever the set's unnamed complement happens to mean, and the wrong branch runs forever.

This file is the forcing function. Each set now names its complement in models.py; adding a PostState
member without classifying it in all three pairs fails here, naming the member. `test_a_new_post_state_...`
is the negative control that proves this file is not decorative: it runs the same expression the real
assertions run, over PostState plus one member no set classifies, and requires that member to be reported.

Behaviour is NOT re-tested here — the move changed none. The `is`-identity assertions pin the re-export, so
`Ledger._LIVE_POST_STATES` cannot drift away from the definition it re-exports, and the existing suites
(test_ledger_cascade_protect, test_ledger_wipe, test_cli_gc, test_reframe, test_rearm_refusal) keep proving
the behaviour through those same objects.
"""

from enum import Enum

import pytest

from fanops import models
from fanops.ledger import Ledger
from fanops.models import (PostState, _LIVE_POST_STATES, _NOT_LIVE_POST_STATES, _PROTECTED_POST_STATES,
                           _REVIEW_REVERT_ALLOWED, _REVIEW_REVERT_BLOCKED, _UNPROTECTED_POST_STATES)
from fanops.studio import actions


def _unclassified(members, *sets):
    """Members that no set claims. ONE expression, shared by the real assertions and the negative control —
    a hand-copied lookalike in the control would prove only that the copy works."""
    covered = frozenset().union(*(frozenset(s) for s in sets))
    return {m for m in members if m not in covered}


# The three questions asked of PostState, each as (name, positive set, named complement).
PAIRS = [
    ("live", _LIVE_POST_STATES, _NOT_LIVE_POST_STATES),
    ("protected", _PROTECTED_POST_STATES, _UNPROTECTED_POST_STATES),
    ("review_revert", _REVIEW_REVERT_BLOCKED, _REVIEW_REVERT_ALLOWED),
]


# --- exhaustiveness: the guard the state-set zoo lacked ----------------------------------------------

@pytest.mark.parametrize("name,positive,complement", PAIRS)
def test_every_post_state_is_explicitly_classified(name, positive, complement):
    """Adding a PostState member without classifying it FAILS here, for every one of the three questions."""
    unclassified = _unclassified(set(PostState), positive, complement)
    assert unclassified == set(), (
        f"PostState member(s) {sorted(s.value for s in unclassified)} are classified by neither side of the "
        f"{name!r} pair — classify them beside PostState in models.py, do not let the complement default")


@pytest.mark.parametrize("name,positive,complement", PAIRS)
def test_each_pair_is_disjoint(name, positive, complement):
    """A member on both sides is not a classification — it is two answers to one question."""
    assert frozenset(positive) & frozenset(complement) == frozenset(), f"{name!r} pair overlaps"


class _HypotheticalPostState(str, Enum):
    """Tomorrow's PostState member, stood in for without editing the live enum. Its value must not collide
    with a real one, or the control would be classified by accident and prove nothing."""
    shadowbanned = "shadowbanned"


def test_the_stand_in_member_really_is_unknown_to_post_state():
    """Guards the control itself: a colliding value would make the control vacuous."""
    assert _HypotheticalPostState.shadowbanned not in set(PostState)
    assert "shadowbanned" not in {s.value for s in PostState}


@pytest.mark.parametrize("name,positive,complement", PAIRS)
def test_a_new_post_state_member_would_fail_the_guard(name, positive, complement):
    """THE NEGATIVE CONTROL. Without it, the assertions above pass for the uninteresting reason that today's
    sets are complete, and would keep passing if the expression were broken. Here the SAME `_unclassified`
    call is handed PostState plus a member no set has ever heard of, and must report exactly that member —
    so a real new PostState would reach `test_every_post_state_is_explicitly_classified` as a failure."""
    ghost = _HypotheticalPostState.shadowbanned
    assert _unclassified(set(PostState) | {ghost}, positive, complement) == {ghost}


# --- the move preserved the sets exactly (this was a relocation, not a redefinition) ------------------

def test_live_post_states_membership_type_and_order_are_unchanged():
    """A TUPLE in this ORDER, not a frozenset: `post_is_remote_or_publishable` concatenates it, the protected
    superset is built by concatenating it, and test_ledger_cascade_protect asserts tuple equality."""
    assert _LIVE_POST_STATES == (PostState.published, PostState.analyzed, PostState.submitted,
                                 PostState.submitting, PostState.needs_reconcile)
    assert isinstance(_LIVE_POST_STATES, tuple)
    assert PostState.awaiting_approval not in _LIVE_POST_STATES   # never shipped — not "live"
    assert PostState.queued not in _LIVE_POST_STATES              # approved, still not shipped


def test_protected_post_states_membership_type_and_order_are_unchanged():
    assert _PROTECTED_POST_STATES == (PostState.published, PostState.analyzed, PostState.submitted,
                                      PostState.submitting, PostState.needs_reconcile,
                                      PostState.awaiting_approval, PostState.queued, PostState.retired)
    assert isinstance(_PROTECTED_POST_STATES, tuple)


def test_protected_is_a_strict_superset_of_live():
    """Structural, not a literal: the protected set is DEFINED as live + the operator's in-flight work, so a
    live post can never fall out of cascade protection by a member being edited out of one list only."""
    assert frozenset(_LIVE_POST_STATES) < frozenset(_PROTECTED_POST_STATES)


def test_review_revert_blocked_membership_is_unchanged():
    assert _REVIEW_REVERT_BLOCKED == frozenset({PostState.published, PostState.analyzed,
                                                PostState.needs_reconcile, PostState.submitting,
                                                PostState.submitted})


# --- the readers resolve to the DEFINITION, not to a copy ---------------------------------------------

def test_the_ledger_class_attributes_re_export_the_definitions():
    """`is`, not `==`: a copy could drift member-by-member and every equality assertion would still pass
    while `Ledger._LIVE_POST_STATES` and `models._LIVE_POST_STATES` disagreed. cli, pipeline, stranded_posts
    and four pin tests all read the class attribute, so it must BE the definition."""
    assert Ledger._LIVE_POST_STATES is models._LIVE_POST_STATES
    assert Ledger._PROTECTED_POST_STATES is models._PROTECTED_POST_STATES


def test_studio_actions_reads_the_definition():
    assert actions._REVIEW_REVERT_BLOCKED is models._REVIEW_REVERT_BLOCKED


def test_the_clip_side_set_stays_on_the_ledger():
    """`_LIVE_CLIP_STATES` is deliberately NOT moved by this ticket: it partitions ClipState, whose
    source-side reading T2.9 already owns beside the enum. Two owners for one enum would be the zoo again."""
    assert "_LIVE_CLIP_STATES" in vars(Ledger)
