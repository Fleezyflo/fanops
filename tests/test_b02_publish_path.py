# B02 publish-path integrity: at-most-once (H01/H02) + only-when-due (M07/M08) + hardening (M09/L17)
import requests as _rq
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.post.run import publish_due
from fanops.timeutil import schedule_utc


def _live_postiz(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.setenv("FANOPS_LIVE", "1")


def _live_zernio(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.setenv("FANOPS_LIVE", "1")


def _seed_queued(cfg, pid="p1", cid="c1", *, sched="2020-01-01T00:00:00Z", sub=None):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
                          caption="c", scheduled_time=sched, state=PostState.queued,
                          media_urls=["https://cdn/v.mp4"], public_url="dryrun://p1",
                          submission_id=sub))


# ---- H01: retry ConnectTimeout only, not ConnectionError ----
def test_postiz_connection_error_single_attempt_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    from fanops.post.postiz import PostizPoster
    _live_postiz(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg)
    led = Ledger.load(cfg)
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"].state = PostState.submitting
    led = Ledger.load(cfg)
    calls = {"n": 0}
    def post_side(*a, **kw):
        calls["n"] += 1
        raise _rq.exceptions.ConnectionError("connection dropped")
    mocker.patch("fanops.post.postiz.requests.post", side_effect=post_side)
    mocker.patch("fanops.post.postiz.time.sleep", return_value=None)
    PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile
    assert calls["n"] == 1


def test_postiz_connect_timeout_retries_then_succeeds(tmp_path, monkeypatch, mocker):
    from fanops.post.postiz import PostizPoster, _PUBLISH_TRANSIENT_MAX
    _live_postiz(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg)
    led = Ledger.load(cfg)
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"].state = PostState.submitting
    led = Ledger.load(cfg)
    calls = {"n": 0}
    class _R:
        status_code = 201
        def json(self): return {"id": "postiz_ok"}
    def post_side(*a, **kw):
        calls["n"] += 1
        if calls["n"] < _PUBLISH_TRANSIENT_MAX:
            raise _rq.exceptions.ConnectTimeout("timed out")
        return _R()
    mocker.patch("fanops.post.postiz.requests.post", side_effect=post_side)
    mocker.patch("fanops.post.postiz.time.sleep", return_value=None)
    PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.submitted
    assert calls["n"] == _PUBLISH_TRANSIENT_MAX


def test_zernio_connection_error_single_attempt_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    from fanops.post.zernio import ZernioPoster
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg)
    led = Ledger.load(cfg)
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"].state = PostState.submitting
    led = Ledger.load(cfg)
    calls = {"n": 0}
    def post_side(*a, **kw):
        calls["n"] += 1
        raise _rq.exceptions.ConnectionError("connection dropped")
    mocker.patch("fanops.post.zernio.requests.post", side_effect=post_side)
    mocker.patch("fanops.post.zernio.time.sleep", return_value=None)
    ZernioPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile
    assert calls["n"] == 1


def test_zernio_connect_timeout_retries_then_succeeds(tmp_path, monkeypatch, mocker):
    from fanops.post.zernio import ZernioPoster, _PUBLISH_TRANSIENT_MAX
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg)
    led = Ledger.load(cfg)
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"].state = PostState.submitting
    led = Ledger.load(cfg)
    calls = {"n": 0}
    class _R:
        status_code = 201
        def json(self): return {"id": "z_ok"}
    def post_side(*a, **kw):
        calls["n"] += 1
        if calls["n"] < _PUBLISH_TRANSIENT_MAX:
            raise _rq.exceptions.ConnectTimeout("timed out")
        return _R()
    mocker.patch("fanops.post.zernio.requests.post", side_effect=post_side)
    mocker.patch("fanops.post.zernio.time.sleep", return_value=None)
    ZernioPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.submitted
    assert calls["n"] == _PUBLISH_TRANSIENT_MAX


# ---- H02: heal -> needs_reconcile; publish_due skips ----
def test_heal_stranded_submitting_parks_needs_reconcile(tmp_path):
    from fanops.reconcile import heal_stranded_submitting
    from fanops.timeutil import iso_z
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    old = iso_z(datetime.now(timezone.utc) - timedelta(hours=2))
    led.add_post(Post(id="stuck", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, scheduled_time=old, submission_id=None))
    led.save()
    assert heal_stranded_submitting(cfg) == 1
    p = Ledger.load(cfg).posts["stuck"]
    assert p.state is PostState.needs_reconcile
    assert "ambiguous" in (p.error_reason or "").lower() or "may be live" in (p.error_reason or "").lower()


