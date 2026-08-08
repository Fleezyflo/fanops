# tests/test_stranded_posts.py — posts stranded under a RETIRED lineage.
#
# The defect: preserve-and-retire retired the MOMENT but left its never-shipped posts
# `awaiting_approval`. review_buckets hid them, so the Review worklist and the Home/Run headline
# disagreed (493 vs 761) — and worse, approve_account/approve_batch read led.posts DIRECTLY, so a
# "112 pending" button silently promoted 176 posts, publishing lineage the pipeline had dropped.
#
# Three invariants are locked here: the cascade suppresses the lineage by retiring the MOMENT and
# relabels no descendant (every reader derives through Ledger.is_suppressed), the approve engine
# refuses a retired lineage, and the two awaiting counts can never drift again.
import json
from datetime import datetime, timezone

from fanops.accounts import Accounts
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source)
from fanops.studio.views_review import awaiting_moment_count, review_buckets, review_counts

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _seed_accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active", "persona": "hype"}]}))


def _post(pid, clip_id, state, **kw):
    return Post(id=pid, parent_id=clip_id, account="a", account_id="1", platform=Platform.instagram,
                caption="c", state=state, **kw)


def _lineage(led, *, mom_id, clip_id, moment_state=MomentState.clipped):
    """One source -> moment -> clip. The clip stays ClipState.queued: a stranded post's clip is
    normally still live — only the MOMENT is retired — which is why a clip-only guard misses it."""
    if "src_1" not in led.sources:
        led.add_source(Source(id="src_1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id=mom_id, parent_id="src_1", content_token=mom_id, start=0, end=7,
                          reason="r", state=moment_state))
    led.add_clip(Clip(id=clip_id, parent_id=mom_id, path=f"/{clip_id}.mp4", aspect=Fmt.r9x16,
                      state=ClipState.queued))


# ---- the root fix: preserve suppresses by LINEAGE; no label is copied down -------------------

def test_cascade_suppresses_by_lineage_without_relabelling(tmp_path):
    """The moment flip is the WHOLE write. Every descendant reads dead through `Ledger.is_suppressed`,
    which walks post -> clip -> moment, so nothing is copied down and no stored label is a second source
    of truth. The preserved live post keeps `analyzed` — that record is the entire reason preserve exists."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _lineage(led, mom_id="mom_x", clip_id="clip_x")
    led.add_post(_post("p_await", "clip_x", PostState.awaiting_approval))
    led.add_post(_post("p_queued", "clip_x", PostState.queued))
    led.add_post(_post("p_live", "clip_x", PostState.analyzed, public_url="dryrun://p_live"))

    led._delete_moment_cascade("mom_x")

    assert led.moments["mom_x"].state is MomentState.retired      # moment suppressed, not erased
    # STORED LABELS UNTOUCHED — the cascade relabels no descendant, shipped or not
    assert led.posts["p_await"].state is PostState.awaiting_approval
    assert led.posts["p_queued"].state is PostState.queued
    assert led.posts["p_live"].state is PostState.analyzed
    # ...yet all three read dead at the owner, derived from the moment above
    for pid in ("p_await", "p_queued", "p_live"):
        assert led.is_suppressed(led.posts[pid]), f"{pid} must derive as suppressed under a retired moment"


def test_cascade_leaves_the_clip_label_alone_and_derives_its_disposition(tmp_path):
    """The clip half of the same rule. `survived` preserves a clip whose protected posts kept it alive;
    its label is left exactly as the operator last wrote it, and `is_suppressed` derives the dead lineage
    from the retired moment instead — so a stored `retired` clip is never a drift-prone copy."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _lineage(led, mom_id="mom_x", clip_id="clip_x")
    led.add_post(_post("p_await", "clip_x", PostState.awaiting_approval))
    led.add_post(_post("p_queued", "clip_x", PostState.queued))

    led._delete_moment_cascade("mom_x")

    assert led.moments["mom_x"].state is MomentState.retired
    assert led.clips["clip_x"].state is ClipState.queued             # label untouched, NOT relabelled
    assert led.is_suppressed(led.clips["clip_x"])                    # ...but derived dead all the same
    assert "clip_x" in led.clips                                     # suppressed, NOT deleted — the row stays


