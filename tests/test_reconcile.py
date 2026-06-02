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
import pytest
from fanops.config import Config
from fanops.errors import BlotatoAuthError
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


def test_reconcile_polls_a_client_token_post(tmp_path):
    # AUDIT H1: a post parked as needs_reconcile now ALWAYS carries a submission_id (the client
    # idempotency token stamped at crosspost), so reconcile can poll it via GET /v2/posts/:id and
    # resolve it automatically — no longer stranded for human-only reconcile. The token is the id
    # the poll is keyed by until/unless a real Blotato id overwrites it.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "pt", PostState.needs_reconcile, sub="fanops_deadbeefcafe")
    polled = []
    def get_status(sid):
        polled.append(sid)
        return {"postSubmissionId": sid, "status": "published", "publicUrl": "https://ig.com/p/tok"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert polled == ["fanops_deadbeefcafe"]               # the client token IS pollable
    assert led.posts["pt"].state is PostState.published
    assert led.posts["pt"].public_url == "https://ig.com/p/tok"

def test_reconcile_durable_across_save(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p5", PostState.submitting, sub="sub_5")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "u"})
    led.save()
    again = Ledger.load(cfg)
    assert again.posts["p5"].state is PostState.published


def test_reconcile_poll_error_on_one_post_does_not_abort_the_pass(tmp_path):
    # AUDIT H1 fallout: D1 stamps EVERY crossposted post with a CLIENT idempotency token
    # (submission_id = "fanops_..."), so a post parked in needs_reconcile after a PURE NETWORK
    # TIMEOUT carries a fanops_ token that is NOT a real Blotato postSubmissionId. Polling it against
    # the live API 404s -> BlotatoStatusClient.get_status raises RuntimeError. If that raise escapes
    # reconcile_posts, every genuinely-published post LATER in iteration order is never reconciled
    # and stays stuck. The fanops_ post is inserted FIRST so its poll error precedes the real-id
    # post in led.posts.values() order — the exact order that triggered the bug. The poll error must
    # be contained to that post (parked, NOT failed — it may be live) so the loop reaches the real id.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "tok", PostState.needs_reconcile, sub="fanops_deadbeef")   # FIRST in iteration order
    _post(led, "real", PostState.needs_reconcile, sub="sub_real")         # SECOND — must still resolve
    polled = []
    def get_status(sid):
        polled.append(sid)
        if sid.startswith("fanops_"):
            raise RuntimeError("blotato status 404: postSubmissionId not found")
        return {"postSubmissionId": sid, "status": "published", "publicUrl": "https://ig.com/p/real"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    # both were polled — the first post's error did NOT abort the pass before reaching the second
    assert polled == ["fanops_deadbeef", "sub_real"]
    # the fanops_ post is left PARKED (poll error is not evidence it failed — it may be live)
    assert led.posts["tok"].state is PostState.needs_reconcile
    assert led.posts["tok"].state is not PostState.failed       # MUST NOT guess it failed
    # the genuinely-published post is reconciled in the SAME pass despite the earlier error
    assert led.posts["real"].state is PostState.published
    assert led.posts["real"].public_url == "https://ig.com/p/real"


def test_reconcile_records_poll_error_reason_without_changing_state(tmp_path):
    # A contained poll error is surfaced for the digest via error_reason, but the state is untouched
    # (still parked) — recording the error must never be mistaken for resolving the post's fate.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "tok", PostState.submitting, sub="fanops_cafe")
    def get_status(sid):
        raise RuntimeError("blotato status 404: postSubmissionId not found")
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert led.posts["tok"].state is PostState.submitting       # unresolved -> untouched
    assert "404" in (led.posts["tok"].error_reason or "")       # error surfaced for the digest


def test_reconcile_logs_each_post(tmp_path):
    # Phase E4: a reconcile pass must leave an audit trail in run.log so a cron+mail/PagerDuty
    # monitor can see which parked posts were touched and how they resolved. Today reconcile_posts
    # emits NO log lines (no get_logger call), so cfg.log_path is never written. Seed one post that
    # resolves to 'published' and assert the run log records both the stage ('reconcile') and the
    # post id ('p1').
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="fanops_t")
    reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "u"})
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "reconcile" in log
    assert "p1" in log


