# tests/test_stranded_posts.py — posts stranded under a RETIRED lineage.
#
# The defect: preserve-and-retire retired the MOMENT but left its never-shipped posts
# `awaiting_approval`. review_buckets hid them, so the Review worklist and the Home/Run headline
# disagreed (493 vs 761) — and worse, approve_account/approve_batch read led.posts DIRECTLY, so a
# "112 pending" button silently promoted 176 posts, publishing lineage the pipeline had dropped.
#
# Three invariants are locked here: the cascade gives never-shipped posts a terminal state, the
# approve engine refuses a retired lineage, and the two awaiting counts can never drift again.
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


# ---- the root fix: preserve-and-retire carries DOWN to never-shipped posts -------------------

def test_cascade_retires_unshipped_posts_but_keeps_shipped_ones(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _lineage(led, mom_id="mom_x", clip_id="clip_x")
    led.add_post(_post("p_await", "clip_x", PostState.awaiting_approval))
    led.add_post(_post("p_queued", "clip_x", PostState.queued))
    led.add_post(_post("p_live", "clip_x", PostState.analyzed, public_url="dryrun://p_live"))

    led._delete_moment_cascade("mom_x")

    assert led.moments["mom_x"].state is MomentState.retired      # moment suppressed, not erased
    # never touched a platform -> terminal, so no reader can act on it again
    assert led.posts["p_await"].state is PostState.retired
    assert led.posts["p_queued"].state is PostState.retired
    # HAS touched a platform -> untouched; that record is the entire reason preserve exists
    assert led.posts["p_live"].state is PostState.analyzed
    # ...and that live post still PINS its clip: the file is what it points at, so the clip stays live
    assert led.clips["clip_x"].state is ClipState.queued


def test_cascade_retires_the_clip_once_nothing_live_is_left_on_it(tmp_path):
    """The clip half of the same rule. `survived` preserves a clip whose protected posts kept it alive but
    never relabelled it — so once those never-shipped posts are retired it kept reading `queued` under a
    dead moment: inert, but live in every clip census and invisible to gc (retired/analyzed only)."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _lineage(led, mom_id="mom_x", clip_id="clip_x")
    led.add_post(_post("p_await", "clip_x", PostState.awaiting_approval))
    led.add_post(_post("p_queued", "clip_x", PostState.queued))

    led._delete_moment_cascade("mom_x")

    assert led.moments["mom_x"].state is MomentState.retired
    assert led.posts["p_await"].state is PostState.retired          # nothing here ever touched a platform
    assert led.posts["p_queued"].state is PostState.retired
    assert led.clips["clip_x"].state is ClipState.retired           # ...so the clip is dead lineage too
    assert "clip_x" in led.clips                                    # suppressed, NOT deleted — the row stays


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


# ---- the pipeline heals it ITSELF: no operator verb, no hand-typed --apply -------------------

def test_one_pass_relabels_stranded_posts_without_an_operator(tmp_path, mocker):
    """The repair belongs to the pipeline, not to a human. A row written before the cascades existed
    sits at awaiting_approval/queued under retired lineage: inert, but counted in every raw state
    census, so the backlog never drains. `advance` re-asserts the invariant on EVERY pass. Anything
    that has touched a platform is preserved, and a healthy lineage is untouched — those two are the
    negative controls, so a blanket sweep cannot pass this test."""
    from fanops.pipeline import advance
    mocker.patch("fanops.produce.run_all")                    # keep the pass cheap: no subprocesses
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_ok", clip_id="clip_ok")
        _lineage(led, mom_id="mom_gone", clip_id="clip_gone", moment_state=MomentState.retired)
        led.add_post(_post("p_ok", "clip_ok", PostState.awaiting_approval))     # healthy -> untouched
        led.add_post(_post("p_await", "clip_gone", PostState.awaiting_approval))
        led.add_post(_post("p_queued", "clip_gone", PostState.queued))
        led.add_post(_post("p_live", "clip_gone", PostState.needs_reconcile, public_url="dryrun://p_live"))
        # a SECOND clip under the same dead moment, with nothing live on it -> the clip must go too
        led.add_clip(Clip(id="clip_bare", parent_id="mom_gone", path="/clip_bare.mp4", aspect=Fmt.r9x16,
                          state=ClipState.queued))
        led.add_post(_post("p_bare", "clip_bare", PostState.awaiting_approval))

    advance(cfg, base_time="2026-06-02T18:00:00Z")            # ONE ordinary pass — nothing typed

    led = Ledger.load(cfg)
    assert led.posts["p_await"].state is PostState.retired    # stranded, never shipped -> relabelled
    assert led.posts["p_queued"].state is PostState.retired
    assert led.posts["p_live"].state is PostState.needs_reconcile   # MAY be live -> preserved verbatim
    assert led.posts["p_ok"].state is PostState.awaiting_approval   # healthy lineage -> real work stays
    # the clip half: bare clip retired, but the one a needs_reconcile post PINS stays live, and a clip
    # under a healthy moment is never touched — both are negative controls against a blanket sweep
    assert led.clips["clip_bare"].state is ClipState.retired
    assert led.clips["clip_gone"].state is ClipState.queued
    assert led.clips["clip_ok"].state is ClipState.queued

    advance(cfg, base_time="2026-06-02T18:00:00Z")            # converges: a second pass is a no-op
    led = Ledger.load(cfg)
    assert led.posts["p_ok"].state is PostState.awaiting_approval
    assert led.posts["p_live"].state is PostState.needs_reconcile
