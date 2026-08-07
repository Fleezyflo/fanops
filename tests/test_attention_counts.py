# tests/test_attention_counts.py — the ONE owned "needs the operator" predicate (T2.4 / MOL-759).
#
# Before this, ~20 surfaces hand-rolled "how many need me?" and disagreed — two of them shipped in the
# SAME dict literal (views.py: a filtered "awaiting" beside an unfiltered "awaiting_posts"). The rule now
# lives on the Ledger: `review_posts()` = awaiting_approval AND `can_promote` (live lineage), and
# `attention_counts()` sizes that ONE set two ways.
#
# What is pinned here: the lineage cases `can_promote` fixes (a retired ancestor, a missing ancestor —
# fail CLOSED), the posts-vs-clips distinction that produced the "Home 57 vs Review 17" bug, and (MOL-796)
# that `fanops status`' operator headline sizes this predicate rather than a raw state census.
from fanops.cli import cmd_status
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source)


def _post(pid, clip_id, state=PostState.awaiting_approval, account="a", **kw):
    return Post(id=pid, parent_id=clip_id, account=account, account_id="1", platform=Platform.instagram,
                caption="c", state=state, **kw)


def _lineage(led, *, mom_id, clip_id, moment_state=MomentState.clipped, held=False):
    """One source -> moment -> clip. The clip stays live (ClipState.queued): a stranded post's clip is
    normally still live and only the MOMENT is retired, which is why a clip-only guard misses it."""
    if "src_1" not in led.sources:
        led.add_source(Source(id="src_1", source_path="/v.mp4", language="en"))
    led.add_moment(Moment(id=mom_id, parent_id="src_1", content_token=mom_id, start=0, end=7,
                          reason="r", state=moment_state))
    led.add_clip(Clip(id=clip_id, parent_id=mom_id, path=f"/{clip_id}.mp4", aspect=Fmt.r9x16,
                      state=ClipState.held if held else ClipState.queued, held=held,
                      held_reason="brand risk" if held else None))


def test_review_posts_excludes_a_post_under_a_retired_moment(tmp_path):
    """(a) The 29-row class: the clip is LIVE, only its moment is retired. A clip-only guard reads this
    post as actionable; the derived predicate walks post -> clip -> moment and does not."""
    led = Ledger.load(Config(root=tmp_path))
    _lineage(led, mom_id="mom_live", clip_id="clip_live")
    _lineage(led, mom_id="mom_dead", clip_id="clip_stranded", moment_state=MomentState.retired)
    led.add_post(_post("p_ok", "clip_live"))
    led.add_post(_post("p_stranded", "clip_stranded"))

    assert [p.id for p in led.review_posts()] == ["p_ok"]
    assert led.attention_counts() == {"posts": 1, "moments": 1}


def test_review_posts_fails_closed_on_a_missing_clip(tmp_path):
    """(b) A missing ancestor is SUPPRESSED, never live. The old is_retired_clip/is_retired_moment pair
    failed OPEN (`bool(c and ...)` -> False when the row is gone), which is what let a post whose clip was
    deleted outright read as operator work."""
    led = Ledger.load(Config(root=tmp_path))
    _lineage(led, mom_id="mom_live", clip_id="clip_live")
    led.add_post(_post("p_ok", "clip_live"))
    led.add_post(_post("p_orphan", "clip_that_never_existed"))

    assert [p.id for p in led.review_posts()] == ["p_ok"]
    assert led.attention_counts()["posts"] == 1


def test_moments_counts_clips_not_the_surface_fan_out(tmp_path):
    """(c) The 'Home 57 vs Review 17' bug: one clip fans out to N per-account surface posts, so a POST
    tally overstates the worklist. `moments` counts distinct parent clips; `posts` keeps the raw size."""
    led = Ledger.load(Config(root=tmp_path))
    _lineage(led, mom_id="mom_1", clip_id="clip_1")
    for pid, acct in (("p_a", "a"), ("p_b", "b"), ("p_c", "c")):
        led.add_post(_post(pid, "clip_1", account=acct))

    assert led.attention_counts() == {"posts": 3, "moments": 1}


def test_a_held_clip_is_no_moment_but_its_post_is_still_actionable(tmp_path):
    """(d) A brand-risk HOLD parks the clip, it does not kill the lineage — so its awaiting post stays in
    `review_posts()` (releasing the hold is operator work) while contributing 0 to the clip worklist, the
    same asymmetry awaiting_moment_count has always had. `held` itself is a review-CARD bucket owned by
    views_review.review_counts, deliberately NOT a key here."""
    led = Ledger.load(Config(root=tmp_path))
    _lineage(led, mom_id="mom_1", clip_id="clip_open")
    _lineage(led, mom_id="mom_1", clip_id="clip_held", held=True)
    led.add_post(_post("p_open", "clip_open"))
    led.add_post(_post("p_held", "clip_held"))

    counts = led.attention_counts()
    assert sorted(p.id for p in led.review_posts()) == ["p_held", "p_open"]
    assert counts == {"posts": 2, "moments": 1}
    assert set(counts) == {"posts", "moments"}      # no held/prepared: those are card buckets, not ledger facts


def test_fanops_status_headline_reads_the_predicate_not_a_raw_census(tmp_path, capsys):
    """(e) The consumer that still drifted after T2.4: `fanops status`' post-approval headline was a raw
    `posts_in_state(awaiting_approval)` census with no lineage guard, so a stranded post inflated the
    operator's number while the Review worklist derived correctly — the live 761-vs-493 split (#827).
    The headline now sizes `review_posts()`, so it cannot disagree with the worklist."""
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_live", clip_id="clip_live")
        _lineage(led, mom_id="mom_dead", clip_id="clip_stranded", moment_state=MomentState.retired)
        led.add_post(_post("p_ok", "clip_live"))
        led.add_post(_post("p_stranded", "clip_stranded"))

    assert cmd_status(cfg) == 0
    out = capsys.readouterr().out
    led = Ledger.load(cfg)
    assert len(led.posts_in_state(PostState.awaiting_approval)) == 2   # the raw census still counts the stranded post
    assert "awaiting_approval=1 " in out                               # the headline counts only the actionable one
    assert f"awaiting_approval={led.attention_counts()['posts']} " in out   # and equals the worklist, by construction


def test_review_posts_ignores_every_non_awaiting_state(tmp_path):
    """The predicate is a WORKLIST, not a state census: an approved (queued) or shipped post has left the
    operator's queue even though its lineage is perfectly live."""
    led = Ledger.load(Config(root=tmp_path))
    _lineage(led, mom_id="mom_1", clip_id="clip_1")
    led.add_post(_post("p_await", "clip_1"))
    led.add_post(_post("p_queued", "clip_1", state=PostState.queued))
    led.add_post(_post("p_published", "clip_1", state=PostState.published, public_url="dryrun://p_published"))

    assert [p.id for p in led.review_posts()] == ["p_await"]
    assert led.attention_counts() == {"posts": 1, "moments": 1}
