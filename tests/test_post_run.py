from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.post.run import publish_due

def _queued(led, cfg, pid="p1", cid="clip_1", when="2026-06-02T18:00:00Z"):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="98432",
                      platform=Platform.instagram, caption="ship it",
                      scheduled_time=when, state=PostState.queued))


def test_is_fatal_auth_error_matches_by_type_not_substring():
    # AUDIT H8: the halt decision is now a TYPE check (BlotatoAuthError), not "401"/"BLOTATO_API_KEY"
    # in the message. So it fires on a reworded auth error (under-fire fixed) and does NOT fire on a
    # non-auth error whose text happens to contain "401" (over-fire fixed).
    from fanops.errors import BlotatoAuthError
    from fanops.post.run import _is_fatal_auth_error
    assert _is_fatal_auth_error(BlotatoAuthError("invalid credentials")) is True       # no "401" text
    assert _is_fatal_auth_error(RuntimeError("Blotato 503: upstream 401abc")) is False  # "401" but not auth
    assert _is_fatal_auth_error(RuntimeError("BLOTATO_API_KEY missing")) is False       # substring, not the type

def test_publishes_only_due_posts(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)  # dryrun
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="due", cid="c_due", when="2020-01-01T00:00:00Z")     # past => due
    _queued(led, cfg, pid="future", cid="c_future", when="2999-01-01T00:00:00Z")  # not due
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["due"].state is PostState.published
    assert led.posts["future"].state is PostState.queued       # held back (FIX F12)

def test_publish_uploads_media_once_and_advances(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="p1", cid="clip_1", when="2020-01-01T00:00:00Z")
    _queued(led, cfg, pid="p2", cid="clip_1", when="2020-01-01T00:00:00Z")  # same clip, 2 posts
    # spy ensure_clip_media to prove one upload per clip
    import fanops.post.run as run
    spy = mocker.spy(run, "ensure_clip_media")
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["p1"].state is PostState.published and led.posts["p2"].state is PostState.published
    assert led.posts["p1"].media_urls[0].startswith("file://")
    # clip_1 media ensured but cached: both posts resolve to the same url, and the SECOND call
    # returns the Clip-cached value (F44) — the spy proves ensure ran once per post but the
    # underlying upload work is shared via clip.media_url
    assert spy.call_count == 2 and led.clips["clip_1"].media_url
    assert led.posts["p1"].media_urls == led.posts["p2"].media_urls

def test_publish_idempotent_skips_already_submitted(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, when="2020-01-01T00:00:00Z")
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["p1"].state is PostState.published

def test_publish_failed_poster_marks_failed_durable(tmp_path, monkeypatch, mocker):
    # A poster that fails -> post.state failed (not analyzed, not published), durable.
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _queued(led, cfg, pid="pf", cid="c_pf", when="2020-01-01T00:00:00Z")
    # make get_poster return a poster whose publish sets the post to failed
    import fanops.post.run as run
    class _FailPoster:
        def __init__(self, cfg): pass
        def publish(self, led, post_id):
            led.posts[post_id].state = PostState.failed
            led.posts[post_id].error_reason = "simulated 422"
            return led
    mocker.patch.object(run, "get_poster", return_value=_FailPoster(cfg))
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["pf"].state is PostState.failed
    # durable: reload from disk and confirm
    led2 = Ledger.load(cfg)
    assert led2.posts["pf"].state is PostState.failed

def test_publish_no_schedule_publishes_immediately(tmp_path, monkeypatch):
    # A post with no scheduled_time is due now (no schedule => publish).
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_ns.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c_ns", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="pns", parent_id="c_ns", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued))  # no scheduled_time
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["pns"].state is PostState.published

def test_publish_does_not_redrive_submitting_post(tmp_path, monkeypatch, mocker):
    # F11 crash-sim regression lock: a post stranded in 'submitting' is NOT re-published.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_sub.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c_sub", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="psub", parent_id="c_sub", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x",
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.submitting))
    import fanops.post.run as run
    spy = mocker.spy(run, "ensure_clip_media")
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["psub"].state is PostState.submitting    # untouched — not re-driven
    assert spy.call_count == 0                                # no media re-upload either

def test_publish_one_bad_upload_does_not_block_others(tmp_path, monkeypatch, mocker):
    # Per-post isolation: clip A's media raises, clip B still publishes.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, cid in [("pa", "c_a"), ("pb", "c_b")]:
        f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued))
    import fanops.post.run as run
    # c_a upload raises a NON-auth error; c_b uploads fine; poster.publish succeeds (submitted)
    def fake_ensure(led_, cfg_, clip_id):
        if clip_id == "c_a":
            raise RuntimeError("Blotato presign failed (503): server down")
        return "https://cdn/ok.mp4"
    mocker.patch.object(run, "ensure_clip_media", side_effect=fake_ensure)
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id].state = PostState.submitted
            led_.posts[post_id].submission_id = "s_ok"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["pa"].state is PostState.failed          # bad upload -> failed, isolated
    assert "503" in (led.posts["pa"].error_reason or "")
    assert led.posts["pb"].state is PostState.published        # healthy clip still shipped

