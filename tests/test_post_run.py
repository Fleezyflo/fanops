import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ErrorKind, Post, Clip, Moment, PostState, ClipState, MomentState, Platform
from fanops.post.run import publish_due

# publish-out-of-lock: publish_due(cfg, *, now) self-loads the ledger and publishes each due post via a
# per-post claim->network->finalize discipline (network OUTSIDE the flock). So the unit setup must PERSIST
# the queued posts to disk before calling, and assertions reload from disk (the source of truth).


def _mom1(led, mid="mom_1"):
    """Materialize the moment a clip in this file names as its parent (`mom_1`, or `m1` for a few sites). It was never added:
    the fixtures described a clip with a dangling ancestor, a lineage production cannot build
    (_delete_moment_cascade refuses to drop an ancestor while a protected post hangs off it). It went
    unnoticed only because every retirement predicate failed OPEN on a missing row. publish_due now asks
    Ledger.can_promote, which fails CLOSED, so the fixture must describe a lineage that could exist.
    add_moment is setdefault, so repeat calls within one test are a no-op on the first row."""
    led.add_moment(Moment(id=mid, parent_id="src_1", start=0.0, end=7.0, reason="worth posting",
                          state=MomentState.clipped))


def _queued(led, cfg, pid="p1", cid="clip_1", when="2026-06-02T18:00:00Z", account="a"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led)
    led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id=cid, account=account, account_id="98432",
                      platform=Platform.instagram, caption="ship it",
                      scheduled_time=when, state=PostState.queued, public_url="dryrun://98432"))
    led.save()                                          # persist so the self-loading publish_due sees it


# dryrun-boundary (fix/dryrun-boundary-m1): publish_due now SKIPS a dryrun post (system NOT live) — it stays
# `queued`, never `published` (dryrun must never enter the distribution rail / mint a phantom-published row).
# Tests below that exercise a general PUBLISH MECHANISM (published_at stamp, 06_published archive, upload-once,
# idempotency, only-due filtering, no-deadlock) used the dryrun poster only as a stand-in to REACH published.
# Convert them to a genuinely LIVE backend so the post actually enters the rail. Helpers:
#   _live(monkeypatch)      -> flip the process to a live postiz deployment (is_live True, effective_provider=postiz)
#   _stub_ok_poster(...)    -> replace run.get_poster with a stub that drives submitted + a REAL https permalink
#                              (the submitted->published gate in run.py refuses a missing/dryrun URL), so no real
#                              network call happens. _queued's posts get an already-http media_url so _ensure_media
#                              passes it through (no upload) on the live backend.
_LIVE_PERMALINK = "https://www.instagram.com/reel/AAA/"


def _live(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")


def _stub_ok_poster(mocker, cfg):
    import fanops.post.run as run
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted}); led_.posts[post_id].submission_id = "s"
            led_.posts[post_id].public_url = _LIVE_PERMALINK   # real permalink -> submitted promotes to published
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))


def _http_media(led, *pids):
    for pid in pids:
        led.posts[pid].media_urls = ["https://h/v.mp4"]   # already-http -> passes through, no live upload
    led.save()


def test_is_fatal_auth_error_matches_by_type_not_substring():
    # AUDIT H8: the halt decision is now a TYPE check (PostizAuthError), not "401"/"API_KEY"
    # in the message. So it fires on a reworded auth error (under-fire fixed) and does NOT fire on a
    # non-auth error whose text happens to contain "401" (over-fire fixed).
    from fanops.errors import PostizAuthError
    from fanops.post.run import _is_fatal_auth_error
    assert _is_fatal_auth_error(PostizAuthError("invalid credentials")) is True       # no "401" text
    assert _is_fatal_auth_error(RuntimeError("postiz 503: upstream 401abc")) is False  # "401" but not auth
    assert _is_fatal_auth_error(RuntimeError("POSTIZ_API_KEY missing")) is False       # substring, not the type

def test_publishes_only_due_posts(tmp_path, monkeypatch, mocker):
    _live(monkeypatch)                                  # live backend so a due post actually enters the rail
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="due", cid="c_due", when="2020-01-01T00:00:00Z")     # past => due
    _queued(led, cfg, pid="future", cid="c_future", when="2999-01-01T00:00:00Z")  # not due
    _http_media(led, "due", "future"); _stub_ok_poster(mocker, cfg)
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert led.posts["due"].state is PostState.published
    assert led.posts["future"].state is PostState.queued       # held back (FIX F12)

def test_publish_stamps_published_at(tmp_path, monkeypatch, mocker):
    # content-lifecycle Phase 2: the submitted->published transition stamps a TRUE publish time (aware).
    # approve_post must NOT touch it (published_at is immutable after the stamp).
    from fanops.timeutil import parse_iso, iso_z
    from datetime import datetime, timezone
    _live(monkeypatch)                                  # live backend so the post reaches the published stamp
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="pp", cid="c_pp", when="2020-01-01T00:00:00Z")
    _http_media(led, "pp"); _stub_ok_poster(mocker, cfg)
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    p = led.posts["pp"]
    assert p.state is PostState.published
    assert p.published_at and parse_iso(p.published_at).tzinfo is not None
    before = p.published_at
    led.approve_post("pp", now_iso=iso_z(datetime.now(timezone.utc)))   # a no-op on a non-awaiting post
    assert led.posts["pp"].published_at == before                       # untouched by approve

def test_publish_writes_06_published_archive(tmp_path, monkeypatch, mocker):
    # content-lifecycle Phase 3: a published post writes 06_published/<day>/<id>.json with expected fields;
    # the day == the post's published_at day.
    import json
    from fanops.timeutil import parse_iso
    _live(monkeypatch)                                  # live backend so the publish (and its archive) fires
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="pa", cid="c_pa", when="2020-01-01T00:00:00Z")
    _http_media(led, "pa"); _stub_ok_poster(mocker, cfg)
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    day = parse_iso(led.posts["pa"].published_at).date().isoformat()
    rec_path = cfg.published / day / "pa.json"
    assert rec_path.exists()
    rec = json.loads(rec_path.read_text())
    assert rec["post_id"] == "pa" and rec["clip_id"] == "c_pa" and rec["published_at"]
    assert rec["account"] == "a" and rec["caption"] == "ship it"   # the network-phase post carried real fields

