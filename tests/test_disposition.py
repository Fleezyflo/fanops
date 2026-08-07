# tests/test_disposition.py — the DERIVED disposition layer: Ledger.is_suppressed / can_seed / can_promote.
#
# Retirement used to be a copied label: six write-time copy-down sites propagated it and eleven read
# sites re-implemented "is this lineage dead?" by hand (#825-#829 were five separate re-discoveries of
# the same rule with no owner). These tests pin the OWNER's contract, not any consumer — the layer is
# deliberately unused at this commit, and every consumer swap is a wired follow-up ticket.
#
# The two properties that make it an owner rather than a seventh copy:
#   1. it FAILS CLOSED — a missing ancestor row reads suppressed, where is_retired_clip/is_retired_moment
#      still fail OPEN (that fail-open is exactly what let a stranded post read as live work);
#   2. it DERIVES — answering never writes a label, so a mislabelled row reads correctly with zero writes.
import pytest

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source)


def _led(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_source(Source(id="src_1", source_path="/v.mp4", language="en"))
    return led


def _moment(led, mid, state=MomentState.clipped, parent="src_1"):
    led.add_moment(Moment(id=mid, parent_id=parent, content_token=mid, start=0, end=7, reason="r", state=state))
    return led.moments[mid]


def _clip(led, cid, parent, state=ClipState.queued, held=False):
    led.add_clip(Clip(id=cid, parent_id=parent, path=f"/{cid}.mp4", aspect=Fmt.r9x16, state=state, held=held))
    return led.clips[cid]


def _post(led, pid, parent, state=PostState.awaiting_approval):
    led.add_post(Post(id=pid, parent_id=parent, account="a", account_id="1", platform=Platform.instagram,
                      caption="c", state=state))
    return led.posts[pid]


# ---- the walk ---------------------------------------------------------------------------------

def test_active_lineage_reads_active(tmp_path):
    """Negative control: a fully live lineage must read LIVE at all three levels. Without this, a
    blanket-suppress implementation would pass every other test in this file."""
    led = _led(tmp_path)
    m = _moment(led, "mom_live")
    c = _clip(led, "clip_live", "mom_live")
    p = _post(led, "post_live", "clip_live")
    assert led.is_suppressed(m) is False
    assert led.is_suppressed(c) is False
    assert led.is_suppressed(p) is False


def test_retired_moment_suppresses_clip_and_post(tmp_path):
    """The copy-down case: only the MOMENT carries the label, yet the whole subtree is dead — and
    deriving the answer writes NOTHING (the stored states are still the pre-read ones)."""
    led = _led(tmp_path)
    _moment(led, "mom_dead", state=MomentState.retired)
    c = _clip(led, "clip_x", "mom_dead")
    p = _post(led, "post_x", "clip_x")
    assert led.is_suppressed(c) is True
    assert led.is_suppressed(p) is True
    assert led.clips["clip_x"].state is ClipState.queued          # derive-at-read: no label copied down
    assert led.posts["post_x"].state is PostState.awaiting_approval


def test_retired_clip_suppresses_only_its_own_posts(tmp_path):
    """Suppression follows the lineage, not the moment: a retired clip must not poison its live sibling."""
    led = _led(tmp_path)
    _moment(led, "mom_live")
    _clip(led, "clip_a", "mom_live", state=ClipState.retired)
    _clip(led, "clip_b", "mom_live")
    pa = _post(led, "post_a", "clip_a")
    pb = _post(led, "post_b", "clip_b")
    assert led.is_suppressed(pa) is True
    assert led.is_suppressed(pb) is False


def test_missing_ancestor_fails_closed(tmp_path):
    """The posture change. A row whose parent row is GONE is dead work, not live work — and the old
    intrinsic helpers still answer False on the very same inputs, which is the fail-open being replaced."""
    led = _led(tmp_path)
    orphan_clip = _clip(led, "clip_orphan", "mom_missing")
    orphan_post = _post(led, "post_orphan", "clip_missing")
    assert led.is_suppressed(orphan_clip) is True
    assert led.is_suppressed(orphan_post) is True
    assert led.is_retired_moment("mom_missing") is False          # fail-OPEN, still in place by design
    assert led.is_retired_clip("clip_missing") is False


def test_intrinsic_post_retirement_suppresses(tmp_path):
    """The stitch-supersede / canary shape: the lineage is fully live, the POST itself is retired."""
    led = _led(tmp_path)
    _moment(led, "mom_live")
    _clip(led, "clip_live", "mom_live")
    p = _post(led, "post_retired", "clip_live", state=PostState.retired)
    assert led.is_suppressed(p) is True


def test_rejected_and_failed_are_not_suppressed(tmp_path):
    """Only `retired` suppresses. `rejected` and `failed` are workflow states this predicate does not
    read — folding them in here would silently widen a lineage question into a workflow question."""
    led = _led(tmp_path)
    _moment(led, "mom_live")
    _clip(led, "clip_live", "mom_live")
    rejected = _post(led, "post_rejected", "clip_live", state=PostState.rejected)
    failed = _post(led, "post_failed", "clip_live", state=PostState.failed)
    assert led.is_suppressed(rejected) is False
    assert led.is_suppressed(failed) is False


# ---- the two verbs ----------------------------------------------------------------------------

def test_can_seed_excludes_held_and_suppressed(tmp_path):
    """Seeding asks TWO questions: a brand-risk HOLD and a dead lineage each veto on their own."""
    led = _led(tmp_path)
    _moment(led, "mom_live")
    _moment(led, "mom_dead", state=MomentState.retired)
    assert led.can_seed(_clip(led, "clip_held", "mom_live", held=True)) is False
    assert led.can_seed(_clip(led, "clip_dead", "mom_dead")) is False
    assert led.can_seed(_clip(led, "clip_ok", "mom_live")) is True


@pytest.mark.parametrize("pid,clip_id,post_state,expected", [
    ("post_ok", "clip_live", PostState.awaiting_approval, True),          # live lineage, live post
    ("post_under_dead", "clip_dead", PostState.awaiting_approval, False),  # copied-down suppression
    ("post_intrinsic", "clip_live", PostState.retired, False),            # intrinsically retired post
    ("post_orphan", "clip_missing", PostState.awaiting_approval, False),  # fail-closed
])
def test_can_promote_matches_post_disposition(tmp_path, pid, clip_id, post_state, expected):
    """can_promote is exactly `not suppressed` — one rule for approve, re-arm and publish alike."""
    led = _led(tmp_path)
    _moment(led, "mom_live")
    _moment(led, "mom_dead", state=MomentState.retired)
    _clip(led, "clip_live", "mom_live")
    _clip(led, "clip_dead", "mom_dead")
    assert led.can_promote(_post(led, pid, clip_id, state=post_state)) is expected


def test_is_suppressed_rejects_unknown_type(tmp_path):
    """A LOUD raise, not a fail-open default: answering "is this dead?" about a row the walk does not
    model would be a guess, and a guess here is the exact defect class this layer exists to end."""
    led = _led(tmp_path)
    with pytest.raises(TypeError):
        led.is_suppressed(led.sources["src_1"])
    with pytest.raises(TypeError):
        led.is_suppressed(None)
