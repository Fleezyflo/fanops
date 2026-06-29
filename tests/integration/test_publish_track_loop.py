"""AUDIT C4 — the money loop must close in the SHIPPING (dryrun) backend.

No current test spans publish -> track, so the gap between what `DryRunPoster` produces
(a `published` post) and what `pull_metrics` requires to bind a metrics row (a non-null
`submission_id`) was invisible: the green suite hid a dead learning loop. This test drives the
REAL `publish_due` with the REAL `DryRunPoster` (no poster mock — that's the point), then runs
`pull_metrics` with an injected metrics row keyed on the synthetic dryrun submission_id, and
asserts the post reaches `analyzed`. Blotato is stood in for by the injected `list_posts`
(there is genuinely no Blotato in dryrun); production code never fabricates metrics."""
from __future__ import annotations
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.post.run import publish_due
from fanops.track import pull_metrics

pytestmark = pytest.mark.integration


def test_dryrun_published_post_reaches_analyzed(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)          # default backend = dryrun
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    # A due (past-scheduled) queued post with media already resolved, so publish_due needs no
    # ffmpeg/upload — it exercises exactly the publish->state path under test.
    led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="42",
                      platform=Platform.instagram, caption="hello", media_urls=["https://h/v.mp4"],
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued, public_url=f"dryrun://p1"))
    led.save()                                                  # persist for the self-loading publish_due

    # publish in dryrun (past schedule => due now): queued -> submitting -> submitted -> published
    publish_due(cfg)
    led = Ledger.load(cfg)
    p = led.posts["p1"]
    assert p.state is PostState.published
    assert p.submission_id == "dryrun_p1", "dryrun post must carry a trackable submission_id"

    # track: a metrics row keyed on the synthetic id binds and advances published -> analyzed
    rows = [{"postSubmissionId": "dryrun_p1",
             "metrics": {"saves": 30, "shares": 25, "retention": 0.8, "reach": 1000}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows)
    p = led.posts["p1"]
    assert p.state is PostState.analyzed, "C4: dryrun money loop must reach analyzed"
    assert p.metrics["saves"] == 30 and "lift_score" in p.metrics
