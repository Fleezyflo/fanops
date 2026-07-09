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
from fanops.errors import PostizAuthError
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_posts


def _post(led, pid, state, sub=None):
    # R1: stamp a synthetic dryrun:// permalink when state is terminal-with-URL so the invariant
    # holds. Reconcile tests then exercise the reconciler's URL back-fill (real https) on top.
    from fanops.models import _POST_TERMINAL_REQUIRES_URL
    url = f"dryrun://{pid}" if state in _POST_TERMINAL_REQUIRES_URL else None
    led.add_post(Post(id=pid, parent_id="c", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=state, submission_id=sub,
                      public_url=url))


def test_reconcile_promotes_published(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="sub_1")
    def get_status(sid):
        return {"postSubmissionId": sid, "status": "published", "publicUrl": "https://ig.com/p/1"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p1"].public_url == "https://ig.com/p/1"


def test_reconcile_replaces_post_immutably_not_in_place(tmp_path):
    # immutability (the user's CRITICAL principle + the ledger's own set_*_state pattern): reconcile_posts
    # REPLACES led.posts[id] with a model_copy — it never mutates the existing Post object in place, so it is
    # safe even if Post is later frozen. The ledger holds the new object; the original reference is untouched.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="sub_1")
    orig = led.posts["p1"]
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "https://ig.com/p/1"})
    assert led.posts["p1"].state is PostState.published          # the ledger now holds the UPDATED post
    assert led.posts["p1"] is not orig                            # ...as a NEW object (immutable update)
    assert orig.state is PostState.needs_reconcile                # the ORIGINAL object is untouched
    assert orig.public_url is None


def test_reconcile_stamps_stuck_breadcrumb_past_schedule(tmp_path):
    # H4: a post stuck 'scheduled'/unknown long past its schedule gets an age breadcrumb in error_reason so
    # it surfaces (instead of silently looping). State is NOT changed — the post's fate is never guessed.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="ps", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="s1",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "scheduled"})
    p = led.posts["ps"]
    assert p.state is PostState.needs_reconcile and p.error_reason and "stuck" in p.error_reason.lower()


def test_reconcile_no_stuck_breadcrumb_when_recent(tmp_path):
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pr", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="s1",
                      scheduled_time=datetime.now(timezone.utc).isoformat(), public_url="dryrun://pr"))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "scheduled"})
    assert led.posts["pr"].error_reason is None              # recent -> no premature stuck breadcrumb


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
    # R1: a malformed publicUrl ("u") fails safe_public_url AND triggers the published_no_url_parked
    # branch (R1/D2 fail-closed). Pass a real https URL for the durability assertion.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p5", PostState.submitting, sub="sub_5")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published",
                                                            "publicUrl": "https://insta/p/abc"})
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
    # Return the single run.log line whose unit_id == pid, or "" if absent.
    # Matching the id field (not substring) prevents one post's keyword leaking into another's
    # assertion when several posts are reconciled in the same pass / same log file.
    import json
    if not cfg.log_path.exists():
        return ""
    for raw in cfg.log_path.read_text().splitlines():
        try:
            rec = json.loads(raw)
            if rec.get("stage") == "reconcile" and rec.get("unit_id") == pid:
                return raw
        except json.JSONDecodeError:
            cols = raw.split("\t")        # legacy TAB layout
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
    # Mirror publish_due (run.py:71-72): a poster auth failure means EVERY poll will 401, so
    # grinding through the whole ledger is pointless — an AuthError from get_status propagates
    # (halt the pass) rather than being recorded per-post on every parked post. Distinct from a
    # per-post RuntimeError (a single 404), which is contained. Type-matched on the AuthError BASE
    # (the halt is backend-agnostic — PostizAuthError stands in now Blotato is gone).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p", PostState.needs_reconcile, sub="sub_x")
    def get_status(sid):
        raise PostizAuthError("postiz status 401: bad key")
    with pytest.raises(PostizAuthError):
        reconcile_posts(led, cfg, get_status=get_status)


# ---- P2 Task 4: backend dispatch in _default_get_status + widened AuthError halt ----
class _R:
    def __init__(s, c, b): s.status_code = c; s._b = b; s.text = str(b)
    def json(s): return s._b

def _postiz_env(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)

def test_status_client_unknown_backend_fails_closed(tmp_path):
    # Blotato removed: the else-branch that returned BlotatoStatusClient now RAISES (fail-closed +
    # legible) — an unknown backend must never silently construct a status poller. A stale
    # FANOPS_POSTER=rest already degrades to dryrun at cfg (W4), so this raise is reachable only via
    # a direct unknown backend.
    from fanops.reconcile import _status_client_for
    with pytest.raises(ValueError, match="unknown backend"):
        _status_client_for(Config(root=tmp_path), "rest", None)

