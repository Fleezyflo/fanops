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
#
# T2.5 (MOL-763) adds the rest of the surfaces, BY NAME. The old acceptance was a grep total
# (`rg attention_counts | wc -l >= 6`), which counts prose: at the time it was written 2 of its 3 hits were a
# comment and a docstring. A count of text lines cannot say that a surface consumes the owner, so the tests
# below name each rewired surface and assert its number instead.
import json
from datetime import datetime, timezone

from fanops.cli import cmd_status
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source)

NOW = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


def _post(pid, clip_id, state=PostState.awaiting_approval, account="a", **kw):
    return Post(id=pid, parent_id=clip_id, account=account, account_id="1", platform=Platform.instagram,
                caption="c", state=state, **kw)


def _seed_accounts(cfg, handles=("a",)):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": h, "account_id": "1", "platforms": ["instagram"], "status": "active"} for h in handles]}))


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


# ================= T2.5 (MOL-763): every operator surface reads the owned predicate =================

def _one_live_one_stranded(cfg):
    """The fixture the whole rewire is about: two awaiting posts, ONE of them stranded under a retired
    moment. Every surface below must report 1, and every DIAGNOSTIC census must still report 2."""
    _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_live", clip_id="clip_live")
        _lineage(led, mom_id="mom_dead", clip_id="clip_stranded", moment_state=MomentState.retired)
        led.add_post(_post("p_ok", "clip_live"))
        led.add_post(_post("p_stranded", "clip_stranded"))
    return Ledger.load(cfg)


def _metrics_body(cfg, monkeypatch):
    """/metrics without the dependency probes: the post gauges are built in their own try block, before
    build_health_report is ever called, so stubbing the health half keeps this a ledger test."""
    import fanops.health_model as hm
    monkeypatch.setattr(hm, "build_health_report", lambda cfg, led=None: type("R", (), {"deps": []})())
    monkeypatch.setattr(hm, "heartbeat_stale", lambda cfg: (None, False, 600))
    return hm.render_prometheus_metrics(cfg)


def test_cli_metrics_and_runsummary_agree_on_the_actionable_count(tmp_path, capsys, monkeypatch):
    """Cross-surface agreement. Three operator/diagnostic surfaces, one ledger, one retired-lineage post:
    the DERIVED number is the same everywhere, and each raw census still reports the bigger, different
    number beside it under its own name. Before T2.4/T2.5 these three disagreed by construction."""
    from fanops.pipeline import _build_summary
    cfg = Config(root=tmp_path)
    led = _one_live_one_stranded(cfg)
    actionable = led.attention_counts()["posts"]
    assert actionable == 1
    assert len(led.posts_in_state(PostState.awaiting_approval)) == 2      # the raw census, deliberately larger

    assert cmd_status(cfg) == 0
    assert f"awaiting_approval={actionable} " in capsys.readouterr().out

    body = _metrics_body(cfg, monkeypatch)
    assert f"fanops_posts_actionable {actionable}" in body
    assert 'fanops_posts{state="awaiting_approval"} 2' in body            # raw census, unchanged and labelled
    assert "# HELP fanops_posts_actionable" in body                       # a gauge with no HELP is not a metric

    summary = _build_summary(cfg, before=set())
    assert summary["awaiting_actionable"] == actionable
    assert summary["awaiting_approval"] == 2                             # RunSummary keeps BOTH, labelled


def test_the_studio_surfaces_agree_with_the_cli_and_metrics(tmp_path):
    """The Studio half of the same agreement, named surface by surface — Home's headline, Home's per-account
    badge, and the Review worklist the operator actually clicks through."""
    from fanops.accounts import Accounts
    from fanops.studio.views import home_status, account_work_counts
    from fanops.studio.views_review import review_buckets, review_counts
    cfg = Config(root=tmp_path)
    led = _one_live_one_stranded(cfg)

    counts = home_status(cfg).counts
    assert counts["awaiting"] == led.attention_counts()["moments"] == 1   # row 1: the owned clip-sized worklist
    assert counts["awaiting_posts"] == 2                                  # the raw census beside it, untouched
    assert account_work_counts(cfg)["a"]["awaiting"] == 1                 # row 4: the badge is a worklist too
    cards = review_buckets(led, Accounts.load(cfg), cfg, now=NOW)
    assert review_counts(cards)["awaiting"] == 1                          # ...and the page they all link to


