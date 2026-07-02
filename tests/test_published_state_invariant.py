"""R1: Published state invariant — end the ghost-publish class.

The architectural defect this file pins: PostState.published is OVERLOADED. It means both
"backend acknowledged the request" AND "the operator has a permalink to verify it" — but no
type-level constraint binds the two. Three writers (DryRunPoster.publish, actions.mark_published,
cli.cmd_resolve) plus the _publish_one promotion path can produce a Post(state=published,
public_url="") — a row that says SHIPPED but is unverifiable to the operator. The 5 ghost-publishes
on 2026-06-29 (5 sidecar JSONs at 05_scheduled/post_*.json at 16:10:01-26 local) were exactly this.

This file is the FULL RED suite for plan
.claude/plans/defect-roots/R1-published-state-invariant.plan.md and asserts ONE invariant from
seven different angles (one per defect D1, D2, D3, D8, D9, D10, D16). Once these all GREEN the
bad path is structurally unconstructable, not guarded — which is what `fix-root-not-symptom`
means."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (
    Clip,
    ClipState,
    Fmt,
    Moment,
    MomentState,
    Platform,
    Post,
    PostState,
    Source,
    is_real_submission_id,
)


# ───────────────────────────────────────────────────────────────────────────
# Common fixture helpers — minimal Post construction (avoids leaking R2's accounts.json work)
# ───────────────────────────────────────────────────────────────────────────

def _make_post(state: PostState, *, public_url: str | None = None, pid: str = "p_t") -> Post:
    """Construct a Post in the requested state. public_url is the field-under-test for R1."""
    return Post(
        id=pid, parent_id="c_t", account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=state,
        public_url=public_url,
    )


def _seed_minimal_ledger(cfg: Config) -> Clip:
    """Seed a queued Post the publish path can claim. Returns the clip the post is parented to."""
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/s.mp4", duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    clip = Clip(id="clip_1", parent_id="mom_1", path="/clip_1_9x16.mp4", aspect=Fmt.r9x16,
                state=ClipState.captioned)
    clip.meta_captions = {"@a/instagram": {"caption": "c", "hashtags": []}}
    led.add_clip(clip)
    return clip


# ───────────────────────────────────────────────────────────────────────────
# D3 — the Pydantic model invariant: state=published ⇒ public_url non-empty
# (the foundation; D1, D2, D8, D9, D10 ride on this)
# ───────────────────────────────────────────────────────────────────────────

def test_post_published_without_url_raises():
    """RED: Post(state=published, public_url=None) is a contradiction — REJECT at construction.
    Today this is freely constructible — the ghost-publish row. After R1 the Pydantic model raises."""
    with pytest.raises(ValidationError) as exc:
        _make_post(PostState.published, public_url=None)
    assert "public_url" in str(exc.value).lower(), (
        f"the rejection MUST name public_url so the operator/dev knows which field is missing; "
        f"got: {exc.value!s}")


def test_post_published_with_empty_url_raises():
    """RED: Post(state=published, public_url='') is the SAME bug as None — an empty string is not
    a permalink. Reject both ways."""
    with pytest.raises(ValidationError):
        _make_post(PostState.published, public_url="")


def test_post_published_with_whitespace_url_raises():
    """RED: an all-whitespace public_url is not a permalink either — the invariant is
    'non-empty after strip', not 'is set'."""
    with pytest.raises(ValidationError):
        _make_post(PostState.published, public_url="   ")


def test_post_published_with_dryrun_url_constructs_ok():
    """RED: a dryrun:// public_url is a valid permalink (per M5 _classify_channel — dryrun rows
    label as 'dryrun'). The invariant is structural (URL non-empty), not 'must be https'."""
    p = _make_post(PostState.published, public_url="dryrun://p_t")
    assert p.state is PostState.published
    assert p.public_url == "dryrun://p_t"


def test_post_published_with_https_url_constructs_ok():
    """RED: a real https permalink is the canonical happy path."""
    p = _make_post(PostState.published, public_url="https://www.instagram.com/p/abc123/")
    assert p.public_url == "https://www.instagram.com/p/abc123/"


def test_post_analyzed_without_url_raises():
    """RED: analyzed is a TERMINAL state past published — same invariant applies."""
    with pytest.raises(ValidationError):
        _make_post(PostState.analyzed, public_url=None)


def test_post_retired_without_url_raises():
    """RED: retired is the lifecycle's archival terminal — same invariant applies."""
    with pytest.raises(ValidationError):
        _make_post(PostState.retired, public_url=None)