def test_archive_fail_open_write(tmp_path, monkeypatch, mocker):
    # A record-write failure must NOT strand the live post: it still reaches published (archive swallowed)
    # AND the failure is LOGGED — fail-open with a breadcrumb, never a silent swallow.
    # MOL-728 (decorative-test repair): this injected `pathlib.Path.write_text`, which _archive_published has
    # NEVER called — it opened the final path with os.open+O_TRUNC (and now writes via write_text_atomic, which
    # uses os.fdopen). Nothing in its call tree write_text's under 06_published either (`_moment_hook` is a dict
    # lookup; ledger.py has zero write_text calls). The fault could not fire, so the test passed with its
    # injection SKIPPED. Inject at the writer the archive actually calls, and assert the fault fired.
    _live(monkeypatch)                                  # live backend so the post publishes (and tries to archive)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="pw", cid="c_pw", when="2020-01-01T00:00:00Z")
    _http_media(led, "pw"); _stub_ok_poster(mocker, cfg)
    boom = mocker.patch("fanops.post.publish_archive.write_text_atomic", side_effect=OSError("disk full"))
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    assert boom.called                                                 # NEGATIVE CONTROL: the fault really fired
    assert Ledger.load(cfg).posts["pw"].state is PostState.published   # archive failure did NOT flip it to failed
    assert "archive_error" in cfg.log_path.read_text()                 # ...and it was logged, not silently swallowed

def test_archive_fail_open_mkdir(tmp_path, monkeypatch, mocker):
    # A mkdir PermissionError on the published dir must also be swallowed — the post still publishes.
    # Scope the failure to 06_published only (a blanket Path.mkdir mock would also break the ledger save).
    import pathlib
    _live(monkeypatch)                                  # live backend so the post publishes (and tries to mkdir the archive)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="pm", cid="c_pm", when="2020-01-01T00:00:00Z")
    _http_media(led, "pm"); _stub_ok_poster(mocker, cfg)
    real_mkdir = pathlib.Path.mkdir
    def fake_mkdir(self, *a, **k):
        if "06_published" in str(self): raise PermissionError("nope")
        return real_mkdir(self, *a, **k)
    mocker.patch("pathlib.Path.mkdir", fake_mkdir)
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    assert Ledger.load(cfg).posts["pm"].state is PostState.published

def test_publish_uploads_media_once_and_advances(tmp_path, monkeypatch, mocker):
    # dryrun-boundary: this used the dryrun poster only to REACH published. Run it LIVE so the two posts
    # actually enter the rail; the property under test is the F44 cache surviving the per-post
    # claim->network->finalize round-trip (ensure_clip_media runs once per post, clip.media_url persists,
    # both posts resolve to the same url). The real single-upload-across-posts property is locked by the
    # sibling test_publish_uploads_clip_media_once_across_posts_live; here we stub the uploader so no network
    # is hit and keep the ensure_clip_media spy (call_count == 2 = once per post).
    _live(monkeypatch)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="clip_1", when="2020-01-01T00:00:00Z")
    _queued(led, cfg, pid="p2", cid="clip_1", when="2020-01-01T00:00:00Z")  # same clip, 2 posts (no media_urls -> ensure runs)
    mocker.patch("fanops.post.get_media_uploader",
                 return_value=lambda cfg_, path, **kw: "img1|https://cdn.postiz.test/clip_1.mp4")
    _stub_ok_poster(mocker, cfg)
    # spy ensure_clip_media to prove it runs once per post (the cache-survival property)
    import fanops.post.run as run
    spy = mocker.spy(run, "ensure_clip_media")
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert led.posts["p1"].state is PostState.published and led.posts["p2"].state is PostState.published
    assert led.posts["p1"].media_urls[0] == "img1|https://cdn.postiz.test/clip_1.mp4"
    assert spy.call_count == 2 and led.clips["clip_1"].media_url
    assert led.posts["p1"].media_urls == led.posts["p2"].media_urls

def test_publish_uploads_clip_media_once_across_posts_live(tmp_path, monkeypatch, mocker):
    # F44 locked for the per-post claim->finalize world: two posts on ONE clip must trigger EXACTLY ONE
    # real upload. The first post's finalize persists clip.media_url to disk; the second post's lock-free
    # network phase reloads that cache and skips the upload. dryrun can't show this (no real upload), so
    # use a live backend (postiz) and spy the actual uploader (ensure_clip_media -> get_media_uploader).
    # A double-upload here would be a per-post cost/latency regression the separate claim-per-post design
    # could silently reintroduce if the clip cache stopped surviving finalize.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="shared", when="2020-01-01T00:00:00Z")
    _queued(led, cfg, pid="p2", cid="shared", when="2020-01-01T00:00:00Z")  # same clip, 2nd post
    uploads = []
    def fake_upload(cfg_, path, **kw):
        uploads.append(str(path)); return "img1|https://cdn.postiz.test/shared.mp4"
    mocker.patch("fanops.post.get_media_uploader", return_value=fake_upload)
    import fanops.post.run as run
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted}); led_.posts[post_id].submission_id = "s"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert len(uploads) == 1                                   # the clip uploaded ONCE, not once-per-post
    assert led.posts["p1"].state is PostState.published and led.posts["p2"].state is PostState.published
    assert led.posts["p1"].media_urls == led.posts["p2"].media_urls == ["img1|https://cdn.postiz.test/shared.mp4"]