def test_the_review_handoff_batch_pickers_ignore_dead_lineage(tmp_path):
    """Rows 5: `review_handoff` / `review_nav_params` choose the batch the operator is sent to. Off a raw
    state tally the LARGER batch wins even when every post in it is stranded — the handoff link then lands
    on a Review page showing nothing. Off the owned worklist the live batch wins."""
    from fanops.studio.views import review_handoff, review_nav_params
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_live", clip_id="clip_live")
        _lineage(led, mom_id="mom_dead", clip_id="clip_dead", moment_state=MomentState.retired)
        led.add_post(_post("p_live", "clip_live", batch_id="b_live"))
        led.add_post(_post("p_d1", "clip_dead", batch_id="b_dead"))
        led.add_post(_post("p_d2", "clip_dead", batch_id="b_dead"))       # the bigger batch, entirely dead

    assert review_handoff(cfg) == {"account": "a", "awaiting": 1, "batch": "b_live"}
    assert review_nav_params(cfg, "a")["batch"] == "b_live"


def test_an_account_whose_work_is_all_stranded_offers_no_handoff(tmp_path):
    """The negative control for row 4: with every awaiting post stranded the badge is 0, so Home offers no
    Review handoff at all rather than sending the operator to an empty page."""
    from fanops.studio.views import review_handoff, account_work_counts
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_dead", clip_id="clip_dead", moment_state=MomentState.retired)
        led.add_post(_post("p_d1", "clip_dead"))

    # No awaiting arm fires, so the handle never enters the map at all — the same shape an account with only
    # retired posts has always had, and every reader (`home_accounts_panel`, the session bar) defaults it to 0.
    assert account_work_counts(cfg).get("a", {}).get("awaiting", 0) == 0
    assert review_handoff(cfg) == {}


def test_a_held_clips_post_counts_in_posts_and_not_in_moments(tmp_path, monkeypatch):
    """THE DECISION this unit had to make explicitly (MOL-759's landmine). `can_promote` does not read
    `clip.held` — only `can_seed` does — so a brand-held clip's awaiting post IS in `review_posts()` and IS
    counted by `posts`, while `moments` (distinct non-held parent clips) excludes it.

    Both new diagnostic companions are sized in POSTS on purpose: each sits beside a per-POST census
    (`fanops_posts{state=...}`, RunSummary's `awaiting_approval`) and answers the same per-post question,
    "of these rows, how many are the operator's queue?". A hold IS operator work — releasing or dropping it
    is a decision only the operator can make, and the Review page parks such a clip in its `held` bucket
    rather than discarding it. The clip-sized view of the same worklist stays available under its own name,
    `fanops_awaiting_moments`. This test exists so the divergence cannot drift back silently."""
    from fanops.pipeline import _build_summary
    cfg = Config(root=tmp_path); _seed_accounts(cfg)
    with Ledger.transaction(cfg) as led:
        _lineage(led, mom_id="mom_1", clip_id="clip_open")
        _lineage(led, mom_id="mom_1", clip_id="clip_held", held=True)
        led.add_post(_post("p_open", "clip_open"))
        led.add_post(_post("p_held", "clip_held"))

    assert Ledger.load(cfg).attention_counts() == {"posts": 2, "moments": 1}
    assert _build_summary(cfg, before=set())["awaiting_actionable"] == 2
    body = _metrics_body(cfg, monkeypatch)
    assert "fanops_posts_actionable 2" in body
    assert "fanops_awaiting_moments 1" in body               # the clip-sized view, deliberately different


def test_bulk_approve_still_reports_the_lineage_refusal_it_makes(tmp_path):
    """Row 10, and the one place this unit deviates from the brief. `_approve_matching`'s resolver now reads
    `posts_in_state` (an owned accessor) instead of scanning `led.posts` by hand, but it deliberately does
    NOT pre-filter to `review_posts()`: `_approve_ids_with_render` asks `can_promote` per post and COUNTS the
    refusal into `skipped_retired` plus a `skipped_retired_lineage` breadcrumb. Pre-filtering promotes the
    same posts and destroys that report — a stale Review page approving a since-retired clip would come back
    "approved 0" with no reason logged anywhere. Selection is intent; the owner still gives the verdict."""
    from fanops.studio.actions_approve import approve_account
    cfg = Config(root=tmp_path)
    _one_live_one_stranded(cfg)

    res = approve_account(cfg, "a", now=NOW)

    assert res.ok
    assert res.detail["approved"] == 1                       # only the live one promotes...
    assert res.detail["skipped_retired"] == 1                # ...and the refusal is still counted, not silent
    again = Ledger.load(cfg)
    assert again.posts["p_ok"].state is PostState.queued
    assert again.posts["p_stranded"].state is PostState.awaiting_approval
