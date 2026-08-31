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
import itertools

import pytest
from fanops.config import Config
from fanops.errors import PostizAuthError
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.reconcile import reconcile_due, reconcile_posts


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


def test_an_unresolved_observation_leaves_the_row_byte_identical(tmp_path):
    # The `stuck …` breadcrumb is GONE, at EVERY age. A post the backend has not settled is LATE, not
    # failed, and lateness is DERIVED by the digest from the row and the schedule on every read — a
    # stamped "stuck 12h" is wrong an hour later, and while it existed it doubled as the do-not-look-again
    # latch that made a strand silent. So an unresolved observation writes nothing at all: both a 12h-late
    # post and a fresh one come out of the pass byte-for-byte as they went in.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, hours in (("late", 12), ("fresh", 0)):
        led.add_post(Post(id=pid, parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.needs_reconcile, submission_id=f"s_{pid}",
                          scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()))
    before = {pid: led.posts[pid].model_dump() for pid in ("late", "fresh")}
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "scheduled"})
    for pid in ("late", "fresh"):
        assert led.posts[pid].model_dump() == before[pid], f"{pid} was rewritten by an unresolved observation"


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


def test_reconcile_does_not_poll_a_client_token_post(tmp_path):
    # fanops_ is a birth idempotency token, not a Zernio/Postiz row id.
    # GET /posts/fanops_* 400s (Invalid post ID) and can never resolve the row.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "pt", PostState.needs_reconcile, sub="fanops_deadbeefcafe")
    polled = []
    def get_status(sid):
        polled.append(sid)
        return {"postSubmissionId": sid, "status": "published", "publicUrl": "https://ig.com/p/tok"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert polled == []
    assert led.posts["pt"].state is PostState.needs_reconcile

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
    # A fanops_ birth token is not a GET key. Inserted FIRST in iteration order so a skip-bug that
    # still GET+raises would abort before the real-id post. The real id must still resolve.
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
    assert polled == ["sub_real"]
    assert led.posts["tok"].state is PostState.needs_reconcile
    assert led.posts["tok"].state is not PostState.failed       # MUST NOT guess it failed
    # the genuinely-published post is reconciled in the SAME pass despite the earlier error
    assert led.posts["real"].state is PostState.published
    assert led.posts["real"].public_url == "https://ig.com/p/real"


def test_reconcile_read_error_writes_nothing_and_only_logs(tmp_path):
    # A contained read error is not evidence ABOUT the post — it is evidence about the network. It buys a
    # log line and nothing else. The `reconcile poll error: …` stamp this used to write went into
    # error_reason, a field three substring parsers read, and it was also the latch that suppressed the
    # post from every later pass: the breadcrumb that said "stuck" was the mechanism that kept it stuck.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "tok", PostState.submitting, sub="sub_cafe")
    before = led.posts["tok"].model_dump()
    def get_status(sid):
        raise RuntimeError("postiz status 404: postSubmissionId not found")
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert led.posts["tok"].model_dump() == before               # the row is untouched, error_reason included
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "poll-error" in log and "404" in log                  # the detail rides the log stream only


def test_reconcile_logs_each_post(tmp_path):
    # Phase E4: a reconcile pass must leave an audit trail in run.log so a cron+mail/PagerDuty
    # monitor can see which parked posts were touched and how they resolved. Today reconcile_posts
    # emits NO log lines (no get_logger call), so cfg.log_path is never written. Seed one post that
    # resolves to 'published' and assert the run log records both the stage ('reconcile') and the
    # post id ('p1').
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="sub_t")
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
    _post(led, "boom", PostState.needs_reconcile, sub="sub_boom")    # (b) poll raises -> poll-error
    _post(led, "fail", PostState.needs_reconcile, sub="sub_fail")    # (c) status failed
    _post(led, "prog", PostState.submitting,      sub="sub_prog")    # (d) in-progress -> left
    _post(led, "tok", PostState.needs_reconcile, sub="fanops_x")     # birth token: never GET

    def get_status(sid):
        if sid.startswith("fanops_"):
            raise AssertionError(f"must not GET a birth token: {sid}")
        if sid == "sub_boom":
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

    # birth token: visited for the age ladder, never GET, never poll-error.
    tok_line = _reconcile_log_line_for(cfg, "tok")
    assert tok_line, "no reconcile log line for the client-token post 'tok'"
    assert "skipped" in tok_line and "poll-error" not in tok_line


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
    # An unknown backend must never silently construct a status poller. Message names only the
    # backends `_status_client_for` can serve (zernio). A stale FANOPS_POSTER=rest already degrades
    # to dryrun at cfg (W4), so this raise is reachable only via a direct unknown backend.
    from fanops.reconcile import _status_client_for
    with pytest.raises(ValueError, match=r"expected zernio"):
        _status_client_for(Config(root=tmp_path), "rest", None)