def test_publish_idempotent_skips_already_submitted(tmp_path, monkeypatch, mocker):
    _live(monkeypatch)                                  # live backend so the 1st pass actually publishes
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, when="2020-01-01T00:00:00Z")
    _http_media(led, "p1"); _stub_ok_poster(mocker, cfg)
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    publish_due(cfg, now="2026-06-02T18:00:00Z")        # 2nd pass: p1 is published, not queued -> no-op
    assert Ledger.load(cfg).posts["p1"].state is PostState.published

def test_publish_failed_poster_marks_failed_durable(tmp_path, monkeypatch, mocker):
    # A poster that fails -> post.state failed (not analyzed, not published), durable.
    # dryrun-boundary: live env swap so the post enters the rail and the poster stub runs; the stub (below)
    # drives the terminal `failed` state. http media_urls -> _ensure_media passes through (no live upload).
    _live(monkeypatch)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="pf", cid="c_pf", when="2020-01-01T00:00:00Z")
    _http_media(led, "pf")
    # make get_poster return a poster whose publish sets the post to failed
    import fanops.post.run as run
    class _FailPoster:
        def __init__(self, cfg): pass
        def publish(self, led, post_id):
            led.posts[post_id] = led.posts[post_id].model_copy(update={"state": PostState.failed})
            led.posts[post_id].error_reason = "simulated 422"
            return led
    mocker.patch.object(run, "get_poster", return_value=_FailPoster(cfg))
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    # durable: reload from disk and confirm
    led2 = Ledger.load(cfg)
    assert led2.posts["pf"].state is PostState.failed

def test_publish_failure_redacts_api_key_from_error_reason(tmp_path, monkeypatch, mocker):
    # opsec follow-up: a network/library exception text can embed the presented key; it must be SCRUBBED
    # before it lands in the durable error_reason (defense-in-depth; mirrors _safe on response bodies).
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "SUPERSECRETKEY")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_k.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led)
    led.add_clip(Clip(id="c_k", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="pk", parent_id="c_k", account="a", account_id="1",
                      platform=Platform.instagram, caption="x",
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued, public_url="dryrun://pk"))
    led.save()
    import fanops.post.run as run
    def boom(led_, cfg_, clip_id, backend=None, **kw):
        raise RuntimeError("postiz presign 503: token=SUPERSECRETKEY rejected")
    mocker.patch.object(run, "ensure_clip_media", side_effect=boom)
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    er = Ledger.load(cfg).posts["pk"].error_reason or ""
    assert "SUPERSECRETKEY" not in er           # the key is scrubbed from the durable record
    assert "***" in er and "503" in er          # redaction marker present; the diagnostic detail survives

def test_publish_no_schedule_parks_not_publishes(tmp_path, monkeypatch):
    # CULM-4: a queued post with NO scheduled_time must NOT auto-publish via publish_due (no-auto-publish
    # defense-in-depth) — it parks (stays queued). publish_post (manual Publish-now) still ships a timeless post.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_ns.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led)
    led.add_clip(Clip(id="c_ns", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="pns", parent_id="c_ns", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued, public_url="dryrun://pns"))  # no scheduled_time
    led.save()
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    assert Ledger.load(cfg).posts["pns"].state is PostState.queued   # CULM-4: parked, never auto-published

def test_publish_refreshes_account_id_from_current_mapping(tmp_path, monkeypatch, mocker):
    # #1 resolve-at-publish: account_id is FROZEN onto the post at crosspost; a later Go-Live integration
    # REMAP must still reach the post — publish re-resolves the CURRENT integration id before sending,
    # rather than shipping the stale frozen id (which the backend would reject / route wrong).
    import json
    _live(monkeypatch)                                  # dryrun-boundary: live env so the post enters the rail
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)  # (the account_id-refresh only happens on a real backend)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "NEW_IG_ID"}}]}))
    f = cfg.clips / "c_a.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led)
    led.add_clip(Clip(id="c_a", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="pa", parent_id="c_a", account="a", account_id="OLD_STALE_ID",   # frozen-at-crosspost id
                      platform=Platform.instagram, caption="x", media_urls=["https://h/v.mp4"],  # http -> no live upload
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued, public_url="dryrun://pa"))
    led.save()
    import fanops.post.run as run
    seen = {}
    class _CapturePoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            seen["account_id"] = led_.posts[post_id].account_id    # what the poster will actually send
            led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted}); led_.posts[post_id].submission_id = "s"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_CapturePoster(cfg))
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    assert seen["account_id"] == "NEW_IG_ID"                        # sent with the CURRENT mapping, not the frozen one
    assert Ledger.load(cfg).posts["pa"].account_id == "NEW_IG_ID"   # and persisted for the Posted record

def test_publish_does_not_redrive_submitting_post(tmp_path, monkeypatch, mocker):
    # F11 crash-sim regression lock: a post stranded in 'submitting' is NOT re-published.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_sub.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led)
    led.add_clip(Clip(id="c_sub", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="psub", parent_id="c_sub", account="a", account_id="1",
                      platform=Platform.instagram, caption="x",
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.submitting, public_url="dryrun://psub"))
    led.save()
    import fanops.post.run as run
    spy = mocker.spy(run, "ensure_clip_media")
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    assert Ledger.load(cfg).posts["psub"].state is PostState.submitting    # untouched — not re-driven
    assert spy.call_count == 0                                             # no media re-upload either

def test_publish_one_bad_upload_does_not_block_others(tmp_path, monkeypatch, mocker):
    # Per-post isolation: clip A's media raises, clip B still publishes.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, cid in [("pa", "c_a"), ("pb", "c_b")]:
        f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
        _mom1(led)
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued, public_url="dryrun://1"))
    led.save()
    import fanops.post.run as run
    # c_a upload raises a NON-auth error; c_b uploads fine; poster.publish succeeds (submitted)
    def fake_ensure(led_, cfg_, clip_id, backend=None, **kw):
        if clip_id == "c_a":
            raise RuntimeError("postiz upload failed (503): server down")
        return "https://cdn/ok.mp4"
    mocker.patch.object(run, "ensure_clip_media", side_effect=fake_ensure)
    mocker.patch("fanops.post.run.time.sleep", return_value=None)   # MOL-115 retry backoff — no real sleep in unit test
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted})
            led_.posts[post_id].submission_id = "s_ok"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert led.posts["pa"].state is PostState.failed   # MOL-125: pre-send transient -> failed (re-queueable)
    assert "503" in (led.posts["pa"].error_reason or "") or "publish failed" in (led.posts["pa"].error_reason or "").lower()
    assert led.posts["pb"].state is PostState.published        # healthy clip still shipped