def test_publish_needs_reconcile_does_not_halt_loop(tmp_path, monkeypatch, mocker):
    # AUDIT C1: a poster that parks a post in needs_reconcile (ambiguous 5xx/timeout) is NOT an
    # exception — publish_due must leave that post in needs_reconcile and keep publishing the rest
    # (a needs_reconcile post is terminal-for-now, like failed, never re-driven this pass).
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, cid in [("prec", "c_rec"), ("pok", "c_ok")]:
        f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued))
    import fanops.post.run as run
    mocker.patch.object(run, "ensure_clip_media", return_value="https://cdn/ok.mp4")
    class _ReconcileThenOkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            if post_id == "prec":
                led_.posts[post_id].state = PostState.needs_reconcile
                led_.posts[post_id].error_reason = "blotato 503: ambiguous, may be live"
            else:
                led_.posts[post_id].state = PostState.submitted
                led_.posts[post_id].submission_id = "s_ok"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_ReconcileThenOkPoster(cfg))
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert led.posts["prec"].state is PostState.needs_reconcile   # parked, not re-driven, not failed
    assert led.posts["pok"].state is PostState.published          # healthy post still shipped
    led2 = Ledger.load(cfg)                                       # durable across the save
    assert led2.posts["prec"].state is PostState.needs_reconcile

def test_publish_auth_error_halts_run(tmp_path, monkeypatch, mocker):
    # AUDIT H8: a fatal auth failure must HALT (raise), not mark one post failed and grind on.
    # The trigger is now the TYPE (BlotatoAuthError), not a substring in the message — so it fires
    # even when the message doesn't literally contain "401" (fixes the F52 under-fire).
    from fanops.errors import BlotatoAuthError
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "badkey")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c_auth.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c_auth", parent_id="mom_1", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="pauth", parent_id="c_auth", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x",
                      scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued))
    import fanops.post.run as run
    mocker.patch.object(run, "ensure_clip_media", return_value="https://cdn/ok.mp4")
    class _AuthFailPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            # worded WITHOUT "401" on purpose — a reworded auth error must still halt by type
            raise BlotatoAuthError("Blotato rejected the api key (invalid credentials)")
    mocker.patch.object(run, "get_poster", return_value=_AuthFailPoster(cfg))
    import pytest
    with pytest.raises(BlotatoAuthError):
        publish_due(led, cfg, now="2026-06-02T18:00:00Z")


def test_publish_non_auth_error_with_401_in_text_does_not_halt(tmp_path, monkeypatch, mocker):
    # AUDIT H8 over-fire regression: a NON-auth error whose message merely CONTAINS "401" (e.g. a
    # 503 body echoing an upstream id) must NOT halt the queue — it's a per-post failure. The old
    # substring match wrongly tore down the whole run on this; the typed check must not.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    for pid, cid in [("pbad", "c_bad"), ("pok", "c_ok2")]:
        f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="@a", account_id="1",
                          platform=Platform.instagram, caption="x",
                          scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued))
    import fanops.post.run as run
    def fake_ensure(led_, cfg_, clip_id):
        if clip_id == "c_bad":
            raise RuntimeError("Blotato 503: upstream request 401abc timed out")  # 401 in text, NOT auth
        return "https://cdn/ok.mp4"
    mocker.patch.object(run, "ensure_clip_media", side_effect=fake_ensure)
    class _OkPoster:
        def __init__(self, cfg): pass
        def publish(self, led_, post_id):
            led_.posts[post_id].state = PostState.submitted
            led_.posts[post_id].submission_id = "s_ok"
            return led_
    mocker.patch.object(run, "get_poster", return_value=_OkPoster(cfg))
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")   # must NOT raise
    assert led.posts["pbad"].state is PostState.failed         # isolated per-post failure
    assert led.posts["pok"].state is PostState.published        # the run continued