def test_status_client_postiz_is_not_a_poller(tmp_path):
    # MOL-820: Postiz is mirror-only; the per-post arm is gone.
    from fanops.reconcile import _status_client_for
    with pytest.raises(ValueError, match=r"expected zernio"):
        _status_client_for(Config(root=tmp_path), "postiz", None)

def test_reconcile_postiz_persists_ig_media_id_from_releaseId(tmp_path, monkeypatch, mocker):
    # MOL-112 foundation: reconcile stamps media_id from the Postiz row's releaseId at promote time —
    # via the mirror observation (MOL-820 deleted the per-post get_status path).
    _postiz_env(monkeypatch)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "pp", PostState.needs_reconcile, sub="postiz_99")
    url = "https://www.instagram.com/reel/DZvZ8Itkaxz/"
    rid = "17841456789012345"
    mirror = {"postiz_99": {"status": "published", "publicUrl": url, "releaseId": rid, "postiz_state": "PUBLISHED"}}
    led = reconcile_posts(led, cfg, mirror=mirror,
                          get_status=lambda sid: (_ for _ in ()).throw(AssertionError("postiz must not poll")))
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
    # A fanops_ birth token is not a GET key, so a published poll body cannot overwrite it.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="fanops_deadbeef")
    polled = []
    info = {"postSubmissionId": "blotato_99", "status": "published", "publicUrl": "https://ig.com/p/1"}
    def get_status(sid):
        polled.append(sid)
        return info
    led = reconcile_posts(led, cfg, get_status=get_status)
    assert polled == []
    assert led.posts["p1"].state is PostState.needs_reconcile
    assert led.posts["p1"].submission_id == "fanops_deadbeef"

def test_reconcile_published_without_real_id_keeps_token_not_none(tmp_path):
    # No real id in the poll body -> never overwrite the existing real backend id with None.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="blotato_deadbeef")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published",
                                                            "publicUrl": "https://insta/p/keep"})
    assert led.posts["p1"].state is PostState.published
    assert led.posts["p1"].submission_id == "blotato_deadbeef"     # NOT overwritten by None

def test_reconcile_published_post_is_archived(tmp_path):
    # CULM-Q3: a reconcile-recovered published post must land in the day-bucketed Posted archive too.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _post(led, "p1", PostState.needs_reconcile, sub="blotato_7")
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": "https://ig/p/7"})
    assert list(cfg.published.rglob("p1.json")), "reconcile-recovered published post must be archived"


# ---- WS-R1 XC-1/XC-2/XC-6: bounded escalation out of submit/reconcile limbo ----------------------

def test_submitting_escalate_to_needs_reconcile_past_deadline_with_fake_token(tmp_path):
    # I5: a `submitting` post crash-stranded >24h on a never-real fanops_ token cannot be polled.
    # It leaves inflight as failed/unknown — never needs_reconcile (would stay inflight forever)
    # and never transient (daemon would auto-retry a fanops_ id). Never GET.
    from datetime import datetime, timezone, timedelta
    from fanops.models import ErrorKind
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="ps", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="fanops_abc",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()))
    polled = []
    led = reconcile_posts(led, cfg, get_status=lambda sid: polled.append(sid) or {"status": "in-progress"})
    p = led.posts["ps"]
    assert polled == []                                          # age ladder must not depend on a GET
    assert p.state is PostState.failed
    assert p.state is not PostState.needs_reconcile
    assert p.error_kind is ErrorKind.unknown
    assert "unpollable" in (p.error_reason or "")