def test_publish_needs_reconcile_does_not_halt_loop(tmp_path, monkeypatch, mocker):
    # AUDIT C1: a poster that parks a post in needs_reconcile (ambiguous 5xx/timeout) is NOT an
    # exception — publish_due must leave that post in needs_reconcile and keep publishing the rest
    # (a needs_reconcile post is terminal-for-now, like failed, never re-driven this pass).
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, cid in [("prec", "c_rec"), ("pok", "c_ok")]:
        f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
        _mom1(led)
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued, public_url="dryrun://1"))
    led.save()
    import fanops.post.run as run
    mocker.patch.object(run, "ensure_clip_media", return_value="https://cdn/ok.mp4")
    class _ReconcileThenOkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            if post_id == "prec":
                led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.needs_reconcile})
                led_.posts[post_id].error_reason = "blotato 503: ambiguous, may be live"
            else:
                led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted})
                led_.posts[post_id].submission_id = "s_ok"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_ReconcileThenOkPoster(cfg))
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led2 = Ledger.load(cfg)                                       # durable across the finalize save
    assert led2.posts["prec"].state is PostState.needs_reconcile   # parked, not re-driven, not failed
    assert led2.posts["pok"].state is PostState.published          # healthy post still shipped


def test_publish_auth_error_halts_run(tmp_path, monkeypatch, mocker):
    # AUDIT H8: a fatal auth failure must HALT (raise), not mark one post failed and grind on.
    # The trigger is now the TYPE (PostizAuthError), not a substring in the message — so it fires
    # even when the message doesn't literally contain "401" (fixes the F52 under-fire).
    from fanops.errors import PostizAuthError
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "badkey")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_auth.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led)
    led.add_clip(Clip(id="c_auth", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="pauth", parent_id="c_auth", account="a", account_id="1",
                      platform=Platform.instagram, caption="x",
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued, public_url="dryrun://pauth"))
    led.save()
    import fanops.post.run as run
    mocker.patch.object(run, "ensure_clip_media", return_value="https://cdn/ok.mp4")
    class _AuthFailPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            # worded WITHOUT "401" on purpose — a reworded auth error must still halt by type
            raise PostizAuthError("postiz rejected the api key (invalid credentials)")
    mocker.patch.object(run, "get_poster", return_value=_AuthFailPoster(cfg))
    import pytest
    with pytest.raises(PostizAuthError):
        publish_due(cfg, now="2026-06-02T18:00:00Z")


def test_publish_non_auth_error_with_401_in_text_does_not_halt(tmp_path, monkeypatch, mocker):
    # AUDIT H8 over-fire regression: a NON-auth error whose message merely CONTAINS "401" (e.g. a
    # 503 body echoing an upstream id) must NOT halt the queue — it's a per-post failure. The old
    # substring match wrongly tore down the whole run on this; the typed check must not.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, cid in [("pbad", "c_bad"), ("pok", "c_ok2")]:
        f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
        _mom1(led)
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued, public_url="dryrun://1"))
    led.save()
    import fanops.post.run as run
    def fake_ensure(led_, cfg_, clip_id, backend=None, **kw):
        if clip_id == "c_bad":
            raise RuntimeError("postiz 503: upstream request 401abc timed out")  # 401 in text, NOT auth
        return "https://cdn/ok.mp4"
    mocker.patch.object(run, "ensure_clip_media", side_effect=fake_ensure)
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted})
            led_.posts[post_id].submission_id = "s_ok"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))
    publish_due(cfg, now="2026-06-02T18:00:00Z")   # must NOT raise
    led = Ledger.load(cfg)
    assert led.posts["pbad"].state is PostState.failed         # isolated per-post failure
    assert led.posts["pok"].state is PostState.published        # the run continued


def test_publish_due_no_deadlock_self_manages_its_lock(tmp_path, monkeypatch, mocker):
    # publish-out-of-lock: publish_due owns its locking (per-post claim/finalize transactions) and is
    # called STANDALONE — never inside a caller-held Ledger.transaction (advance/publish_now both moved
    # the publish OUT of their lock). A normal call must acquire/release cleanly and complete (no hang).
    # dryrun-boundary: run LIVE (with a stubbed poster) so the post actually publishes through the rail.
    _live(monkeypatch)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c1.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"x")
    _mom1(led, "m1")
    led.add_clip(Clip(id="c1", parent_id="m1", path=str(f), state=ClipState.captioned))
    led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued, media_urls=["https://h/v.mp4"],
                      scheduled_time="2020-01-01T00:00:00Z", public_url="dryrun://p1"))
    led.save()
    _stub_ok_poster(mocker, cfg)
    publish_due(cfg, now="2020-01-02T00:00:00Z")
    assert Ledger.load(cfg).posts["p1"].state is PostState.published


def test_publish_due_malformed_scheduled_time_is_per_post_failure_not_escape(tmp_path, monkeypatch):
    # M07: parseable naive past is due (canonical UTC); only truly unparseable times fail the post.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    f = cfg.clips / "c1.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led, "m1")
    led.add_clip(Clip(id="c1", parent_id="m1", path=str(f), state=ClipState.captioned))
    led.add_post(Post(id="bad", parent_id="c1", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued,
                      scheduled_time="2026-06-01 09:00", public_url="dryrun://bad"))   # naive but parseable -> due when past
    led.save()
    publish_due(cfg, now="2026-06-02T00:00:00Z")            # must NOT raise
    led = Ledger.load(cfg)
    assert led.posts["bad"].state is not PostState.failed
    assert led.posts["bad"].state is PostState.queued   # dryrun: not distributed, stays queued