def test_cascade_still_deletes_a_moment_with_nothing_protected(tmp_path):
    """Guard against over-reach: the new post-retirement must not make an unprotected moment survive."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _lineage(led, mom_id="mom_d", clip_id="clip_d")
    led.add_post(_post("p_failed", "clip_d", PostState.failed, public_url="dryrun://p_failed"))

    led._delete_moment_cascade("mom_d")

    assert "mom_d" not in led.moments and "clip_d" not in led.clips and "p_failed" not in led.posts


# ---- the approve engine must refuse a retired lineage ----------------------------------------

def test_approve_refuses_a_post_under_a_retired_moment(tmp_path):
    """approve_posts / approve_clip / approve_batch / approve_account all resolve through
    _approve_ids_with_render, so guarding it covers every bulk path. The skip is REPORTED."""
    from fanops.studio.actions_approve import approve_posts
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_live", clip_id="clip_live")
        _lineage(led, mom_id="mom_dead", clip_id="clip_dead", moment_state=MomentState.retired)
        led.add_post(_post("p_ok", "clip_live", PostState.awaiting_approval))
        led.add_post(_post("p_stranded", "clip_dead", PostState.awaiting_approval))

    res = approve_posts(cfg, ["p_ok", "p_stranded"], now=NOW)

    assert res.ok
    assert res.detail["approved"] == 1
    assert res.detail["skipped_retired"] == 1
    again = Ledger.load(cfg)
    assert again.posts["p_ok"].state is PostState.queued
    assert again.posts["p_stranded"].state is PostState.awaiting_approval   # never promoted


def test_approve_clip_skips_a_retired_clip(tmp_path):
    from fanops.studio.actions_approve import approve_clip
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_c", clip_id="clip_c")
        led.retire_clip("clip_c")                       # clip retired, moment still clipped
        led.add_post(_post("p_c", "clip_c", PostState.awaiting_approval))

    res = approve_clip(cfg, "clip_c", now=NOW)

    assert res.detail["approved"] == 0 and res.detail["skipped_retired"] == 1
    assert Ledger.load(cfg).posts["p_c"].state is PostState.awaiting_approval


def test_approve_with_hook_refuses_a_retired_clip(tmp_path):
    from fanops.studio.actions_approve import approve_with_hook
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_h", clip_id="clip_h", moment_state=MomentState.retired)
        led.add_post(_post("p_h", "clip_h", PostState.awaiting_approval))

    res = approve_with_hook(cfg, "clip_h", now=NOW)

    assert not res.ok and "retired" in (res.error or "")
    assert Ledger.load(cfg).posts["p_h"].state is PostState.awaiting_approval


# ---- the parity invariant the docstring promises but nothing enforced ------------------------

def test_awaiting_headline_equals_the_review_worklist_with_retired_lineage(tmp_path):
    """awaiting_moment_count is declared a MIRROR of review_buckets' editable bucket. Assert the
    EQUALITY, not a number: a hardcoded count rots, and it was a one-sided edit to exactly these two
    rules (guards added to review_buckets only) that produced the 493-vs-761 split."""
    cfg = Config(root=tmp_path)
    _seed_accounts(cfg)
    led = Ledger.load(cfg)
    _lineage(led, mom_id="mom_1", clip_id="clip_1")
    _lineage(led, mom_id="mom_2", clip_id="clip_2", moment_state=MomentState.retired)
    _lineage(led, mom_id="mom_3", clip_id="clip_3")
    led.retire_clip("clip_3")
    led.add_clip(Clip(id="clip_held", parent_id="mom_1", path="/h.mp4", aspect=Fmt.r9x16,
                      state=ClipState.held, held=True, held_reason="brand risk"))
    for pid, cid in (("p1", "clip_1"), ("p2", "clip_2"), ("p3", "clip_3"), ("p4", "clip_held")):
        led.add_post(_post(pid, cid, PostState.awaiting_approval))
    led.save()

    led = Ledger.load(cfg)
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)

    assert awaiting_moment_count(led) == review_counts(cards)["awaiting"]
    assert awaiting_moment_count(led) == 1        # only clip_1: retired moment, retired clip and held are all out


# ---- the one-time reconcile's finder ---------------------------------------------------------

def test_stranded_posts_finds_retired_lineage_only(tmp_path):
    from fanops.stranded_posts import stranded_posts
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _lineage(led, mom_id="mom_ok", clip_id="clip_ok")
    _lineage(led, mom_id="mom_gone", clip_id="clip_gone", moment_state=MomentState.retired)
    led.add_post(_post("p_ok", "clip_ok", PostState.awaiting_approval))
    led.add_post(_post("p_bad", "clip_gone", PostState.awaiting_approval))
    led.add_post(_post("p_live", "clip_gone", PostState.analyzed, public_url="dryrun://p_live"))

    found = {p.id for p in stranded_posts(led)}

    assert found == {"p_bad", "p_live"}                       # everything under the dead moment...
    unshipped = set(Ledger._UNSHIPPED_POST_STATES)
    assert {p.id for p in stranded_posts(led) if p.state in unshipped} == {"p_bad"}   # ...only p_bad is retirable


# ---- the pipeline writes NOTHING to dead lineage: suppression is derived, never healed -------

def _suppressed_states(led):
    """Every stored `(kind, id) -> state` whose lineage `Ledger.is_suppressed` calls dead. Enumerated from
    the ledger, never hand-listed: a hardcoded row list rots exactly like a hardcoded count."""
    rows = ([("moment", m) for m in led.moments.values()] + [("clip", c) for c in led.clips.values()]
            + [("post", p) for p in led.posts.values()])
    return {(kind, r.id): r.state for kind, r in rows if led.is_suppressed(r)}


def test_one_pass_writes_nothing_to_a_stranded_lineage(tmp_path, mocker):
    """`advance` no longer re-asserts lineage suppression into stored labels (T3.5 deleted both per-tick
    heal sweeps): every reader derives it at O(1) from `Ledger.is_suppressed`, so the stored rows are left
    exactly as the operator last wrote them. Asserted as a byte-identity over the WHOLE suppressed set —
    not five hand-named rows — so any future write to dead lineage, on any row, reddens this. The healthy
    lineage is the negative control: it must still be live work the pass may advance, or an `advance` that
    did nothing at all would pass."""
    from fanops.pipeline import advance
    mocker.patch("fanops.produce.run_all")                    # keep the pass cheap: no subprocesses
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_ok", clip_id="clip_ok")
        _lineage(led, mom_id="mom_gone", clip_id="clip_gone", moment_state=MomentState.retired)
        led.add_post(_post("p_ok", "clip_ok", PostState.awaiting_approval))     # healthy -> real work
        led.add_post(_post("p_await", "clip_gone", PostState.awaiting_approval))
        led.add_post(_post("p_queued", "clip_gone", PostState.queued))
        led.add_post(_post("p_live", "clip_gone", PostState.needs_reconcile, public_url="dryrun://p_live"))
        # a SECOND clip under the same dead moment, with nothing live on it — the sweep used to retire it
        led.add_clip(Clip(id="clip_bare", parent_id="mom_gone", path="/clip_bare.mp4", aspect=Fmt.r9x16,
                          state=ClipState.queued))
        led.add_post(_post("p_bare", "clip_bare", PostState.awaiting_approval))

    led = Ledger.load(cfg)
    before = _suppressed_states(led)
    # the snapshot is non-vacuous, and the healthy lineage is NOT in it (an empty/over-wide set would make
    # the identity below meaningless — this is the control on the control)
    assert set(before) == {("moment", "mom_gone"), ("clip", "clip_gone"), ("clip", "clip_bare"),
                           ("post", "p_await"), ("post", "p_queued"), ("post", "p_live"), ("post", "p_bare")}

    advance(cfg, base_time="2026-06-02T18:00:00Z")            # ONE ordinary pass

    led = Ledger.load(cfg)
    assert _suppressed_states(led) == before                  # byte-identical: the pass wrote nothing here
    assert led.posts["p_ok"].state is PostState.awaiting_approval   # healthy lineage -> real work stays
    assert not led.is_suppressed(led.posts["p_ok"])                 # ...and reads active at the owner
    # clip_ok sits on a LIVE lineage, so the pass legitimately advances it (captions_requested, ...). Pin
    # what this control is actually for — it was not retired — not whichever stage the pass happened to reach.
    assert led.clips["clip_ok"].state is not ClipState.retired

    advance(cfg, base_time="2026-06-02T18:00:00Z")            # convergence re-run: still nothing written
    led = Ledger.load(cfg)
    assert _suppressed_states(led) == before
    assert led.posts["p_ok"].state is PostState.awaiting_approval