def test_submitting_not_escalated_when_fresh(tmp_path):
    # A submitting post only a few hours past schedule is left untouched (slow submit, not crash-stranded).
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pf", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="fanops_abc",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()))
    polled = []
    led = reconcile_posts(led, cfg, get_status=lambda sid: polled.append(sid) or {"status": "in-progress"})
    assert polled == []
    assert led.posts["pf"].state is PostState.submitting


def test_submitting_real_token_ALSO_escalates_past_deadline(tmp_path):
    # RC-2/S04: a submitting post >24h past schedule escalates REGARDLESS of token provenance. The old code
    # left a REAL-token post submitting forever on the false assumption 'its status WILL resolve'
    # (reconcile.py:76) — false when the platform deleted the post or the integration was removed. PD-2 (a):
    # the SAME age ladder applies to a real id. (Escalation moves it to needs_reconcile — still polled, NOT
    # a re-queueable state, so it cannot double-post.) This INVERTS the pre-S04 characterization test.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pr", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="blotato_REAL_1",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "in-progress"})
    assert led.posts["pr"].state is PostState.needs_reconcile     # real token -> ALSO escalates (RC-2)
    assert "escalated" in (led.posts["pr"].error_reason or "")


def test_no_age_makes_an_unresolved_post_terminal(tmp_path):
    # I5 exception to "waiting is not failing": a fanops_ birth token cannot be polled, so past 24h
    # (here 80h) it leaves inflight as failed/unknown. A real backend id at this age is still
    # untouched (test_a_real_token_past_the_old_horizon_is_also_untouched). Never GET.
    from datetime import datetime, timezone, timedelta
    from fanops.models import ErrorKind
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pg", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="fanops_abc",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()))
    polled = []
    led = reconcile_posts(led, cfg, get_status=lambda sid: polled.append(sid) or {"status": "unknown"})
    p = led.posts["pg"]
    assert polled == []
    assert p.state is PostState.failed
    assert p.state is not PostState.needs_reconcile
    assert p.error_kind is ErrorKind.unknown
    assert "unpollable" in (p.error_reason or "")


@pytest.mark.parametrize("legacy", [
    "reconcile poll error: connreset SENTINEL",                    # the deleted transient stamp
    "stuck unknown ~80h past schedule — check the channel",        # the deleted lateness breadcrumb
    "unresolved 96h past schedule; verify on the channel manually",  # the deleted terminal label
])
def test_no_legacy_reason_latches_a_post_out_of_the_pass(tmp_path, legacy):
    # THE DISPOSAL PROOF for every row the deleted machinery already wrote. Those rows are still in the
    # live ledger and no operator step exists to clear them; the labeled ones were skipped at the loop head
    # FOREVER, so the label was its own latch and an outage the backend had long since resolved could never
    # be observed away. No value of error_reason is read by this module any more, so the first pass that
    # sees the row PUBLISHED promotes the post — permalink and media_id included — and clears the stale
    # reason. (The exact deleted sentinel is deliberately not reproduced here: the acceptance grep for its
    # vocabulary must come back empty, and the property proven is the stronger one — NO string latches.)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    url, rid = "https://www.instagram.com/reel/DZvZ8Itkaxz/", "17841456789012345"
    led.add_post(Post(id="pg", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="postiz_real_1",
                      error_reason=legacy, public_url="dryrun://pg"))
    calls = []
    def get_status(sid):
        calls.append(sid); return {"status": "published", "publicUrl": url, "releaseId": rid}
    led = reconcile_posts(led, cfg, get_status=get_status)
    p = led.posts["pg"]
    assert calls == ["postiz_real_1"]                    # visited — the reason no longer suppresses it
    assert p.state is PostState.published
    assert p.public_url == url and p.media_id == rid     # promotion carries permalink + media_id, same pass
    assert p.error_reason is None                        # the stale reason does not survive the resolution


def test_a_real_token_past_the_old_horizon_is_also_untouched(tmp_path):
    # The other half of the inversion, on the axis the OLD ladder was widened over: token provenance. A
    # real backend id 80h past schedule was given up exactly like a fake one. Now neither is: the token
    # kind never mattered to the question, and the question is no longer asked by an age.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pr", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="postiz_REAL_1",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()))
    before = led.posts["pr"].model_dump()
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "in-progress"})
    assert led.posts["pr"].model_dump() == before