def test_publish_due_garbage_scheduled_time_does_not_escape(tmp_path, monkeypatch):
    # Same root cause, unparseable (ValueError) variant.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    f = cfg.clips / "c2.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    _mom1(led, "m1")
    led.add_clip(Clip(id="c2", parent_id="m1", path=str(f), state=ClipState.captioned))
    led.add_post(Post(id="garbage", parent_id="c2", account="a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued,
                      scheduled_time="not-a-timestamp", public_url="dryrun://garbage"))
    led.save()
    publish_due(cfg, now="2026-06-02T00:00:00Z")   # must NOT raise
    assert Ledger.load(cfg).posts["garbage"].state is PostState.failed


def test_publish_uploads_variant_file_media_on_live_backend(tmp_path, monkeypatch, mocker):
    # AUDIT (stage-6 HIGH): a creative-variation post is BORN with media_urls=["file://<variant>"]
    # (crosspost stamps the per-account variant render). On a live backend a file:// entry must be
    # uploaded as the variant FILE itself (NOT ensure_clip_media — the clip-level cache holds the
    # parent's BASE render and would lose the burned hook). The https result is persisted so a retry
    # never re-uploads.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    vfile = cfg.clips / "clip_1_vhash.mp4"; vfile.parent.mkdir(parents=True, exist_ok=True); vfile.write_bytes(b"V")
    _mom1(led)
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(cfg.clips / "clip_1.mp4"), state=ClipState.queued))
    led.add_post(Post(id="pv", parent_id="clip_1", account="a", account_id="98432",
                      platform=Platform.instagram, caption="x", scheduled_time="2020-01-01T00:00:00Z",
                      state=PostState.queued, media_urls=[f"file://{vfile}"], public_url="dryrun://pv"))
    led.save()
    uploaded = []
    def fake_upload(cfg_, path, **kw):
        uploaded.append(str(path)); return "img1|https://cdn.postiz.test/v.mp4"
    # run.py routes the variant file:// upload through get_media_uploader(cfg, backend)(cfg, path);
    # patch that resolver (bound into run.py's namespace) to a fake uploader.
    mocker.patch("fanops.post.run.get_media_uploader", return_value=fake_upload)
    sent = {}
    class FakePoster:
        def publish(self, led_, post_id):
            sent["media_urls"] = list(led_.posts[post_id].media_urls)
            led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted})
            return led_
    mocker.patch("fanops.post.run.get_poster", return_value=FakePoster())
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led = Ledger.load(cfg)
    assert uploaded == [str(vfile)]                                    # the VARIANT file, not the parent clip
    assert sent["media_urls"] == ["img1|https://cdn.postiz.test/v.mp4"]     # the poster sees postiz composite, never file://
    assert led.posts["pv"].media_urls == ["img1|https://cdn.postiz.test/v.mp4"]  # persisted -> a retry never re-uploads
    assert led.posts["pv"].state is PostState.published


def test_archive_published_is_owner_only_with_no_world_readable_window(tmp_path):
    # L2 (audit): the published-post archive (operator handle + live permalink + creative) is written 0600
    # ATOMICALLY (no write-then-chmod world-readable window) into a 0700 day-dir (not world-listable).
    import stat
    from fanops.post.run import _archive_published
    cfg = Config(root=tmp_path)
    post = Post(id="p_arch", parent_id="clip_1", account="a", account_id="98432",
                platform=Platform.instagram, caption="c", state=PostState.published,
                created_at="2026-06-02T18:00:00Z", public_url="https://example/p")
    _archive_published(cfg, post)
    ap = cfg.published / "2026-06-02" / "p_arch.json"
    assert ap.exists()
    assert stat.S_IMODE(ap.stat().st_mode) == 0o600                  # owner-only file, created 0600 (no chmod window)
    assert stat.S_IMODE(ap.parent.stat().st_mode) == 0o700          # owner-only day dir (not world-listable)


def _arch_post(pid: str, url: str) -> Post:
    return Post(id=pid, parent_id="clip_1", account="a", account_id="98432", platform=Platform.instagram,
                caption="c", state=PostState.published, created_at="2026-06-02T18:00:00Z", public_url=url)


def test_archive_published_rewrite_replaces_inode_and_relocks_a_loose_prior_file(tmp_path):
    # MOL-728: os.replace swaps in a FRESH 0600 inode, so a re-archive over a record left world-readable by an
    # older build lands owner-only WITHOUT any tighten-after-write chmod, and carries the new content.
    # (reconcile.py re-fires _archive_published on an already-archived post, so re-archive is a live path.)
    import json, stat
    from fanops.post.run import _archive_published
    cfg = Config(root=tmp_path)
    _archive_published(cfg, _arch_post("p_re", "https://example/first"))
    ap = cfg.published / "2026-06-02" / "p_re.json"
    ap.chmod(0o644)                                                  # a record archived by a looser, older build
    _archive_published(cfg, _arch_post("p_re", "https://example/second"))
    assert stat.S_IMODE(ap.stat().st_mode) == 0o600                  # replace brought its own 0600 inode
    assert json.loads(ap.read_text())["public_url"] == "https://example/second"   # new content landed
    assert list(ap.parent.glob("*.tmp")) == []                       # no temp residue on the happy path


