"""AUDIT C4 — the money loop must close: publish -> track -> analyzed.

No current test spans publish -> track, so the gap between what a poster produces (a `published`
post) and what `pull_metrics` requires to bind a metrics row (a non-null `submission_id`) was
invisible: the green suite hid a dead learning loop. This test drives the REAL `publish_due` on a
LIVE backend (a stub poster stands in for the network — the point is the publish->state path, not
a real HTTP call), then runs `pull_metrics` with an injected metrics row keyed on the post's
submission_id, and asserts the post reaches `analyzed`.

dryrun-boundary NOTE: post the boundary (M1), a DRYRUN post never enters distribution / reaches
`published` — so the money loop cannot be proven through the dryrun path. This test is therefore
LIVE. The dryrun-specific money loop (metrics bound by post_id, no fabricated `dryrun_` id) is
M2's redesign (DryRunPoster -> preview writer + local metrics binding) and gets its own test there."""
from __future__ import annotations
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.post.run import publish_due
from fanops.track import pull_metrics

pytestmark = pytest.mark.integration


def test_live_published_post_reaches_analyzed(tmp_path, monkeypatch, mocker):
    # LIVE backend so publish_due actually distributes (dryrun is held at the boundary). A stub poster
    # sets submitted + a real permalink; publish_due promotes it to published.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "42", "platforms": ["instagram"], "status": "active"}]}))
    # A due (past-scheduled) queued post with already-http media, so publish_due needs no ffmpeg/upload
    # — it exercises exactly the publish->state path under test.
    led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="42",
                      platform=Platform.instagram, caption="hello", media_urls=["https://h/v.mp4"],
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued))
    led.save()                                                  # persist for the self-loading publish_due

    import fanops.post.run as run
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id].state = PostState.submitted
            led_.posts[post_id].submission_id = "sub_p1"
            led_.posts[post_id].public_url = "https://www.instagram.com/reel/AAA/"   # real permalink -> published
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))

    # publish live (past schedule => due now): queued -> submitting -> submitted -> published
    publish_due(cfg)
    led = Ledger.load(cfg)
    p = led.posts["p1"]
    assert p.state is PostState.published
    assert p.submission_id == "sub_p1", "published post must carry a trackable submission_id"

    # track: a metrics row keyed on the submission_id binds and advances published -> analyzed
    rows = [{"postSubmissionId": "sub_p1",
             "metrics": {"saves": 30, "shares": 25, "retention": 0.8, "reach": 1000}}]
    led = pull_metrics(led, cfg, list_posts=lambda w: rows)
    p = led.posts["p1"]
    assert p.state is PostState.analyzed, "C4: the money loop must reach analyzed"
    assert p.metrics["saves"] == 30 and "lift_score" in p.metrics
