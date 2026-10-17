# B02 publish-path integrity: at-most-once (H01/H02) + only-when-due (M07/M08) + hardening (M09/L17)
import requests as _rq
from datetime import datetime, timezone, timedelta
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform, Moment, MomentState
from fanops.post.run import publish_due
from fanops.timeutil import schedule_utc


class _R:
    def __init__(self, code, body=None, text=""):
        self.status_code = code
        self._b = {} if body is None else body
        self.text = text
        self.headers = {}
    def json(self):
        return self._b


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
        # Materialize `mom_1`: the clip named it but no such row was ever added, leaving a DANGLING ancestor
        # production cannot build. Harmless while the publish guard failed OPEN on a missing row; publish_due
        # now asks Ledger.can_promote, which fails CLOSED.
        led.add_moment(Moment(id="mom_1", parent_id="src_1", start=0.0, end=7.0, reason="worth posting",
                              state=MomentState.clipped))
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        # created_at: required to publish on the Zernio path (report 11 §8.4) — the per-incarnation
        # discriminator in the x-request-id. Harmless for the Postiz path, and it makes the fixture match
        # what production actually mints (every mint site stamps it).
        led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
                          caption="c", scheduled_time=sched, state=PostState.queued,
                          post_type="post", created_at="2026-07-16T13:31:00Z",
                          media_urls=["https://cdn/v.mp4"], public_url="dryrun://p1",
                          submission_id=sub))


def _postiz_http(mocker, *, on_post=None):
    """Vendor-edge HTTP. GET /integrations is the real list_integrations path — not a fanops.* stub."""
    log = {"posts": 0, "uploads": 0}
    def _get(url, **kw):
        if "integrations" in str(url):
            return _R(200, [{"id": "1", "name": "ig", "identifier": "instagram-standalone"}])
        return _R(200, {"posts": []})
    def _post(url, **kw):
        u = str(url)
        if "/upload" in u:
            log["uploads"] += 1
            return _R(201, {"id": "img1", "path": "https://uploads.postiz.com/v.mp4"})
        log["posts"] += 1
        if on_post is not None:
            return on_post(url, **kw)
        return _R(201, {"id": "postiz_1"})
    mocker.patch("requests.get", side_effect=_get)
    mocker.patch("requests.post", side_effect=_post)
    mocker.patch("requests.put", return_value=_R(200, {}))
    return log


# ---- H01: Postiz ConnectTimeout does not retry; ConnectionError parks immediately ----
def test_postiz_connection_error_single_attempt_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    from fanops.post.postiz import PostizPoster
    _live_postiz(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg)
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"] = lg.posts["p1"].model_copy(update={"state": PostState.submitting})
    led = Ledger.load(cfg)
    calls = {"n": 0}
    def post_side(url, **kw):
        if "/posts" in str(url) and "/upload" not in str(url):
            calls["n"] += 1
            raise _rq.exceptions.ConnectionError("connection dropped")
        return _R(201, {"id": "img1", "path": "https://uploads.postiz.com/v.mp4"})
    _postiz_http(mocker, on_post=post_side)
    PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile
    assert led.posts["p1"].state is not PostState.published
    assert calls["n"] == 1


def test_zernio_connection_error_single_attempt_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    from fanops.post.zernio import ZernioPoster
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg)
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"] = lg.posts["p1"].model_copy(update={"state": PostState.submitting})
    led = Ledger.load(cfg)
    calls = {"n": 0}
    def post_side(url, **kw):
        calls["n"] += 1
        raise _rq.exceptions.ConnectionError("connection dropped")
    mocker.patch("fanops.post.zernio.requests.post", side_effect=post_side)
    ZernioPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile
    assert led.posts["p1"].state is not PostState.published
    assert calls["n"] == 1


def test_zernio_connect_timeout_retries_then_succeeds(tmp_path, monkeypatch, mocker):
    from fanops.post.zernio import ZernioPoster, _MAX_RETRIES
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg)
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"] = lg.posts["p1"].model_copy(update={"state": PostState.submitting})
    led = Ledger.load(cfg)
    calls = {"n": 0}
    def post_side(url, **kw):
        calls["n"] += 1
        if calls["n"] < _MAX_RETRIES:
            raise _rq.exceptions.ConnectTimeout("timed out")
        return _R(201, {"_id": "z_ok"})
    mocker.patch("fanops.post.zernio.requests.post", side_effect=post_side)
    ZernioPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.submitted
    assert led.posts["p1"].state is not PostState.published
    assert calls["n"] == _MAX_RETRIES


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
    led.add_moment(Moment(id="m", parent_id="src_1", start=0.0, end=7.0, reason="worth posting",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="c", parent_id="m", path=str(f), state=ClipState.queued))
    led.add_post(Post(id="stuck", parent_id="c", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.submitting, scheduled_time=old, submission_id=None,
                      post_type="post", media_urls=["https://cdn/v.mp4"]))
    led.save()
    heal_stranded_submitting(cfg)
    log = _postiz_http(mocker)
    publish_due(cfg, now=iso_z(datetime.now(timezone.utc)))
    assert log["posts"] == 0
    p = Ledger.load(cfg).posts["stuck"]
    assert p.state is PostState.needs_reconcile
    assert p.state is not PostState.published