def test_publish_due_skips_healed_needs_reconcile_post(tmp_path, monkeypatch, mocker):
    from fanops.reconcile import heal_stranded_submitting
    from fanops.timeutil import iso_z
    _live_postiz(monkeypatch)
    cfg = Config(root=tmp_path)
    old = iso_z(datetime.now(timezone.utc) - timedelta(hours=2))
    f = cfg.clips / "c.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="c", parent_id="m", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="stuck", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, scheduled_time=old, submission_id=None,
                      media_urls=["https://cdn/v.mp4"]))
    led.save()
    heal_stranded_submitting(cfg)
    gp = mocker.patch("fanops.post.run.get_poster")
    publish_due(cfg, now=iso_z(datetime.now(timezone.utc)))
    gp.assert_not_called()
    assert Ledger.load(cfg).posts["stuck"].state is PostState.needs_reconcile


# ---- M08: defer between snapshot and claim ----
def test_publish_defer_between_snapshot_and_claim(tmp_path, monkeypatch, mocker):
    _live_postiz(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg)
    calls = {"n": 0}
    def gate(post, cutoff):
        calls["n"] += 1
        return True if calls["n"] == 1 else False
    monkeypatch.setattr("fanops.post.run.is_scheduled_due", gate)
    mocker.patch("fanops.post.run.get_poster")
    publish_due(cfg, now="2020-01-02T00:00:00Z")
    assert Ledger.load(cfg).posts["p1"].state is PostState.queued
    assert calls["n"] >= 2


# ---- M07: naive schedule handling ----
def test_schedule_utc_naive_is_canonical_utc():
    dt = schedule_utc("2026-06-01 09:00")
    assert dt is not None and dt.tzinfo is not None
    assert dt.hour == 9


def test_publish_due_naive_past_publishes(tmp_path, monkeypatch, mocker):
    _live_postiz(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg, sched="2026-06-01 09:00")
    class FakePoster:
        def publish(self, led, post_id):
            led.posts[post_id].state = PostState.submitted
            led.posts[post_id].public_url = "https://ig.example/p/1"
            return led
    mocker.patch("fanops.post.run.get_poster", return_value=FakePoster())
    publish_due(cfg, now="2026-06-02T00:00:00Z")
    assert Ledger.load(cfg).posts["p1"].state is PostState.published


def test_publish_due_naive_future_stays_queued(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg, sched="2099-06-01 09:00")
    gp = mocker.patch("fanops.post.run.get_poster")
    publish_due(cfg, now="2026-06-02T00:00:00Z")
    gp.assert_not_called()
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.queued
    assert p.state is not PostState.failed


# ---- M09: cross-backend cache miss ----
def test_media_cache_postiz_rejects_bare_https(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c", parent_id="m", path=str(f), state=ClipState.queued,
                      media_url="https://cdn.zernio.test/v.mp4"))
    up = mocker.patch("fanops.post.get_media_uploader",
                      return_value=lambda c, p, **_kw: "img1|https://cdn.postiz.test/v.mp4")
    from fanops.post.media import ensure_clip_media
    url = ensure_clip_media(led, cfg, "c", backend="postiz")
    assert url == "img1|https://cdn.postiz.test/v.mp4"
    assert up.call_count == 1


def test_media_cache_zernio_rejects_postiz_composite_and_localhost(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c", parent_id="m", path=str(f), state=ClipState.queued,
                      media_url="img1|https://cdn.postiz.test/v.mp4"))
    up = mocker.patch("fanops.post.get_media_uploader",
                      return_value=lambda c, p, **_kw: "https://media.zernio.test/v.mp4")
    from fanops.post.media import ensure_clip_media
    url = ensure_clip_media(led, cfg, "c", backend="zernio")
    assert url == "https://media.zernio.test/v.mp4"
    assert up.call_count == 1
    led.clips["c"].media_url = "https://127.0.0.1:4007/x.mp4"
    url2 = ensure_clip_media(led, cfg, "c", backend="zernio")
    assert url2 == "https://media.zernio.test/v.mp4"
    assert up.call_count == 2


# ---- L17: mirror never calls read_bytes ----
def test_mirror_media_to_r2_never_read_bytes(tmp_path, monkeypatch, mocker):
    from fanops.post.postiz import _mirror_media_to_r2
    monkeypatch.setenv("FANOPS_MEDIA_PUBLIC_BASE", "https://pub.r2.dev/fanops")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "clips")
    cfg = Config(root=tmp_path)
    f = tmp_path / "v.mp4"; f.write_bytes(b"VIDEO")
    rb = mocker.patch("pathlib.Path.read_bytes", side_effect=AssertionError("read_bytes must not be called"))
    mocker.patch("fanops.post.postiz.requests.put", return_value=type("_R", (), {"status_code": 200})())
    url = _mirror_media_to_r2(cfg, f)
    assert url.startswith("https://pub.r2.dev/fanops/fanops/")
    rb.assert_not_called()