def test_archive_published_replace_failure_keeps_prior_record_and_removes_temp(tmp_path, monkeypatch):
    # MOL-728 REGRESSION: the old writer opened the FINAL path with O_CREAT|O_WRONLY|O_TRUNC, so an interrupted
    # re-archive destroyed the record it was rewriting (truncate lands before any byte is written). Under
    # mkstemp+os.replace the prior record must survive a replace failure byte-for-byte, with no temp left behind
    # and the failure still logged (fail-open, never silent).
    from fanops import controlio
    from fanops.post.run import _archive_published
    cfg = Config(root=tmp_path)
    _archive_published(cfg, _arch_post("p_int", "https://example/first"))
    ap = cfg.published / "2026-06-02" / "p_int.json"
    prior = ap.read_bytes()
    real_replace = controlio.os.replace
    def boom(src, dst, *a, **k):
        if "06_published" in str(dst): raise OSError("simulated interruption")   # scope: never the ledger's own replace
        return real_replace(src, dst, *a, **k)
    monkeypatch.setattr(controlio.os, "replace", boom)
    _archive_published(cfg, _arch_post("p_int", "https://example/second"))       # fail-open: must NOT raise
    monkeypatch.undo()
    assert ap.read_bytes() == prior                                  # prior record intact — no O_TRUNC husk
    assert list(ap.parent.glob("*.tmp")) == []                       # temp cleaned up on the failure path
    assert "archive_error" in cfg.log_path.read_text()               # failure logged, not silently swallowed



# ---- Sprint 2: Postiz publish throttle (per integration) ----
def test_publish_throttle_wait_spaces_postiz_calls(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTIZ_PUBLISH_PER_MIN", "4")
    from fanops.post.run import _publish_throttle_wait, reset_publish_throttle
    reset_publish_throttle()
    cfg = Config(root=tmp_path)
    _mono = iter([100.0, 100.0, 100.5, 115.5, 115.5])
    def _next_mono():
        try: return next(_mono)
        except StopIteration: return 115.5
    mocker.patch("fanops.post.run.time.monotonic", side_effect=_next_mono)
    sleeps = []
    mocker.patch("fanops.post.run._sleep", side_effect=lambda s: sleeps.append(s))   # capture the wait (no real sleep; conftest already no-ops it)
    _publish_throttle_wait(cfg, "postiz", "ig_1")
    _publish_throttle_wait(cfg, "postiz", "ig_1")
    assert len(sleeps) == 1 and sleeps[0] >= 14.0
    reset_publish_throttle()


def test_publish_due_calls_postiz_throttle(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk_test")
    from fanops.post.run import publish_due, reset_publish_throttle
    reset_publish_throttle()
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    for pid in ("p1", "p2"):
        _queued(led, cfg, pid=pid, cid=f"c_{pid}", when="2020-01-01T00:00:00Z")
        led.posts[pid].media_urls = ["https://cdn.test/clip.mp4"]
    led.save()
    class FakePoster:
        def publish(self, led_, post_id):
            led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted})
            led_.posts[post_id].public_url = "https://instagram.com/x/"
            return led_
    mocker.patch("fanops.post.run.get_poster", return_value=FakePoster())
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    throttle = mocker.patch("fanops.post.run._publish_throttle_wait")
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    assert throttle.call_count == 2
    assert all(c.args[1] == "postiz" for c in throttle.call_args_list)
    reset_publish_throttle()


# ---- publish_due: no per-account daily outbound quota ----

def _quota_now():
    from datetime import datetime, timezone
    from fanops.timeutil import iso_z
    return iso_z(datetime.now(timezone.utc))


def test_daily_account_cap_constant_is_gone():
    import fanops.studio.views_common as vc
    assert not hasattr(vc, "_DAILY_ACCOUNT_CAP")


def test_publish_due_publishes_fourth_post_same_local_day(tmp_path, monkeypatch, mocker):
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(4):
        _queued(led, cfg, pid=f"q{i}", cid=f"cq{i}", when="2020-01-01T00:00:00Z")
    _http_media(led, *[f"q{i}" for i in range(4)]); _stub_ok_poster(mocker, cfg)
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    summary = publish_due(cfg, now=_quota_now())
    assert summary["published"] == 4
    assert "quota_skipped" not in summary
    assert all(Ledger.load(cfg).posts[f"q{i}"].state is PostState.published for i in range(4))


def test_publish_due_second_pass_still_publishes_without_daily_quota(tmp_path, monkeypatch, mocker):
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for i in range(8):
        _queued(led, cfg, pid=f"q{i}", cid=f"cq{i}", when="2020-01-01T00:00:00Z")
    _http_media(led, *[f"q{i}" for i in range(8)]); _stub_ok_poster(mocker, cfg)
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    now = _quota_now()
    first = publish_due(cfg, now=now)
    second = publish_due(cfg, now=now)
    assert first["published"] == 8 and second["published"] == 0
    assert "quota_skipped" not in first and "quota_skipped" not in second
    assert sum(1 for p in Ledger.load(cfg).posts.values() if p.state is PostState.published) == 8

def test_publish_due_refuses_a_post_under_retired_lineage(tmp_path, monkeypatch, mocker):
    # THE publish-side lineage guard, now asking `Ledger.can_promote` (the owner) instead of hand-copying the
    # predicate. review_buckets, approve_account/approve_batch, single approve and crosspost refuse a
    # retired-lineage post too; publish_due is the only one of them that reaches a real platform. An approval
    # granted BEFORE the pipeline dropped the moment is stale, and `queued` means "ships on the next due
    # sweep", so without this the operator's old click still publishes lineage the pipeline abandoned. All
    # THREE refusal shapes run alongside a healthy post in the SAME pass, so a blanket refusal cannot pass this
    # test either. The MISSING-CLIP case is class closure, not a live repro: _delete_moment_cascade refuses to
    # pop a clip while a protected post hangs off it, so no live row reaches that shape — but the old hand-copy
    # (`c is not None and ...`) would have SHIPPED one, and can_promote fails CLOSED on it.
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, cid in (("p_ok", "c_ok"), ("p_clip", "c_clip"), ("p_mom", "c_mom"), ("p_gone", "c_gone")):
        _queued(led, cfg, pid=pid, cid=cid, when="2020-01-01T00:00:00Z")
    led.add_moment(Moment(id="mom_ret", parent_id="src_1", start=0.0, end=5.0,
                          reason="dropped by a re-decision", state=MomentState.retired))
    led.retire_clip("c_clip")                          # predicate 1: the CLIP is retired
    led.clips["c_mom"].parent_id = "mom_ret"           # predicate 2: the parent MOMENT is retired
    led.clips.pop("c_gone")                            # predicate 3: p_gone's parent_id names NO clip row
    _http_media(led, "p_ok", "p_clip", "p_mom", "p_gone"); _stub_ok_poster(mocker, cfg)   # _http_media saves
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    summary = publish_due(cfg, now=_quota_now())
    assert summary["due"] == 1 and summary["published"] == 1 and summary["skipped_retired_lineage"] == 3
    led = Ledger.load(cfg)
    assert led.posts["p_ok"].state is PostState.published           # the control still ships
    for pid in ("p_clip", "p_mom", "p_gone"):                       # refused, and ZERO mutation on the skip
        assert led.posts[pid].state is PostState.queued and led.posts[pid].submission_started_at is None