def test_post_queued_without_url_constructs_ok():
    """RED (firewall): non-terminal states MUST stay constructible without a URL. The invariant
    is SCOPED to published/analyzed/retired — queued/submitting/submitted/awaiting_approval are
    pre-permalink states by design (a queued post can't have a permalink yet)."""
    p = _make_post(PostState.queued, public_url=None)
    assert p.state is PostState.queued
    assert p.public_url is None


def test_post_submitting_without_url_constructs_ok():
    """RED (firewall): submitting is the mid-network crash-safe state (F11). It MUST be
    constructible without a URL — the URL arrives only after the poster returns."""
    p = _make_post(PostState.submitting, public_url=None)
    assert p.state is PostState.submitting


def test_post_failed_with_or_without_url_constructs_ok():
    """RED (firewall): failed is a terminal NEGATIVE — it MAY or MAY NOT have a public_url
    (a failed publish AFTER the post hit the backend would have one; a failed publish from a
    pre-network error would not). The invariant must NOT block either shape."""
    p1 = _make_post(PostState.failed, public_url=None)
    p2 = _make_post(PostState.failed, public_url="https://www.instagram.com/p/abc/")
    assert p1.state is PostState.failed
    assert p2.public_url is not None


# ───────────────────────────────────────────────────────────────────────────
# is_real_submission_id excludes ONLY the fanops_ idempotency token.
# (dryrun-boundary M2 removed the dryrun_ synthetic-id path entirely — no code stamps a dryrun_
# submission id any more, since a dryrun post halts `queued` at the boundary and never distributes.
# The predicate therefore no longer needs to name dryrun_; only the load-bearing fanops_ token is excluded.)
# ───────────────────────────────────────────────────────────────────────────

def test_is_real_submission_id_still_excludes_fanops_prefix():
    """The fanops_ exclusion MUST stay — the crosspost idempotency-token contract is load-bearing
    (a fanops_ token is a birth stand-in, not a backend-resolvable id)."""
    assert is_real_submission_id("fanops_abc123") is False


def test_is_real_submission_id_accepts_real_backend_ids():
    """RED (firewall): a real Postiz id (cm…) or a Blotato numeric id stays True."""
    assert is_real_submission_id("cmqeb1uuv0001o579bjcdj7my") is True
    assert is_real_submission_id("17841414501372977") is True


# ───────────────────────────────────────────────────────────────────────────
# dryrun-boundary M2 — DryRunPoster.publish is a PREVIEW writer: it writes the would-send sidecar
# and touches NO distribution artifacts (no state / submission_id / public_url). A dry run does not
# distribute, so it fabricates none of them. (Supersedes the old D1 pin, which required the poster to
# stamp dryrun://<id> + dryrun_<id> + submitted — the phantom-publish behavior M1/M2 removed.)
# ───────────────────────────────────────────────────────────────────────────

def test_dryrun_poster_writes_preview_and_no_artifacts(tmp_path):
    """The reduced DryRunPoster.publish writes the would-send preview sidecar and leaves the post
    otherwise untouched: no public_url, no submission_id, state unchanged. Post-M1 a dryrun post is
    held `queued` at the publish_due boundary and never enters distribution, so the poster has no
    honest reason to fabricate a permalink or a synthetic id."""
    from fanops.post.dryrun import DryRunPoster
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_t", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.queued,
        media_urls=["file:///clip_1_9x16.mp4"],
    ))
    led.save()
    led = Ledger.load(cfg)

    poster = DryRunPoster(cfg)
    led_after = poster.publish(led, "post_t")

    p = led_after.posts["post_t"]
    assert (cfg.scheduled / "post_t.json").exists()            # the would-send preview WAS written
    assert p.public_url is None                                # no fabricated permalink
    assert p.submission_id is None                             # no synthetic id
    assert p.state is PostState.queued                         # state untouched — nothing distributed


# ───────────────────────────────────────────────────────────────────────────
# D2 — _publish_one MUST refuse to flip submitted→published if public_url is empty
# (parks the post in needs_reconcile so the next reconcile pass can back-fill the URL;
# this also catches a future Postiz poster that returns 'submitted' but with no permalink yet)
# ───────────────────────────────────────────────────────────────────────────

