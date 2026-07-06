# tests/test_publish_transient_retry.py — MOL-115: transient publish failures retry with backoff
# then park needs_reconcile (recoverable); permanent 4xx stays failed; idempotency skips re-POST.
import pytest
import requests as _rq
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.post.run import _publish_one, _is_transient_publish_error


def _live_zernio(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.setenv("FANOPS_LIVE", "1")


def _queued(cfg, pid="p1", cid="c1", *, sub=None):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="@tk", account_id="z1", platform=Platform.tiktok,
                          caption="c", scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued,
                          media_urls=["https://cdn/v.mp4"], public_url="dryrun://p1",
                          submission_id=sub))


def test_is_transient_publish_error_classifies():
    assert _is_transient_publish_error(_rq.exceptions.ConnectionError("dropped")) is True
    assert _is_transient_publish_error(_rq.exceptions.Timeout("timed out")) is True
    assert _is_transient_publish_error(RuntimeError("Zernio upload failed (503) — body withheld")) is True
    assert _is_transient_publish_error(RuntimeError("postiz upload failed (422) — body withheld")) is False
    from fanops.errors import ZernioAuthError
    assert _is_transient_publish_error(ZernioAuthError("401")) is False


def test_transient_upload_retries_then_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    # ConnectionError during media ensure → retry → exhausted → needs_reconcile, NOT failed.
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _queued(cfg)
    import fanops.post.run as run
    calls = {"n": 0}
    def boom(*a, **kw):
        calls["n"] += 1
        raise _rq.exceptions.ConnectionError("HTTPSConnectionPool(host='zernio.com', port=443): Max retries exceeded")
    mocker.patch("fanops.post.run._ensure_media", side_effect=boom)
    mocker.patch("fanops.post.run.time.sleep", return_value=None)   # no real backoff in unit test
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.needs_reconcile, f"expected needs_reconcile, got {p.state}"
    assert "transient" in (p.error_reason or "").lower() or "connection" in (p.error_reason or "").lower()
    assert calls["n"] == run._PUBLISH_TRANSIENT_MAX   # retried to exhaustion


def test_permanent_4xx_fails_no_retry(tmp_path, monkeypatch, mocker):
    # A permanent validation/auth-ish 4xx during upload → failed immediately, no retry storm.
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _queued(cfg)
    calls = {"n": 0}
    def boom(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("Zernio upload failed (422) — body withheld")
    mocker.patch("fanops.post.run._ensure_media", side_effect=boom)
    mocker.patch("fanops.post.run.time.sleep", return_value=None)
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.failed
    assert calls["n"] == 1   # no retry on permanent 4xx


def test_idempotency_skips_resubmit_when_submission_id_exists(tmp_path, monkeypatch, mocker):
    # A post that already has a real submission_id must NOT be re-POSTed on retry/recovery.
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _queued(cfg, sub="z_existing_1")
    import fanops.post.run as run
    mocker.patch("fanops.post.run._ensure_media", return_value=None)
    gp = mocker.patch.object(run, "get_poster")
    _publish_one(cfg, "p1", "zernio")
    gp.assert_not_called()   # poster.publish never invoked — no double-submit


def test_zernio_connection_error_retries_before_needs_reconcile(tmp_path, monkeypatch, mocker):
    # Poster-level: ConnectionError on POST /posts retries, then parks needs_reconcile (not failed).
    from fanops.post.zernio import ZernioPoster, _PUBLISH_TRANSIENT_MAX
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    cfg = Config(root=tmp_path)
    _queued(cfg)
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
    assert calls["n"] == _PUBLISH_TRANSIENT_MAX


def test_zernio_401_fails_not_retried(tmp_path, monkeypatch, mocker):
    from fanops.post.zernio import ZernioPoster
    from fanops.errors import ZernioAuthError
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    cfg = Config(root=tmp_path)
    _queued(cfg)
    led = Ledger.load(cfg)
    with Ledger.transaction(cfg) as lg:
        lg.posts["p1"].state = PostState.submitting
    led = Ledger.load(cfg)
    class _R:
        status_code = 401
        text = "denied"
        def json(self): return {}
    calls = {"n": 0}
    def post_side(*a, **kw):
        calls["n"] += 1
        return _R()
    mocker.patch("fanops.post.zernio.requests.post", side_effect=post_side)
    with pytest.raises(ZernioAuthError):
        ZernioPoster(cfg).publish(led, "p1")
    assert calls["n"] == 1