def _reconcile_log_line_for(cfg, pid):
    # Return the single run.log line whose TAB-delimited unit_id field == pid, or "" if absent.
    # Matching the id positionally (not substring) prevents one post's keyword leaking into another's
    # assertion when several posts are reconciled in the same pass / same log file.
    if not cfg.log_path.exists():
        return ""
    for raw in cfg.log_path.read_text().splitlines():
        cols = raw.split("\t")            # get_logger writes "{ts}\t{stage}\t{unit_id}\t{outcome}"
        if len(cols) >= 4 and cols[1] == "reconcile" and cols[2] == pid:
            return raw
    return ""


def test_reconcile_logs_every_branch(tmp_path):
    # E4 HARDEN: test_reconcile_logs_each_post above drives ONLY the 'published' branch, so the
    # audit-log emit on the OTHER four branches (skipped-no-id / poll-error / failed / in-progress
    # 'left') is unpinned — deleting any of those log() calls keeps the suite green and a monitor
    # goes blind to the very residue a human must look at. Drive ALL of them in one pass and pin,
    # per post id, that a 'reconcile' line exists AND carries that branch's outcome keyword. Each
    # post gets a distinct id so the positional matcher binds each assertion to exactly one branch.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "noid", PostState.needs_reconcile, sub=None)          # (a) skipped: no submission_id
    _post(led, "boom", PostState.needs_reconcile, sub="fanops_x")    # (b) poll raises -> poll-error
    _post(led, "fail", PostState.needs_reconcile, sub="sub_fail")    # (c) status failed
    _post(led, "prog", PostState.submitting,      sub="sub_prog")    # (d) in-progress -> left

    def get_status(sid):
        if sid == "fanops_x":
            raise RuntimeError("blotato status 404: postSubmissionId not found")
        if sid == "sub_fail":
            return {"postSubmissionId": sid, "status": "failed", "errorMessage": "platform rejected"}
        if sid == "sub_prog":
            return {"postSubmissionId": sid, "status": "in-progress"}
        raise AssertionError(f"unexpected poll for {sid}")          # noid must NEVER be polled

    reconcile_posts(led, cfg, get_status=get_status)

    # (a) skipped-no-id: the id-less post is logged as skipped (THE branch the old test never bound).
    noid_line = _reconcile_log_line_for(cfg, "noid")
    assert noid_line, "no reconcile log line for the skipped-no-id post 'noid'"
    assert "skipped" in noid_line

    # (b) poll-error: a raising poll is contained AND audit-logged as poll-error (not silently parked).
    boom_line = _reconcile_log_line_for(cfg, "boom")
    assert boom_line, "no reconcile log line for the poll-error post 'boom'"
    assert "poll-error" in boom_line

    # (c) failed: a 'failed' resolution is audit-logged.
    fail_line = _reconcile_log_line_for(cfg, "fail")
    assert fail_line, "no reconcile log line for the failed post 'fail'"
    assert "failed" in fail_line

    # (d) left: an in-progress post left parked is still audit-logged (monitor sees it was visited).
    prog_line = _reconcile_log_line_for(cfg, "prog")
    assert prog_line, "no reconcile log line for the in-progress post 'prog'"
    assert "left" in prog_line


def test_reconcile_halts_on_fatal_auth_error(tmp_path):
    # Mirror publish_due (run.py:71-72): a Blotato auth failure means EVERY poll will 401, so
    # grinding through the whole ledger is pointless — a BlotatoAuthError from get_status propagates
    # (halt the pass) rather than being recorded per-post on every parked post. Distinct from a
    # per-post RuntimeError (a single 404), which is contained.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p", PostState.needs_reconcile, sub="sub_x")
    def get_status(sid):
        raise BlotatoAuthError("blotato status 401: bad key")
    with pytest.raises(BlotatoAuthError):
        reconcile_posts(led, cfg, get_status=get_status)