# ── S04 / RC-2: the terminal ladder is a pure function of (state, age) ───────────────────────────
@pytest.mark.parametrize("backend,poll,token,reason", list(itertools.product(
    ["postiz", "zernio"],
    ["published", "failed", "unknown", "raises"],
    ["fanops_FAKE", "blotato_REAL"],
    ["", "stuck 9h past schedule — check the channel"])))
def test_terminal_ladder_matrix(tmp_path, backend, poll, token, reason):
    # THE 32-CELL INVARIANT, inverted. A needs_reconcile post 73h past schedule — past the horizon at which
    # the deleted ladder declared it lost — is decided by the OBSERVATION and by nothing else, across every
    # (backend × observation × token × error_reason). A published/failed answer resolves it; an unknown one,
    # or a read that raised, leaves the ledger row BYTE-IDENTICAL. The three axes that could once veto the
    # outcome (a raising read, a real token, a stale reason) still never do — but now what they cannot veto
    # is a NON-write, so no cell can invent a verdict the backend never gave.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="m", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id=token,
                      error_reason=(reason or None),
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()))
    before = led.posts["m"].model_dump()
    polled = []

    def get_status(sid):
        polled.append(sid)
        if poll == "raises": raise RuntimeError(f"{backend} 404")
        if poll == "published": return {"status": "published", "publicUrl": "https://x/p/1", "postSubmissionId": "postiz_REAL"}
        if poll == "failed": return {"status": "failed", "errorMessage": "rejected"}
        return {"status": "unknown"}
    led = reconcile_posts(led, cfg, get_status=get_status)
    p = led.posts["m"]
    assert p.state is not PostState.submitting              # NEVER stranded — the point of the fix, in every cell
    if token.startswith("fanops_"):
        assert polled == []                                 # birth token is not a GET key
        assert p.state is PostState.failed                  # I5: unpollable past 24h leaves inflight
        assert p.state is not PostState.needs_reconcile
        from fanops.models import ErrorKind
        assert p.error_kind is ErrorKind.unknown            # never transient — daemon must not auto-retry
        assert "unpollable" in (p.error_reason or "")
    elif poll == "published":
        assert polled == [token]
        assert p.state is PostState.published               # the observation RESOLVES it — never discarded
    elif poll == "failed":
        assert polled == [token]
        assert p.state is PostState.failed
    else:                                                    # unknown / raises -> no observation to act on...
        assert polled == [token]
        assert p.model_dump() == before                      # ...so ZERO ledger bytes, in every one of the cells
        assert p.state is not PostState.failed               # ...and never GUESSED re-queueable (double-post)


def test_needs_reconcile_poll_promotes_within_the_window_before_giveup(tmp_path):
    # Ladder does not blind the reconciler: a needs_reconcile post WITHIN the give-up window (age < 72h) is
    # still POLLED, and a published poll promotes it. The terminal only pre-empts the poll ONCE past 72h — so
    # a genuine resolution before the deadline is never discarded (this is why escalation at 24h -> the still-
    # polled needs_reconcile column recovers a real post in the 24h..72h window).
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pw", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="blotato_REAL",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()))
    led = reconcile_posts(led, cfg, get_status=lambda sid: {
        "postSubmissionId": "blotato_REAL", "status": "published", "publicUrl": "https://ig.com/p/x"})
    assert led.posts["pw"].state is PostState.published


def test_an_unresolved_observation_never_writes_a_re_queueable_state(tmp_path):
    # PRESERVATION — the double-post safety the whole design rests on, restated for the mirror. `failed` is
    # RE-QUEUEABLE (_requeue_transient_failed_for_daemon reads posts_in_state(failed)), so anything able to
    # write it from an ABSENCE of evidence licences a second publish of a post that may well be live. An
    # unresolved observation therefore writes no state at all — not `failed`, not a label, nothing.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pl", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="blotato_REAL",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()))
    before = led.posts["pl"].model_dump()
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "unknown"})
    p = led.posts["pl"]
    assert p.state is PostState.needs_reconcile              # state UNCHANGED
    assert p.state is not PostState.failed                   # explicitly NOT re-queueable
    assert p.model_dump() == before                          # and not a byte of anything else, either