# ---- F6-I / F6-T (MOL-980 / MOL-982): non-active handles stay queued ----

def _acct_row(handle, status, account_id="98432"):
    """Explicit accounts.json row. F6-T PIN: write status; do not rely on the model default."""
    return {"handle": handle, "account_id": account_id, "platforms": ["instagram"],
            "status": status, "backends": {"instagram": "postiz"},
            "integrations": {"instagram": account_id}}


def _write_accounts(cfg, rows):
    import json
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


def test_publish_due_planned_live_creds_stay_queued(tmp_path, monkeypatch, mocker):
    # F6-T: queued + planned + live Postiz creds → skip, left queued. Poster must not run.
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [_acct_row("@a", "planned")])
    _queued(led, cfg, pid="p_plan", cid="c_plan", when="2020-01-01T00:00:00Z")
    _http_media(led, "p_plan")
    poster = mocker.patch("fanops.post.run.get_poster")
    ensure = mocker.patch("fanops.postiz_lifecycle.ensure_up")
    summary = publish_due(cfg, now=_quota_now())
    assert summary["published"] == 0 and summary["skipped_not_active"] >= 1
    assert "quota_skipped" not in summary
    p = Ledger.load(cfg).posts["p_plan"]
    assert p.state is PostState.queued and p.submission_started_at is None
    assert "skip_account_not_active" in cfg.log_path.read_text()
    poster.assert_not_called()
    ensure.assert_not_called()                                 # all-dead due list must not start Postiz


def test_publish_due_active_handle_still_ships(tmp_path, monkeypatch, mocker):
    # F6-T sibling: same fixture with status=active still ships (happy path with an explicit row).
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [_acct_row("@a", "active")])
    _queued(led, cfg, pid="p_ok", cid="c_ok", when="2020-01-01T00:00:00Z")
    _http_media(led, "p_ok"); _stub_ok_poster(mocker, cfg)
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    summary = publish_due(cfg, now=_quota_now())
    assert summary["published"] == 1 and summary["skipped_not_active"] == 0
    assert "quota_skipped" not in summary
    assert Ledger.load(cfg).posts["p_ok"].state is PostState.published


def test_publish_due_mixed_planned_and_active(tmp_path, monkeypatch, mocker):
    # F6-T: one planned + one active in the same pass → only the active handle publishes.
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [_acct_row("@dead", "planned"), _acct_row("@live", "active")])
    _queued(led, cfg, pid="p_dead", cid="c_dead", when="2020-01-01T00:00:00Z", account="dead")
    _queued(led, cfg, pid="p_live", cid="c_live", when="2020-01-01T00:00:00Z", account="live")
    _http_media(led, "p_dead", "p_live"); _stub_ok_poster(mocker, cfg)
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    summary = publish_due(cfg, now=_quota_now())
    assert summary["published"] == 1 and summary["skipped_not_active"] >= 1
    assert "quota_skipped" not in summary
    led = Ledger.load(cfg)
    assert led.posts["p_live"].state is PostState.published
    assert led.posts["p_dead"].state is PostState.queued and led.posts["p_dead"].submission_started_at is None
    assert "skip_account_not_active" in cfg.log_path.read_text()


def test_publish_due_warming_and_retired_stay_queued(tmp_path, monkeypatch, mocker):
    # F6-I: still skip warming / retired when a row exists (same path as planned).
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [_acct_row("@warm", "warming"), _acct_row("@gone", "retired")])
    _queued(led, cfg, pid="p_warm", cid="c_warm", when="2020-01-01T00:00:00Z", account="warm")
    _queued(led, cfg, pid="p_gone", cid="c_gone", when="2020-01-01T00:00:00Z", account="gone")
    _http_media(led, "p_warm", "p_gone")
    poster = mocker.patch("fanops.post.run.get_poster")
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    summary = publish_due(cfg, now=_quota_now())
    assert summary["published"] == 0 and summary["skipped_not_active"] == 2
    led = Ledger.load(cfg)
    assert led.posts["p_warm"].state is PostState.queued and led.posts["p_gone"].state is PostState.queued
    poster.assert_not_called()


def test_publish_due_planned_logs_once_per_account(tmp_path, monkeypatch, mocker):
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [_acct_row("@a", "planned")])
    _queued(led, cfg, pid="p1", cid="c1", when="2020-01-01T00:00:00Z")
    _queued(led, cfg, pid="p2", cid="c2", when="2020-01-01T00:00:00Z")
    _http_media(led, "p1", "p2")
    poster = mocker.patch("fanops.post.run.get_poster")
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    summary = publish_due(cfg, now=_quota_now())
    assert summary["published"] == 0 and summary["skipped_not_active"] == 2
    recs = [json.loads(line) for line in cfg.log_path.read_text().splitlines() if line.strip()
            and "skip_account_not_active" in line]
    assert len(recs) == 1
    assert recs[0]["account"] == "a" and recs[0]["n"] == "2"
    poster.assert_not_called()