def test_default_get_status_postiz_resolves_end_to_end_with_date_window(tmp_path, monkeypatch, mocker):
    # postiz + key: a parked Postiz post resolves end-to-end through the UNCHANGED reconcile_posts via
    # the Postiz list read (proves dispatch without closure introspection), AND the closure passes the
    # post's own scheduled_time so the startDate/endDate window brackets a future/2099 post (FOUND, not
    # "unknown"), capturing its real IG permalink from the row's releaseURL.
    _postiz_env(monkeypatch)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "pp", PostState.needs_reconcile, sub="postiz_99")
    led.posts["pp"].scheduled_time = "2099-01-01T00:00:00Z"
    url = "https://www.instagram.com/reel/DZvZ8Itkaxz/"
    page = {"posts": [{"id": "postiz_99", "state": "PUBLISHED", "releaseURL": url, "publishDate": "2099-01-01T00:00:00.000Z"}]}
    captured = {}
    def fake_get(url_, **kw):
        captured["params"] = kw.get("params"); return _R(200, page)
    mocker.patch("fanops.post.metrics.requests.get", side_effect=fake_get)
    led = reconcile_posts(led, cfg)                # NO injected get_status → exercises _default_get_status(postiz)
    assert led.posts["pp"].state is PostState.published
    assert led.posts["pp"].public_url == url                          # releaseURL flowed through reconcile
    p = captured["params"] or {}
    assert "date" not in p and p["startDate"] <= "2099-01-01" <= p["endDate"]   # window brackets scheduled_time

def test_reconcile_postiz_persists_ig_media_id_from_releaseId(tmp_path, monkeypatch, mocker):
    # MOL-112 foundation: reconcile stamps media_id from the Postiz row's releaseId at promote time — the IG
    # object id is captured at source, not inferred later by permalink feed-matching.
    _postiz_env(monkeypatch)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "pp", PostState.needs_reconcile, sub="postiz_99")
    url = "https://www.instagram.com/reel/DZvZ8Itkaxz/"
    rid = "17841456789012345"
    page = {"posts": [{"id": "postiz_99", "state": "PUBLISHED", "releaseURL": url, "releaseId": rid,
                       "publishDate": "2099-01-01T00:00:00.000Z"}]}
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, page))
    led = reconcile_posts(led, cfg)
    assert led.posts["pp"].state is PostState.published
    assert led.posts["pp"].media_id == rid

def test_reconcile_poll_error_log_carries_the_error_detail(tmp_path):
    # OBSERVABILITY: a persistent reconcile failure (API shape change, 404-on-every-token) must be
    # diagnosable from the log STREAM, not only by loading the ledger and reading each post's
    # error_reason. The poll-error log line must carry the err= detail.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "pp", PostState.needs_reconcile, sub="sub_1")
    def boom(sid): raise RuntimeError("connreset SENTINEL-ERR")
    reconcile_posts(led, cfg, get_status=boom)
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "poll-error" in log and "SENTINEL-ERR" in log          # the error detail rides the log line

def test_reconcile_halts_on_postiz_auth_error(tmp_path):
    # The widened auth-halt catch (BlotatoAuthError → the shared AuthError base): a Postiz 401 in the
    # status poll must ALSO halt the pass (not grind a bogus error onto every parked post). Before the
    # widen, PostizAuthError (a sibling of BlotatoAuthError, not a subclass) slipped to the per-post
    # contain branch and never propagated.
    from fanops.errors import PostizAuthError
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p", PostState.needs_reconcile, sub="sub_x")
    def get_status(sid):
        raise PostizAuthError("Postiz 401 — bad key (body withheld)")
    with pytest.raises(PostizAuthError):
        reconcile_posts(led, cfg, get_status=get_status)


def test_reconcile_published_captures_real_id_over_fanops_token(tmp_path):
    # CULM-3: a post recovered to published via reconcile must capture the REAL backend id, replacing the
    # birth fanops_ idempotency token (which analytics 404s) — else pull_metrics can never attribute it.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="fanops_deadbeef")
    info = {"postSubmissionId": "blotato_99", "status": "published", "publicUrl": "https://ig.com/p/1"}
    led = reconcile_posts(led, cfg, get_status=lambda sid: info)
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p1"].submission_id == "blotato_99"          # real id captured

def test_reconcile_published_without_real_id_keeps_token_not_none(tmp_path):
    # No real id in the poll body -> never overwrite the (pollable) token with None.
    # R1: a 'published' status with NO publicUrl now parks in needs_reconcile (the fail-closed
    # gate keeps the post pollable on the next pass instead of promoting to a ghost row).
    # Provide a real publicUrl so the original assertion (token preserved across the promotion)
    # still holds.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="fanops_deadbeef")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published",
                                                            "publicUrl": "https://insta/p/keep"})
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p1"].submission_id == "fanops_deadbeef"     # NOT overwritten by None

def test_reconcile_published_post_is_archived(tmp_path):
    # CULM-Q3: a reconcile-recovered published post must land in the day-bucketed Posted archive too.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="blotato_7")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "https://ig/p/7"})
    assert list(cfg.published.rglob("p1.json")), "reconcile-recovered published post must be archived"