def test_reconcile_visits_a_post_carrying_a_transient_reason(tmp_path, mocker):
    # A post carrying a pre-existing reason must STILL be visited — the any-non-empty-error_reason latch made
    # it silent from pass one. It is visited AND its reason is left alone: the visit no longer overwrites the
    # field, because nothing in this pass has anything true to say about why the post is unresolved.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    reason = "reconcile poll error: transient boom"
    led.add_post(Post(id="pt", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="fanops_x",
                      error_reason=reason,
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()))
    spy = []
    def fake_logger(cfg):
        def log(*a, **k): spy.append(a)
        return log
    mocker.patch("fanops.reconcile.get_logger", fake_logger)
    polled = []
    led = reconcile_posts(led, cfg, get_status=lambda sid: polled.append(sid) or {"status": "scheduled"})
    assert polled == []
    assert [a for a in spy if len(a) >= 3 and "client token is not a backend id" in str(a[2])], \
        "a transient-reason client-token post was NOT visited"
    assert led.posts["pt"].error_reason == reason                    # visited, and NOT re-stamped


def test_reconcile_never_guesses_a_fate_on_error(tmp_path):
    # CONTRACT — the prime directive. A RAISING poll is not evidence a post failed/published. Within the
    # deadlines, such a post is PARKED (state untouched), never guessed into a terminal.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pu", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="blotato_REAL",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()))
    def raises(sid): raise RuntimeError("boom")
    led = reconcile_posts(led, cfg, get_status=raises)
    assert led.posts["pu"].state is PostState.needs_reconcile          # parked, NOT guessed
    assert led.posts["pu"].state not in (PostState.failed, PostState.published)


def test_report_terminals_previews_the_escalation_and_the_lateness_and_writes_nothing(tmp_path):
    # MOL-791: the preview carries TWO row kinds in ONE shape, told apart by would_set_state vs state.
    #   esc   — 30h `submitting`, CLIENT token: I5 unpollable close fires (would_set_state MOVES to
    #           failed). No lateness row: a fanops_ token can never match a backend row.
    #   old   — 80h `needs_reconcile`, REAL id, never mirrored: the deleted give-up rung would have
    #           declared it lost; now it previews as LATENESS ONLY (would_set_state == state).
    #   fresh — 2h `submitting`, client token: previews nothing at all.
    # And, as before, the whole call WRITES NOTHING.
    from datetime import datetime, timezone, timedelta
    from fanops.reconcile import report_terminals
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="esc", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="fanops_y",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()))
    led.add_post(Post(id="old", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="blotato_REAL",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()))
    led.add_post(Post(id="fresh", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="fanops_z",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()))
    rows = report_terminals(led)
    writes = [r for r in rows if r["would_set_state"] != r["state"]]
    late = [r for r in rows if r["would_set_state"] == r["state"]]
    assert [r["post_id"] for r in writes] == ["esc"]          # the unpollable close, and only it, would write
    assert writes[0]["would_set_state"] == "failed" and "unpollable" in writes[0]["reason"]
    assert [r["post_id"] for r in late] == ["old"]            # esc (client token) + fresh (on time) are silent
    assert late[0]["state"] == "needs_reconcile" and late[0]["event"] == "note lateness"
    assert "80h past scheduled_time" in late[0]["reason"] and "never mirrored" in late[0]["reason"]
    assert set(late[0]) == set(writes[0])                     # ONE row shape — cli.py's loop renders both
    assert led.posts["esc"].error_reason is None              # WROTE NOTHING — pure preview
    assert led.posts["esc"].state is PostState.submitting
    assert led.posts["old"].error_reason is None and led.posts["old"].state is PostState.needs_reconcile