def test_publish_one_parks_post_without_url_in_needs_reconcile(tmp_path, monkeypatch):
    """RED: a poster that returns state=submitted WITHOUT setting post.public_url MUST NOT result
    in state=published. _publish_one must park the post in needs_reconcile (audit trail: explicit
    publish_missing_url breadcrumb) so reconcile.py can heal it on the next pass.

    Today _publish_one:172-174 unconditionally promotes submitted→published — that's the gate
    that LETS D1 through. After R1 it's gated on public_url being non-empty."""
    from fanops.post.run import _publish_one

    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_g", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.queued,
        media_urls=["file:///clip_1_9x16.mp4"],
    ))
    led.save()

    # Install a fake poster that advances submitting→submitted but leaves public_url empty —
    # exactly what today's DryRunPoster does, and what a future Postiz async-permalink path
    # might do.
    class _GhostPoster:
        def publish(self, led, post_id):
            p = led.posts[post_id]
            p.submission_id = "dryrun_" + post_id
            p.state = PostState.submitted
            # NOTE: deliberately NOT setting p.public_url
            return led

    from fanops import post as _post_pkg
    monkeypatch.setattr(_post_pkg, "get_poster", lambda cfg, backend: _GhostPoster(), raising=False)
    # The poster lookup in run.py is `from fanops.post import get_poster` — patch both surfaces
    from fanops.post import run as _run_mod
    monkeypatch.setattr(_run_mod, "get_poster", lambda cfg, backend: _GhostPoster(), raising=False)
    # _ensure_media's media-upload path also needs to be a no-op for the test
    monkeypatch.setattr(_run_mod, "_ensure_media", lambda *a, **kw: None, raising=False)

    _publish_one(cfg, "post_g", backend="dryrun")
    # The post MUST NOT end in published — it has no permalink
    led_after = Ledger.load(cfg)
    p = led_after.posts["post_g"]
    assert p.state is not PostState.published, (
        f"a poster that returns submitted-without-url MUST NOT result in published; "
        f"got state={p.state.value} (the D3 invariant would catch this at construction, but "
        f"_publish_one MUST also fail-closed BEFORE Pydantic so the operator sees a "
        f"needs_reconcile row, not a ValidationError 500)")
    assert p.state in (PostState.needs_reconcile, PostState.submitted), (
        f"the expected park-state is needs_reconcile (reconcile.py picks it up next pass); "
        f"got {p.state.value}")
    assert not p.published_at, (
        f"published_at MUST NOT be stamped without a permalink — the Posted tub's day-anchor "
        f"would lie; got published_at={p.published_at!r}")


# ───────────────────────────────────────────────────────────────────────────
# dryrun-boundary M1+M2 — the END-TO-END truth: a dryrun publish through the real chokepoint
# (publish_due) HOLDS the post `queued` and writes a would-send preview. It NEVER produces a
# published row (phantom or otherwise). (Supersedes the old D8 pin, which asserted the dryrun path
# produced Post(state=published, public_url='dryrun://…') — the ghost row the boundary eliminates.)
# ───────────────────────────────────────────────────────────────────────────

def test_end_to_end_dryrun_publish_holds_queued_with_preview(tmp_path, monkeypatch):
    """A dryrun advance through publish_due writes the would-send preview sidecar and leaves the
    post `queued` — the honest 'here's what WOULD ship, nothing was sent' state. No published row,
    no fabricated permalink: the phantom-published class is gone by construction, not by cleanup."""
    from fanops.post.run import publish_due

    monkeypatch.delenv("FANOPS_POSTER", raising=False)         # dryrun
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_e2e", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.queued,
        media_urls=["file:///clip_1_9x16.mp4"], scheduled_time="2020-01-01T00:00:00Z",   # due
    ))
    led.save()

    summary = publish_due(cfg)
    assert summary["published"] == 0                           # nothing entered distribution
    p = Ledger.load(cfg).posts["post_e2e"]
    assert p.state is PostState.queued                         # held at the boundary
    assert p.public_url is None                                # no phantom permalink
    assert (cfg.scheduled / "post_e2e.json").exists()          # the would-send preview WAS written


# ───────────────────────────────────────────────────────────────────────────
# D9 — actions.mark_published MUST require a non-empty URL (operator says "I posted by hand")
# ───────────────────────────────────────────────────────────────────────────

