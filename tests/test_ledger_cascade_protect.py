# tests/test_ledger_cascade_protect.py
# content-lifecycle Phase 1 (wipe-safety): a re-decision's cascade must NEVER silently delete the
# operator's awaiting_approval (un-reviewed) / queued (approved, not-yet-shipped) / retired (M4 stitch-
# superseded) posts — those are the human/stitch worklist. They are PRESERVE-and-RETIRE exactly like a
# live post: BOTH the post AND its clip survive (else the post is orphaned). _LIVE_POST_STATES stays put.
from fanops.config import Config
from fanops.models import Source, Moment, Clip, Post, PostState, ClipState, MomentState, Platform
from fanops.ledger import Ledger

def _seed(tmp_path, post_state):
    # source -> moment -> NON-live clip (rendered) -> a post in `post_state`. The clip is deliberately
    # NOT a live clip so the clip's survival hinges ONLY on the post-protection (the thing under test).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/x"))
    led.add_moment(Moment(id="m", parent_id="s", content_token="A", start=0, end=2, reason="a"))
    led.add_clip(Clip(id="c", parent_id="m", path="/c", state=ClipState.rendered))
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=post_state, public_url="dryrun://p"))
    return led

def test_cascade_preserves_awaiting_approval_post_and_clip(tmp_path):
    led = _seed(tmp_path, PostState.awaiting_approval)
    led.reconcile_moments("s", {})                              # empty keep -> drops m -> cascade
    assert "p" in led.posts, "an un-reviewed awaiting_approval post must survive the cascade"
    assert "c" in led.clips, "its clip must survive too (a popped clip orphans the preserved post)"
    assert led.moments["m"].state is MomentState.retired        # moment suppressed, not erased

def test_cascade_preserves_queued_post_and_clip(tmp_path):
    led = _seed(tmp_path, PostState.queued)
    led.reconcile_moments("s", {})
    assert "p" in led.posts and "c" in led.clips, "an approved, not-yet-shipped queued post must survive"
    assert led.moments["m"].state is MomentState.retired

def test_cascade_preserves_retired_post_and_clip(tmp_path):
    led = _seed(tmp_path, PostState.retired)
    led.reconcile_moments("s", {})
    assert "p" in led.posts and "c" in led.clips, "an M4 stitch-superseded retired post must survive"

def test_cascade_still_deletes_rejected_post(tmp_path):
    # control: rejected = the operator already discarded it -> NOT a worklist -> stays deletable.
    led = _seed(tmp_path, PostState.rejected)
    led.reconcile_moments("s", {})
    assert "p" not in led.posts and "c" not in led.clips, "a rejected post is no worklist; cascade deletes it"
    assert "m" not in led.moments                               # nothing survived -> moment erased

def test_live_post_states_membership_pinned():
    # _LIVE_POST_STATES is the "live on platform" set — do NOT widen it. awaiting/queued/retired are
    # protected via the separate _PROTECTED_POST_STATES superset, NOT by mutating this pinned set.
    assert Ledger._LIVE_POST_STATES == (PostState.published, PostState.analyzed, PostState.submitted,
                                        PostState.submitting, PostState.needs_reconcile)
    assert PostState.awaiting_approval not in Ledger._LIVE_POST_STATES
    assert PostState.queued not in Ledger._LIVE_POST_STATES

# MOL-77 (R-037): when the cascade POPS a clip row (no live/worklist post holds it), its on-disk .mp4 must
# be unlinked in the same breath. cmd_gc only sweeps clips still in retired/analyzed state, so a
# ledger-row-less file is unreachable by gc forever — a permanent orphan. Unlink it here or it leaks.
def test_cascade_unlinks_dropped_clip_file(tmp_path):
    led = _seed(tmp_path, PostState.rejected)                   # rejected post -> clip is droppable
    f = tmp_path / "orphan.mp4"; f.write_bytes(b"x")
    led.clips["c"] = led.clips["c"].model_copy(update={"path": str(f)})
    led.reconcile_moments("s", {})                              # empty keep -> drops m -> cascade pops clip c
    led.save()                                                  # M22: unlinks drain on commit, not during cascade
    assert "c" not in led.clips, "the rejected-post clip is still dropped from the ledger"
    assert not f.exists(), "the dropped clip's on-disk file must be unlinked, not left as a gc-unreachable orphan"

def test_cascade_clip_unlink_is_fail_open_when_file_missing(tmp_path):
    # a clip row whose file was already gc'd / never rendered must not blow up the cascade.
    led = _seed(tmp_path, PostState.rejected)
    led.clips["c"] = led.clips["c"].model_copy(update={"path": str(tmp_path / "nope.mp4")})
    led.reconcile_moments("s", {})                              # must not raise
    assert "c" not in led.clips and "m" not in led.moments

def test_cascade_preserved_live_clip_keeps_its_file(tmp_path):
    # a live clip SURVIVES the cascade -> its file must NOT be touched.
    led = _seed(tmp_path, PostState.published)
    led.clips["c"] = led.clips["c"].model_copy(
        update={"state": ClipState.published, "path": str((tmp_path / "keep.mp4"))})
    (tmp_path / "keep.mp4").write_bytes(b"x")
    led.reconcile_moments("s", {})
    assert "c" in led.clips and (tmp_path / "keep.mp4").exists(), "a surviving live clip keeps its file"