def test_pending_lateness_excludes_a_row_the_backend_already_published(tmp_path):
    # MOL-791: lateness is "the backend has not published", NOT "this post is old". A pending post whose
    # MIRRORED row says PUBLISHED is held back by a liveness gate on our side — the backend answered.
    # Claiming that as lateness would point the operator at the wrong system.
    from datetime import datetime, timezone, timedelta
    from fanops.reconcile import pending_lateness
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    led.add_post(Post(id="silent", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="pz_1",
                      scheduled_time=stale, postiz_state="QUEUE"))
    led.add_post(Post(id="answered", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="pz_2",
                      scheduled_time=stale, postiz_state="PUBLISHED"))
    led.add_post(Post(id="future", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, submission_id="pz_3",
                      scheduled_time=(datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()))
    rows = pending_lateness(led)
    assert [r["post_id"] for r in rows] == ["silent"]
    assert rows[0] == {"post_id": "silent", "platform": "instagram", "hours_late": 30, "postiz_state": "QUEUE"}


def test_a_parked_post_is_re_visited_and_re_logged_every_pass(tmp_path, mocker):
    # The XC-6 dedup is GONE, and it should be. It keyed on the stuck breadcrumb: the stamp WAS the
    # suppression key, so the very act of recording that a post was stuck stopped the log stream ever
    # mentioning it again. With nothing stamped, nothing suppresses — a post that is still unresolved is
    # visited and logged on EVERY pass (which is what a monitor needs), while the ledger row does not move
    # a byte across either of them.
    from datetime import datetime, timezone, timedelta
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="pk", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="fanops_abc",
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()))
    before = led.posts["pk"].model_dump()
    spy = []
    def fake_logger(cfg):
        def log(*a, **k): spy.append(a)
        return log
    mocker.patch("fanops.reconcile.get_logger", fake_logger)
    polled = []
    def gs(sid):
        polled.append(sid)
        return {"status": "scheduled"}
    led = reconcile_posts(led, cfg, get_status=gs)          # pass 1
    led = reconcile_posts(led, cfg, get_status=gs)          # pass 2
    assert polled == []
    skips = [a for a in spy if len(a) >= 3 and "client token is not a backend id" in str(a[2])]
    assert len(skips) == 2                                  # visible on both passes, not silenced by pass 1
    assert led.posts["pk"].model_dump() == before           # ...and neither pass wrote a ledger byte


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
    assert Ledger.load(cfg).posts["stuck"].state is PostState.needs_reconcile


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


# ---- MOL-788: the Postiz mirror. ONE bulk read of the backend's rows, projected onto every Postiz-backed
# post with a real id — pending AND resting. Postiz's row is the single truth; an unchanged row is not a
# write; and no mirrored observation may move a post into a re-queueable state. --------------------------

_IG_URL = "https://www.instagram.com/reel/DZvZ8Itkaxz/"
_IG_RID = "17841456789012345"


def _mirror_env(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)


def _serve_window(mocker, rows, *, code=200, boom=None, seen=None):
    """Answer EVERY Postiz GET this pass makes with one window (or fail it). `seen` collects the params of
    each call, so a test can prove the corpus is read once and over the widest bounds."""
    def fake_get(url_, **kw):
        if seen is not None:
            seen.append(kw.get("params") or {})
        if boom is not None:
            raise boom
        return _R(code, {"posts": rows} if code == 200 else {"error": "nope"})
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    mocker.patch("fanops.post.metrics.requests.get", side_effect=fake_get)


def _seed(cfg, pid, state, sub, *, url=None, hours_ago=1, account="a", platform=Platform.instagram,
          error_reason=None, postiz_state=None):
    from datetime import datetime, timezone, timedelta
    led = Ledger.load(cfg)
    led.add_post(Post(id=pid, parent_id="c", account=account, account_id="1", platform=platform,
                      caption="x", state=state, submission_id=sub, public_url=url,
                      error_reason=error_reason, postiz_state=postiz_state,
                      scheduled_time=(datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()))
    led.save()
    return led


def test_mirror_promotes_a_post_the_deleted_ladder_would_have_abandoned(tmp_path, monkeypatch, mocker):
    # THE OUTAGE REGRESSION, end to end. A post 100h past schedule is far past the horizon at which the
    # deleted ladder declared it lost and stopped looking. The mirror asks the one question that can be
    # answered — what does the row say — and the row says PUBLISHED, so the post is promoted with its real
    # permalink and Graph media id in the SAME pass. One GET serves the whole corpus, over the widest window
    # the API accepts, because a narrow window is the mechanism that manufactures a false "absent".
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "pg", PostState.needs_reconcile, "postiz_1", hours_ago=100)
    seen = []
    _serve_window(mocker, [{"id": "postiz_1", "state": "PUBLISHED",
                            "releaseURL": _IG_URL, "releaseId": _IG_RID}], seen=seen)
    reconcile_due(cfg)
    p = Ledger.load(cfg).posts["pg"]
    assert p.state is PostState.published
    assert p.public_url == _IG_URL and p.media_id == _IG_RID
    assert p.postiz_state == "PUBLISHED"                     # the raw token, verbatim, not re-mapped
    assert p.error_reason is None
    assert len(seen) == 1                                    # ONE read for the corpus, not one per post
    assert seen[0]["startDate"] == "2000-01-01" and seen[0]["endDate"] == "2100-12-31"