def test_mark_published_rejects_empty_url(tmp_path):
    """RED: mark_published(cfg, post_id, url='') -> error. Today url is Optional and an empty
    string passes through, producing the same ghost-row class as D1. The operator says
    'I posted by hand' — that MEANS they have a link; refuse the action without one."""
    from fanops.studio.actions import mark_published

    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_mp", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.queued,
    ))
    led.save()

    res = mark_published(cfg, "post_mp", url="")
    assert res.ok is False, (
        f"mark_published with empty url MUST be refused; got ok={res.ok} detail={res.detail!r}")
    assert "url" in (res.error or "").lower(), (
        f"the error MUST name the missing url so the operator knows what to fix; got {res.error!r}")


def test_mark_published_rejects_none_url(tmp_path):
    """RED: explicit None url also refused (today's default — the failure mode that produced
    ghost rows in the wild)."""
    from fanops.studio.actions import mark_published

    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_mp2", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.queued,
    ))
    led.save()

    res = mark_published(cfg, "post_mp2", url=None)
    assert res.ok is False
    assert "url" in (res.error or "").lower()


def test_mark_published_accepts_real_url(tmp_path):
    """RED (firewall): the happy path — operator pastes a real link, mark_published advances
    state=published and writes public_url."""
    from fanops.studio.actions import mark_published

    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_mp3", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.queued,
    ))
    led.save()

    res = mark_published(cfg, "post_mp3", url="https://www.instagram.com/p/abc/")
    assert res.ok is True, f"got error={res.error!r}"
    p = Ledger.load(cfg).posts["post_mp3"]
    assert p.state is PostState.published
    assert p.public_url == "https://www.instagram.com/p/abc/"


# ───────────────────────────────────────────────────────────────────────────
# D10 — cli `fanops resolve <id> published` MUST require --url
# ───────────────────────────────────────────────────────────────────────────

def test_cli_resolve_published_requires_url_flag(tmp_path, capsys):
    """RED: `fanops resolve <id> published` without --url MUST refuse. Today cli.py:633 sets
    state=PostState.published unconditionally — same ghost-row class via a third door."""
    from fanops.cli import cmd_resolve

    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_r", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.queued,
    ))
    led.save()

    import argparse
    args = argparse.Namespace(post_id="post_r", status="published", url=None)
    rc = cmd_resolve(cfg, args)
    assert rc != 0, (
        f"`fanops resolve <id> published` without --url MUST return a non-zero exit code; "
        f"got rc={rc} (the operator must be forced to provide the permalink)")
    # The state MUST stay queued — the bad resolve must NOT have mutated state
    p = Ledger.load(cfg).posts["post_r"]
    assert p.state is PostState.queued, (
        f"a failed resolve must not have side-effected state; got {p.state.value}")


def test_cli_resolve_published_with_url_succeeds(tmp_path):
    """RED (firewall): the happy path — `--url=...` lets the resolve succeed."""
    from fanops.cli import cmd_resolve

    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_r2", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.queued,
    ))
    led.save()

    import argparse
    args = argparse.Namespace(post_id="post_r2", status="published",
                              url="https://www.instagram.com/p/xyz/")
    rc = cmd_resolve(cfg, args)
    assert rc == 0
    p = Ledger.load(cfg).posts["post_r2"]
    assert p.state is PostState.published
    assert p.public_url == "https://www.instagram.com/p/xyz/"


def test_cli_resolve_to_non_published_does_not_require_url(tmp_path):
    """RED (firewall): resolving to failed / error / queued / etc. MUST NOT require --url —
    only published (and other terminal-with-URL states) gates on it."""
    from fanops.cli import cmd_resolve

    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@a", "account_id": "ig_a", "platforms": ["instagram"], "status": "active"}]}'
    )
    led = Ledger.load(cfg)
    clip = _seed_minimal_ledger(cfg)
    led.add_post(Post(
        id="post_r3", parent_id=clip.id, account="@a", account_id="ig_a",
        platform=Platform.instagram, caption="c", state=PostState.submitting,
    ))
    led.save()

    import argparse
    args = argparse.Namespace(post_id="post_r3", status="failed", url=None)
    rc = cmd_resolve(cfg, args)
    assert rc == 0, f"resolving to non-published with no url MUST work; got rc={rc}"
    p = Ledger.load(cfg).posts["post_r3"]
    assert p.state is PostState.failed


# dryrun-boundary M3: the two doctor-fix-ghosts tests are DELETED with the command they exercised.
# They pinned the pre-R1 ghost-row healer (back-fill 'dryrun://<id>' on a sidecar, else park
# needs_reconcile) — a migration that no longer exists. Post-boundary a ghost row is unconstructable
# (a dryrun post halts `queued`, never terminal-without-url), so there is nothing to heal.