# ---- M07: naive schedule handling ----
def test_schedule_utc_naive_is_canonical_utc():
    dt = schedule_utc("2026-06-01 09:00")
    assert dt is not None and dt.tzinfo is not None
    assert dt.hour == 9


def test_publish_due_naive_past_publishes(tmp_path, monkeypatch, mocker):
    # A due naive schedule must enter the real publish path. Leftover dryrun:// is not a permalink.
    _live_postiz(monkeypatch)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg, sched="2026-06-01 09:00")
    log = _postiz_http(mocker)
    publish_due(cfg, now="2026-06-02T00:00:00Z")
    p = Ledger.load(cfg).posts["p1"]
    assert log["posts"] >= 1
    assert p.state is not PostState.queued
    assert p.state is not PostState.published


def test_publish_due_naive_future_stays_queued(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    cfg = Config(root=tmp_path)
    _seed_queued(cfg, sched="2099-06-01 09:00")
    sent = {"n": 0}
    def boom(url, **kw):
        sent["n"] += 1
        raise AssertionError(f"future post must not POST: {url}")
    mocker.patch("requests.post", side_effect=boom)
    mocker.patch("requests.get", side_effect=boom)
    publish_due(cfg, now="2026-06-02T00:00:00Z")
    p = Ledger.load(cfg).posts["p1"]
    assert sent["n"] == 0
    assert p.state is PostState.queued
    assert p.state is not PostState.failed
    assert p.state is not PostState.published


# ---- M09: cross-backend cache miss ----
def test_media_cache_postiz_rejects_bare_https(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c", parent_id="m", path=str(f), state=ClipState.queued,
                      media_url="https://cdn.zernio.test/v.mp4"))
    uploads = {"n": 0}
    def _post(url, **kw):
        if "/upload" in str(url):
            uploads["n"] += 1
            return _R(201, {"id": "img1", "path": "https://cdn.postiz.test/v.mp4"})
        raise AssertionError(url)
    mocker.patch("requests.post", side_effect=_post)
    from fanops.post.media import ensure_clip_media
    url = ensure_clip_media(led, cfg, "c", backend="postiz")
    assert url == "img1|https://cdn.postiz.test/v.mp4"
    assert uploads["n"] == 1


def test_media_cache_zernio_rejects_postiz_composite_and_localhost(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "c.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="c", parent_id="m", path=str(f), state=ClipState.queued,
                      media_url="img1|https://cdn.postiz.test/v.mp4"))
    presigns = {"n": 0}
    def _post(url, **kw):
        if "/media/presign" in str(url):
            presigns["n"] += 1
            return _R(200, {"uploadUrl": "https://signed.example/u", "publicUrl": "https://media.zernio.test/v.mp4"})
        raise AssertionError(url)
    mocker.patch("fanops.post.zernio.requests.post", side_effect=_post)
    mocker.patch("fanops.post.zernio.requests.put", return_value=_R(200, {}))
    from fanops.post.media import ensure_clip_media
    url = ensure_clip_media(led, cfg, "c", backend="zernio")
    assert url == "https://media.zernio.test/v.mp4"
    assert presigns["n"] == 1
    led.clips["c"].media_url = "https://127.0.0.1:4007/x.mp4"
    url2 = ensure_clip_media(led, cfg, "c", backend="zernio")
    assert url2 == "https://media.zernio.test/v.mp4"
    assert presigns["n"] == 2


# ---- L17: mirror streams the file handle (never Path.read_bytes of the whole clip) ----
def test_mirror_media_to_r2_streams_file_handle(tmp_path, monkeypatch, mocker):
    from fanops.post.postiz import _mirror_media_to_r2
    monkeypatch.setenv("FANOPS_MEDIA_PUBLIC_BASE", "https://pub.r2.dev/fanops")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "clips")
    cfg = Config(root=tmp_path)
    f = tmp_path / "v.mp4"; f.write_bytes(b"VIDEO")
    cap = {}
    def _put(url, **kw):
        cap["data"] = kw.get("data")
        return type("_R", (), {"status_code": 200})()
    mocker.patch("fanops.post.postiz.requests.put", side_effect=_put)
    url = _mirror_media_to_r2(cfg, f)
    assert url.startswith("https://pub.r2.dev/fanops/fanops/")
    assert hasattr(cap["data"], "read")