def test_a_published_row_that_vanishes_is_recorded_absent_never_reopened(tmp_path, monkeypatch, mocker):
    # A post that already published keeps being mirrored for life. When its row disappears from the window,
    # THAT is recorded — and only that. Reopening the post would be the double-post vector: the permalink we
    # hold is the evidence it shipped, and a backend forgetting its own row is not evidence it did not.
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "pp", PostState.published, "postiz_1", url=_IG_URL)
    _serve_window(mocker, [])
    reconcile_due(cfg)
    p = Ledger.load(cfg).posts["pp"]
    assert p.postiz_state == "absent"
    assert p.state is PostState.published and p.public_url == _IG_URL
    assert p.error_reason is None                            # the absence buys no prose in the parsed field


def test_an_error_row_on_a_published_post_is_recorded_never_failed(tmp_path, monkeypatch, mocker):
    # The sharpest edge of the contract. A published post whose row later reads ERROR is SURFACED via
    # postiz_state and moved nowhere. `failed` is re-queueable, so mirroring an ERROR into it would hand the
    # daemon a licence to re-publish a post that is live on the platform. The operator decides; the mirror
    # only makes the disagreement visible.
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "pp", PostState.published, "postiz_1", url=_IG_URL, postiz_state="PUBLISHED")
    _serve_window(mocker, [{"id": "postiz_1", "state": "ERROR", "releaseURL": None, "releaseId": None}])
    reconcile_due(cfg)
    p = Ledger.load(cfg).posts["pp"]
    assert p.postiz_state == "ERROR"
    assert p.state is PostState.published                     # NOT failed, NOT needs_reconcile
    assert p.error_reason is None


def test_an_error_row_on_a_pending_post_still_resolves_it_failed(tmp_path, monkeypatch, mocker):
    # The same token on a post that never published means the opposite thing, and the branch is unchanged:
    # nothing is live, so `failed` is safe and re-queueing is the correct affordance.
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "pn", PostState.needs_reconcile, "postiz_1")
    _serve_window(mocker, [{"id": "postiz_1", "state": "ERROR", "releaseURL": None, "releaseId": None,
                            "error": "API access blocked."}])
    reconcile_due(cfg)
    p = Ledger.load(cfg).posts["pn"]
    assert p.state is PostState.failed
    assert p.postiz_state == "ERROR"
    assert "API access blocked." in (p.error_reason or "")
    assert "no detail" not in (p.error_reason or "")


def test_a_second_pass_over_unchanged_rows_writes_nothing(tmp_path, monkeypatch, mocker):
    # THE ZERO-BYTE PROPERTY. The mirror runs on every daemon tick over the whole corpus; if an identical
    # row counted as a write, every published post in the ledger would be rewritten forever, and every
    # downstream reader watching for change would see nothing but noise.
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "pp", PostState.published, "postiz_1", url=_IG_URL)
    rows = [{"id": "postiz_1", "state": "PUBLISHED", "releaseURL": _IG_URL, "releaseId": _IG_RID}]
    _serve_window(mocker, rows)
    reconcile_due(cfg)
    after_first = Ledger.load(cfg).posts["pp"].model_dump()
    assert after_first["postiz_state"] == "PUBLISHED"         # pass 1 DID record the first observation
    log_before = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    reconcile_due(cfg)
    assert Ledger.load(cfg).posts["pp"].model_dump() == after_first
    new_lines = (cfg.log_path.read_text() if cfg.log_path.exists() else "")[len(log_before):]
    assert "postiz_state" not in new_lines                   # and no per-post mirror event was emitted