# ---- WS-R1 XC-1/XC-2/XC-6: bounded escalation out of submit/reconcile limbo ----------------------

def test_submitting_escalate_to_needs_reconcile_past_deadline_with_fake_token(tmp_path):
    # XC-1: a `submitting` post crash-stranded >24h past schedule on a never-real fanops_ token escalates to
    # needs_reconcile (the digest reconcile column owns it). State CHANGES; never to a re-queueable `failed`.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="ps", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="fanops_abc",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "in-progress"})
    p = led.posts["ps"]
    assert p.state is PostState.needs_reconcile
    assert "escalated" in (p.error_reason or "")


def test_submitting_not_escalated_when_fresh(tmp_path):
    # A submitting post only a few hours past schedule is left untouched (slow submit, not crash-stranded).
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pf", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="fanops_abc",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "in-progress"})
    assert led.posts["pf"].state is PostState.submitting


def test_submitting_not_escalated_with_real_token(tmp_path):
    # A submitting post >24h past schedule but carrying a REAL backend id is NOT escalated — its poll resolves.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pr", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="blotato_REAL_1",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "in-progress"})
    assert led.posts["pr"].state is PostState.submitting     # real token -> left to poll, never escalated


def test_needs_reconcile_terminal_giveup_past_long_bound(tmp_path):
    # XC-2: a needs_reconcile post >72h past schedule on a never-real fanops_ token reaches the explicit
    # GAVE UP terminal marker — it stays needs_reconcile (NOT failed: re-queue would double-post a maybe-live
    # post) but is labeled terminal and no longer polled.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pg", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="fanops_abc",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "unknown"})
    p = led.posts["pg"]
    assert p.state is PostState.needs_reconcile          # NOT failed (re-queueable) — stays may-be-live
    assert (p.error_reason or "").startswith("GAVE UP:")


def test_giveup_post_is_not_polled_again(tmp_path):
    # A give-up post is a labeled terminal: the next pass must NOT poll it (dead token) nor re-stamp it.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pg", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="fanops_abc",
                      error_reason="GAVE UP: unresolved 80h past schedule on a never-real token — ...", public_url="dryrun://pg"))
    calls = []
    def get_status(sid):
        calls.append(sid); return {"status": "published"}
    before = led.posts["pg"].error_reason
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert calls == []                                   # never polled
    assert led.posts["pg"].error_reason == before        # never re-stamped
    assert led.posts["pg"].state is PostState.needs_reconcile


def test_needs_reconcile_real_token_keeps_polling_past_long_bound(tmp_path):
    # A REAL-token needs_reconcile post is NEVER given up — a real id can still resolve at a later pass.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pr", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="blotato_REAL_1",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "in-progress"})
    assert not (led.posts["pr"].error_reason or "").startswith("GAVE UP:")   # real token -> never abandoned


def test_breadcrumb_dedup_logged_once_not_every_pass(tmp_path, mocker):
    # XC-6: a permanently-parked post stamps its stuck breadcrumb + logs "left:" ONCE, not on every pass.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pk", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="fanops_abc",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()))
    spy = []
    def fake_logger(cfg):
        def log(*a, **k): spy.append(a)
        return log
    mocker.patch("fanops.reconcile.get_logger", fake_logger)
    def gs(sid): return {"status": "scheduled"}
    led = reconcile_posts(led, cfg, get_status=gs)          # pass 1: stamps + logs "left:"
    first = [a for a in spy if len(a) >= 3 and "left:" in str(a[2])]
    led = reconcile_posts(led, cfg, get_status=gs)          # pass 2: reason already set -> no "left:" line
    second = [a for a in spy if len(a) >= 3 and "left:" in str(a[2])]
    assert len(first) == 1
    assert len(second) == len(first)                        # no additional "left:" line on the second pass
    assert led.posts["pk"].error_reason and "stuck" in led.posts["pk"].error_reason.lower()


# ---- Sprint 4: heal crash-stranded submitting (no submission_id) ----
def test_heal_stranded_submitting_no_sid_back_to_queued(tmp_path):
    from datetime import datetime, timezone, timedelta
    from fanops.reconcile import heal_stranded_submitting
    from fanops.timeutil import iso_z
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    old = iso_z(datetime.now(timezone.utc) - timedelta(hours=2))
    led.add_post(Post(id="stuck", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, scheduled_time=old, submission_id=None))
    led.save()
    assert heal_stranded_submitting(cfg) == 1
    assert Ledger.load(cfg).posts["stuck"].state is PostState.queued


def test_heal_submitting_with_real_sid_unchanged(tmp_path):
    from datetime import datetime, timezone, timedelta
    from fanops.reconcile import heal_stranded_submitting
    from fanops.timeutil import iso_z
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    old = iso_z(datetime.now(timezone.utc) - timedelta(hours=2))
    led.add_post(Post(id="real", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, scheduled_time=old, submission_id="cmqz_real_abc"))
    led.save()
    assert heal_stranded_submitting(cfg) == 0
    assert Ledger.load(cfg).posts["real"].state is PostState.submitting
