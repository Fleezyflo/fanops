# tests/test_reconcile.py
"""AUDIT H4 — reconcile posts stranded in `submitting` (crash mid-publish) or `needs_reconcile`
(ambiguous 5xx/timeout after the body was sent). The ONLY Blotato lookup is
GET /v2/posts/{postSubmissionId} (verified: returns status in-progress|failed|published|scheduled
+ publicUrl/errorMessage), which REQUIRES the submission id. So reconcile_posts polls only posts
that HAVE a submission_id; posts without one cannot be looked up via the API and stay parked for
human reconcile (the digest surfaces them). reconcile_posts:
  - status 'published'   -> PostState.published (+ public_url), so track can later measure it
  - status 'failed'      -> PostState.failed (definitely not live -> safe to re-queue)
  - 'in-progress'/'scheduled' -> leave as-is (not yet resolved)
  - no submission_id      -> skipped (cannot poll; human reconcile)
"""
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_posts


def _post(led, pid, state, sub=None):
    led.add_post(Post(id=pid, parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=state, submission_id=sub))


def test_reconcile_promotes_published(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="sub_1")
    def get_status(sid):
        return {"postSubmissionId": sid, "status": "published", "publicUrl": "https://ig.com/p/1"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p1"].public_url == "https://ig.com/p/1"


def test_reconcile_marks_failed_when_not_live(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p2", PostState.needs_reconcile, sub="sub_2")
    def get_status(sid):
        return {"postSubmissionId": sid, "status": "failed", "errorMessage": "platform rejected"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert led.posts["p2"].state is PostState.failed
    assert "platform rejected" in (led.posts["p2"].error_reason or "")


def test_reconcile_leaves_in_progress_parked(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p3", PostState.submitting, sub="sub_3")
    def get_status(sid):
        return {"postSubmissionId": sid, "status": "in-progress"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert led.posts["p3"].state is PostState.submitting   # unresolved -> untouched


def test_reconcile_skips_posts_without_submission_id(tmp_path):
    # The crux of H4: a submitting/needs_reconcile post with NO submission_id cannot be looked up
    # (GET requires the id). It must be SKIPPED (left for human reconcile), never guessed.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p4", PostState.needs_reconcile, sub=None)
    calls = []
    def get_status(sid):
        calls.append(sid); return {"status": "published"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert calls == []                                     # never polled (no id to poll by)
    assert led.posts["p4"].state is PostState.needs_reconcile   # still parked


def test_reconcile_ignores_terminal_and_queued_posts(tmp_path):
    # Only submitting/submitted/needs_reconcile are reconcilable. queued/published/analyzed/failed
    # must not be polled or changed.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, st in [("q", PostState.queued), ("pub", PostState.published),
                    ("an", PostState.analyzed), ("f", PostState.failed)]:
        _post(led, pid, st, sub=f"sub_{pid}")
    calls = []
    def get_status(sid):
        calls.append(sid); return {"status": "published"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert calls == []
    assert led.posts["q"].state is PostState.queued
    assert led.posts["pub"].state is PostState.published


def test_reconcile_durable_across_save(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p5", PostState.submitting, sub="sub_5")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "u"})
    led.save()
    again = Ledger.load(cfg)
    assert again.posts["p5"].state is PostState.published