def test_a_transport_failure_mirrors_nobody(tmp_path, monkeypatch, mocker):
    # A fetch that did not happen is not evidence about any post. It buys one log line, and every row —
    # pending and resting alike — is left exactly as it was.
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "pp", PostState.published, "postiz_1", url=_IG_URL)
    _seed(cfg, "pn", PostState.needs_reconcile, "postiz_2")
    before = {k: v.model_dump() for k, v in Ledger.load(cfg).posts.items()}
    _serve_window(mocker, [], boom=RuntimeError("connreset SENTINEL-NET"))
    reconcile_due(cfg)
    assert {k: v.model_dump() for k, v in Ledger.load(cfg).posts.items()} == before
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "mirror_fetch_error" in log and "SENTINEL-NET" in log


def test_a_401_on_the_bulk_read_halts_the_pass(tmp_path, monkeypatch, mocker):
    # A bad key makes every read fail — grinding the whole corpus against it is pointless and the halt is
    # what the CLI turns into "reconcile skipped". Unchanged from the per-post era, on the new read.
    from fanops.errors import PostizAuthError
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "pn", PostState.needs_reconcile, "postiz_1")
    _serve_window(mocker, [], code=401)
    with pytest.raises(PostizAuthError):
        reconcile_due(cfg)


def test_a_zernio_backed_resting_post_is_never_stamped_absent(tmp_path, monkeypatch, mocker):
    # THE SCOPE OF THE MIRROR IS ONE BACKEND. A TikTok post published through Zernio is structurally absent
    # from every Postiz window, so mirroring it would stamp `absent` on the first pass and INVENT an
    # observation about a backend that was never asked. postiz_state stays None for it, forever.
    from fanops.accounts import add_account, set_backend
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    _seed(cfg, "ig", PostState.published, "postiz_1", url=_IG_URL)
    _seed(cfg, "tt", PostState.analyzed, "zernio_real_1", url="https://www.tiktok.com/@tt/video/1",
          account="tt", platform=Platform.tiktok)
    _serve_window(mocker, [{"id": "postiz_1", "state": "PUBLISHED",
                            "releaseURL": _IG_URL, "releaseId": _IG_RID}])
    reconcile_due(cfg)
    led = Ledger.load(cfg)
    assert led.posts["ig"].postiz_state == "PUBLISHED"
    assert led.posts["tt"].postiz_state is None               # never asked about -> never answered for
    assert led.posts["tt"].state is PostState.analyzed


def test_a_client_token_post_is_never_mirrored_but_still_escalates(tmp_path, monkeypatch, mocker):
    # A `fanops_` idempotency token is not a Postiz row id, so no window can ever hold it: mirroring it
    # would record a permanent `absent` that says nothing about the post. It is still VISITED, because the
    # (state, age) ladder un-strands a crash-stranded submit claim — I5 closes an unpollable token past
    # 24h as failed/unknown (never GET, never mirrored).
    from fanops.models import ErrorKind
    _mirror_env(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed(cfg, "tok", PostState.submitting, "fanops_deadbeef", hours_ago=30)
    _serve_window(mocker, [])
    reconcile_due(cfg)
    p = Ledger.load(cfg).posts["tok"]
    assert p.postiz_state is None                             # no row could name it -> no observation
    assert p.state is PostState.failed
    assert p.state is not PostState.needs_reconcile
    assert p.error_kind is ErrorKind.unknown
    assert "unpollable" in (p.error_reason or "")


def test_reconcile_reads_puts_zernio_fanops_token_on_token_only_never_polled(tmp_path, monkeypatch):
    # I4: GET /posts/fanops_* 400s. A Zernio-backed birth token is token_only; a real id is still polled.
    from fanops.accounts import add_account, set_backend
    from fanops.reconcile import _reconcile_reads
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    led = Ledger.load(cfg)
    led.add_post(Post(id="tok", parent_id="c", account="tt", account_id="1", platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id="fanops_deadbeef"))
    led.add_post(Post(id="real", parent_id="c", account="tt", account_id="1", platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id="zernio_real_1"))
    mirrored, token_only, polled = _reconcile_reads(cfg, led, lambda *a, **k: None)
    assert [p.id for p in token_only] == ["tok"]
    assert [p.id for p in polled] == ["real"]
    assert mirrored == []