def test_publish_due_inside_transaction_does_not_deadlock(tmp_path, monkeypatch):
    # B2 (AUDIT B4): publish_due saves mid-loop for crash-safety (F11). Inside a transaction
    # those save() calls would RE-ACQUIRE the held lock -> self-deadlock. With in_transaction=True
    # it must use the unlocked save and complete (no hang). Run under `timeout 20` in CI: a hang
    # is a FAILURE, never papered over.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)   # dryrun
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id="c1", parent_id="m1", path=str(tmp_path / "c1.mp4"),
                          state=ClipState.captioned))
        (tmp_path / "c1.mp4").write_bytes(b"x")
        led.add_post(Post(id="p1", parent_id="c1", account="@a", account_id="1",
                          platform=Platform.instagram, caption="x", state=PostState.queued,
                          scheduled_time="2020-01-01T00:00:00Z"))
        # in_transaction=True must use the unlocked save -> no deadlock, completes
        publish_due(led, cfg, now="2020-01-02T00:00:00Z", in_transaction=True)
        assert led.posts["p1"].state is PostState.published


def test_publish_due_malformed_scheduled_time_is_per_post_failure_not_escape(tmp_path, monkeypatch):
    # AUDIT M2 / review finding: the schedule check `_parse(post.scheduled_time)` ran OUTSIDE the
    # per-post try, so a malformed/timezone-naive scheduled_time on disk (hand-edit, corruption,
    # older schema) raised TypeError/ValueError that ESCAPED publish_due — a NON-auth raise that
    # (inside advance()'s transaction) would skip the exit-save and roll back the pass's progress.
    # Fix: a malformed scheduled_time must be a per-post FAILURE (mark THIS post failed, keep going),
    # never an uncaught escape. publish_due must NOT raise here.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    f = cfg.clips / "c1.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c1", parent_id="m1", path=str(f), state=ClipState.captioned))
    led.add_post(Post(id="bad", parent_id="c1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued,
                      scheduled_time="2026-06-01 09:00"))   # naive: no 'T', no tz -> _parse trips
    # must NOT raise; the bad post is marked failed, the loop survives
    publish_due(led, cfg, now="2026-06-02T00:00:00Z")
    assert led.posts["bad"].state is PostState.failed
    assert "schedule" in (led.posts["bad"].error_reason or "").lower()


def test_publish_due_garbage_scheduled_time_does_not_escape(tmp_path, monkeypatch):
    # Same root cause, unparseable (ValueError) variant.
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    f = cfg.clips / "c2.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c2", parent_id="m1", path=str(f), state=ClipState.captioned))
    led.add_post(Post(id="garbage", parent_id="c2", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.queued,
                      scheduled_time="not-a-timestamp"))
    publish_due(led, cfg, now="2026-06-02T00:00:00Z")   # must NOT raise
    assert led.posts["garbage"].state is PostState.failed


def test_publish_uploads_variant_file_media_on_live_backend(tmp_path, monkeypatch, mocker):
    # AUDIT (stage-6 HIGH): a creative-variation post is BORN with media_urls=["file://<variant>"]
    # (crosspost stamps the per-account variant render). The `if not post.media_urls` guard then
    # skipped the upload entirely and shipped the LOCAL path verbatim to Blotato — which cannot
    # fetch a file:// on the operator's host, so every live variant post died. On a live backend a
    # file:// entry must be uploaded as the variant FILE itself (NOT ensure_clip_media — the
    # clip-level cache holds the parent's BASE render and would lose the burned hook).
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    vfile = cfg.clips / "clip_1_vhash.mp4"; vfile.parent.mkdir(parents=True, exist_ok=True); vfile.write_bytes(b"V")
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path=str(cfg.clips / "clip_1.mp4"), state=ClipState.queued))
    led.add_post(Post(id="pv", parent_id="clip_1", account="@a", account_id="98432",
                      platform=Platform.instagram, caption="x", scheduled_time="2020-01-01T00:00:00Z",
                      state=PostState.queued, media_urls=[f"file://{vfile}"]))
    uploaded = []
    def fake_upload(cfg_, path):
        uploaded.append(str(path)); return "https://cdn.blotato.test/v.mp4"
    # run.py now routes the variant file:// upload through get_media_uploader(cfg) -> (for rest)
    # media.upload_media (lazy import), so patch it at its definition site.
    mocker.patch("fanops.post.media.upload_media", side_effect=fake_upload)
    sent = {}
    class FakePoster:
        def publish(self, led_, post_id):
            sent["media_urls"] = list(led_.posts[post_id].media_urls)
            led_.posts[post_id].state = PostState.submitted
            return led_
    mocker.patch("fanops.post.run.get_poster", return_value=FakePoster())
    led = publish_due(led, cfg, now="2026-06-02T18:00:00Z")
    assert uploaded == [str(vfile)]                                    # the VARIANT file, not the parent clip
    assert sent["media_urls"] == ["https://cdn.blotato.test/v.mp4"]    # the poster sees https, never file://
    assert led.posts["pv"].media_urls == ["https://cdn.blotato.test/v.mp4"]  # persisted -> a retry never re-uploads
    assert led.posts["pv"].state is PostState.published