def test_publish_post_planned_does_not_claim(tmp_path, monkeypatch, mocker):
    # F6-T: Studio Publish-now inherits the claim guard via _publish_one.
    from fanops.post.run import publish_post
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [_acct_row("@a", "planned")])
    _queued(led, cfg, pid="p_now", cid="c_now", when="2020-01-01T00:00:00Z")
    _http_media(led, "p_now")
    poster = mocker.patch("fanops.post.run.get_poster")
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    assert publish_post(cfg, "p_now") is None
    p = Ledger.load(cfg).posts["p_now"]
    assert p.state is PostState.queued and p.submission_started_at is None
    assert "skip_account_not_active" in cfg.log_path.read_text()
    poster.assert_not_called()


def test_publish_due_stops_claiming_an_integration_after_429(tmp_path, monkeypatch, mocker):
    """First 429 fails that post; the next due post on the SAME account_id stays queued."""
    _live(monkeypatch)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _queued(led, cfg, pid="a1", cid="c_a1", when="2020-01-01T00:00:00Z")
    _queued(led, cfg, pid="a2", cid="c_a2", when="2020-01-01T00:00:01Z")
    _queued(led, cfg, pid="b1", cid="c_b1", when="2020-01-01T00:00:02Z")
    led.posts["b1"].account_id = "other_ig"
    _http_media(led, "a1", "a2", "b1")
    seen: list[str] = []

    class _RateThenOk:
        def __init__(self):
            self._hit_98432 = False

        def publish(self, led_, post_id):
            seen.append(post_id)
            if led_.posts[post_id].account_id == "98432" and not self._hit_98432:
                self._hit_98432 = True
                led_.set_post_state(post_id, PostState.failed, error_kind=ErrorKind.rate_limit,
                                    error_reason="postiz 429 (body withheld)")
                return led_
            led_.posts[post_id] = led_.posts[post_id].model_copy(update={"state": PostState.submitted})
            led_.posts[post_id].submission_id = "s"
            led_.posts[post_id].public_url = _LIVE_PERMALINK
            return led_

    mocker.patch("fanops.post.run.get_poster", return_value=_RateThenOk())
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    after = Ledger.load(cfg).posts
    same = ("a1", "a2")
    failed = [pid for pid in same if after[pid].state is PostState.failed]
    queued = [pid for pid in same if after[pid].state is PostState.queued]
    assert failed == [seen[0]] and len(queued) == 1
    assert after[failed[0]].error_kind is ErrorKind.rate_limit
    assert after[queued[0]].error_kind is None
    assert after["b1"].state is PostState.published
    assert queued[0] not in seen


def test_ensure_media_reuploads_foreign_https(tmp_path, monkeypatch, mocker):
    _live(monkeypatch)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_MEDIA_PUBLIC_BASE", "https://cdn.example/clips")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _queued(led, cfg, pid="m1", cid="c_m1", when="2020-01-01T00:00:00Z")
    led.posts["m1"].media_urls = [
        "f3f8e0b0-cc86-4cb0-b15c-67088db01581|https://molhams-macbook-pro-2.tail72be94.ts.net/uploads/x.mp4"]
    led.save()
    mocker.patch("fanops.post.run.get_media_uploader",
                 return_value=lambda *a, **k: "new|https://uploads.postiz.com/x.mp4")
    from fanops.post.run import _ensure_media
    led = Ledger.load(cfg)
    _ensure_media(led, cfg, led.posts["m1"], "postiz")
    assert "tail72be94" not in led.posts["m1"].media_urls[0]
    assert "uploads.postiz.com" in led.posts["m1"].media_urls[0]


def test_ensure_media_reuploads_zernio_temp_https(tmp_path, monkeypatch, mocker):
    _live(monkeypatch)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    _queued(led, cfg, pid="m1", cid="c_m1", when="2020-01-01T00:00:00Z")
    led.posts["m1"].media_urls = [
        "https://storage.zernio.com/temp/1752_abc_v.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256"]
    led.save()
    mocker.patch("fanops.post.run.get_media_uploader",
                 return_value=lambda *a, **k: "https://media.zernio.com/m/fresh.mp4")
    from fanops.post.run import _ensure_media
    led = Ledger.load(cfg)
    _ensure_media(led, cfg, led.posts["m1"], "zernio")
    assert "/temp/" not in led.posts["m1"].media_urls[0]
    assert "media.zernio.com/m/" in led.posts["m1"].media_urls[0]


def test_publish_due_no_account_row_does_not_apply_guard(tmp_path, monkeypatch, mocker):
    # F6-I PIN: missing / unknown handle → do not apply this guard (empty-registry parity).
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [_acct_row("@other", "planned")])   # a row exists, but not for this post's handle
    _queued(led, cfg, pid="p_unk", cid="c_unk", when="2020-01-01T00:00:00Z", account="a")
    _http_media(led, "p_unk"); _stub_ok_poster(mocker, cfg)
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    summary = publish_due(cfg, now=_quota_now())
    assert summary["published"] == 1 and summary["skipped_not_active"] == 0
    assert Ledger.load(cfg).posts["p_unk"].state is PostState.published


def test_publish_one_accounts_none_skips_status_guard(tmp_path, monkeypatch, mocker):
    # F6-I PIN: accounts is None (internal test callers) must not apply the status check.
    from fanops.post.run import _publish_one
    _live(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_accounts(cfg, [_acct_row("@a", "planned")])
    _queued(led, cfg, pid="p_int", cid="c_int", when="2020-01-01T00:00:00Z")
    _http_media(led, "p_int"); _stub_ok_poster(mocker, cfg)
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    assert _publish_one(cfg, "p_int", "postiz", accounts=None) == PostState.published.value
    assert Ledger.load(cfg).posts["p_int"].state is PostState.published
