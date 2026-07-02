# tests/test_dryrun_boundary.py
# dryrun-boundary M1 (PRD Finding #1): a dryrun post is built, approved, scheduled — and then simply is
# NOT eligible to enter distribution, because there is no real backend to distribute it to. The boundary is
# enforced at the single chokepoint that already resolves the provider: publish_due claims a post ONLY when
# its resolved provider is a REAL backend. A dryrun post (cfg.is_live False -> provider "dryrun") stays
# `queued` — approved + scheduled + built, awaiting a backend that never comes. No new state, no fabricated
# submission_id/public_url, no threading.
#
# These tests drive the REAL chokepoint (publish_due), NOT DryRunPoster.publish directly (that poster
# contract is M2's). Pure-fixture: a not-live Config + a seeded due `queued` post.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.post.run import publish_due


def _cfg(tmp_path, monkeypatch):
    # NOT live: no FANOPS_LIVE, no live poster -> _post_provider returns "dryrun" for every post.
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    return Config(root=tmp_path)


def _due_queued_post(pid="p1", *, plat=Platform.instagram):
    # An approved (queued) post whose schedule is already due (past), so publish_due considers it.
    return Post(id=pid, parent_id="c1", account="@a", account_id="98432", platform=plat,
                caption="hello", media_urls=["file:///tmp/v.mp4"],
                scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued)


def test_dryrun_publish_due_leaves_queued(tmp_path, monkeypatch):
    # THE boundary: publish_due on a dryrun (not-live) system must leave an approved post `queued` — it is
    # built and scheduled, but has no live channel, so it never enters the distribution rail.
    cfg = _cfg(tmp_path, monkeypatch)
    led = Ledger.load(cfg)
    led.add_post(_due_queued_post("p1"))
    led.save()

    summary = publish_due(cfg)

    post = Ledger.load(cfg).posts["p1"]
    assert post.state is PostState.queued                       # NOT submitting/submitted/published
    assert summary["published"] == 0                            # nothing entered distribution


def test_dryrun_publish_due_mints_no_distribution_artifacts(tmp_path, monkeypatch):
    # The M1 boundary path must NOT fabricate the phantom-publish artifacts: no dryrun_ submission_id, no
    # dryrun:// public_url. (A dryrun post never reaches the poster, so nothing stamps them.)
    cfg = _cfg(tmp_path, monkeypatch)
    led = Ledger.load(cfg)
    led.add_post(_due_queued_post("p1"))
    led.save()

    publish_due(cfg)

    post = Ledger.load(cfg).posts["p1"]
    assert post.submission_id is None                           # no dryrun_<id> minted
    assert post.public_url is None                              # no dryrun://<id> minted
