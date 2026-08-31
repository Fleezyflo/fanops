# tests/test_publish_transient_network_mol125.py — MOL-125: DNS/read-timeout transients classify
# retryable; pre-send exhaustion lands failed (re-queueable), not terminal on first blip; 4xx unchanged.
import requests as _rq
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ErrorKind, Post, Clip, PostState, ClipState, Platform
from fanops.post.run import (
    _publish_one, _is_transient_publish_error, _requeue_transient_failed_for_daemon,
    _requeue_rate_limited_for_daemon, _requeue_proven_dead_for_daemon,
)
from fanops.studio.views_common import is_transient_failure
from fanops.studio.views_results import classify_failure, _RETRYABLE_FAILURES


def _live_zernio(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.setenv("FANOPS_LIVE", "1")


def _queued(cfg, pid="p1", cid="c1", *, sub=None):
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        led.add_post(Post(id=pid, parent_id=cid, account="tk", account_id="z1", platform=Platform.tiktok,
                          caption="c", scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued,
                          media_urls=["https://cdn/v.mp4"], public_url="dryrun://p1",
                          submission_id=sub))


def _fail_post(pid, reason, *, kind=None):
    return Post(id=pid, parent_id="c1", account="a", account_id="1", platform=Platform.tiktok,
                caption="x", state=PostState.failed, error_reason=reason, error_kind=kind)


def test_is_transient_publish_error_dns_and_read_timeout():
    dns = _rq.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='zernio.com', port=443): Max retries exceeded with url: /posts "
        "(Caused by NameResolutionError(\"Failed to resolve 'zernio.com'\"))")
    assert _is_transient_publish_error(dns) is True
    assert _is_transient_publish_error(_rq.exceptions.ReadTimeout("zernio.com Read timed out (read timeout=30)")) is True
    assert _is_transient_publish_error(RuntimeError("publish failed: zernio.com Read timed out (read timeout=30)")) is True


def test_is_transient_failure_reads_typed_error_kind():
    assert is_transient_failure(_fail_post("to", "publish failed: zernio.com Read timed out (read timeout=30)",
                                           kind=ErrorKind.transient)) is True
    assert is_transient_failure(_fail_post("dns", "publish failed: NameResolutionError for zernio.com",
                                           kind=ErrorKind.transient)) is True
    assert classify_failure(_fail_post("dns", "publish failed: NameResolutionError for zernio.com",
                                       kind=ErrorKind.transient)) == "transient"
    assert classify_failure(_fail_post("to", "publish failed: zernio.com Read timed out (read timeout=30)",
                                       kind=ErrorKind.transient)) == "transient"
    # Untyped legacy row: no prose fallback — unknown
    assert classify_failure(_fail_post("legacy", "publish failed: NameResolutionError for zernio.com")) == "unknown"
    assert "transient" in _RETRYABLE_FAILURES


def test_transient_pre_send_not_failed_on_first_failure(tmp_path, monkeypatch, mocker):
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _queued(cfg)
    calls = {"n": 0}
    def boom(*a, **kw):
        calls["n"] += 1
        raise _rq.exceptions.ReadTimeout("zernio.com Read timed out (read timeout=30)")
    mocker.patch("fanops.post.run._ensure_media", side_effect=boom)
    mocker.patch("fanops.post.run.time.sleep", return_value=None)
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is not PostState.failed or calls["n"] > 1
    assert calls["n"] > 1


def test_transient_pre_send_exhausted_lands_failed_requeueable(tmp_path, monkeypatch, mocker):
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _queued(cfg)
    mocker.patch("fanops.post.run._ensure_media",
                 side_effect=_rq.exceptions.ConnectionError("NameResolutionError zernio.com"))
    mocker.patch("fanops.post.run.time.sleep", return_value=None)
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.failed
    assert is_transient_failure(p)
    assert p.error_kind is ErrorKind.transient
    assert not p.submission_id


def test_permanent_4xx_still_fails_immediately(tmp_path, monkeypatch, mocker):
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
    assert calls["n"] == 1
    assert p.error_kind is not ErrorKind.transient
    assert classify_failure(p) != "transient"


def test_daemon_transient_requeue_bounded_then_stays_failed(tmp_path, monkeypatch, mocker):
    _live_zernio(monkeypatch)
    cfg = Config(root=tmp_path)
    _queued(cfg)
    with Ledger.transaction(cfg) as led:
        led.posts["p1"] = led.posts["p1"].model_copy(
            update={"state": PostState.failed, "error_kind": ErrorKind.transient,
                    "error_reason": "publish failed: NameResolutionError zernio.com",
                    "daemon_transient_retry": 0})
    import fanops.post.run as run
    from fanops.studio.views_results import operator_error
    max_d = run._DAEMON_TRANSIENT_MAX
    for i in range(max_d):
        n = _requeue_transient_failed_for_daemon(cfg)
        assert n == 1
        with Ledger.transaction(cfg) as led:
            p = led.posts["p1"]
            assert p.state is PostState.queued
            assert p.daemon_transient_retry == i + 1
            assert p.error_reason is None
            assert "transient_daemon_retry" not in (p.error_reason or "")
            # Re-fail with typed kind + prose only (no machine counter in error_reason).
            led.posts["p1"] = p.model_copy(
                update={"state": PostState.failed, "error_kind": ErrorKind.transient,
                        "error_reason": "publish failed: NameResolutionError zernio.com"})
    assert _requeue_transient_failed_for_daemon(cfg) == 0
    final = Ledger.load(cfg).posts["p1"]
    assert final.state is PostState.failed
    assert final.daemon_transient_retry == max_d
    assert "transient_daemon_retry" not in (final.error_reason or "")
    # MOL-812: classify_failure buckets from error_kind — re-queued-then-failed stays "transient".
    assert classify_failure(final) == "transient"
    assert "transient_daemon_retry" not in operator_error(final.error_reason)
    assert "transient_daemon_retry" not in operator_error(final.error_reason, kind=classify_failure(final))


def test_recover_posts_retries_transient_failed(tmp_path):
    from fanops.studio.actions import recover_posts
    from fanops.models import Moment, MomentState, Source
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    # The `c1`/`mom_1` rows this seed always NAMED but never created: T3.4's re-arm guard reads lineage via
    # `Ledger.is_suppressed`, which fails CLOSED — a post whose parent clip row is missing is suppressed.
    led.add_source(Source(id="src_1", source_path="/v.mp4", duration=10.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="c1", parent_id="mom_1", path="/c1.mp4", state=ClipState.captioned))
    led.add_post(_fail_post("dns", "publish failed: zernio.com Read timed out (read timeout=30)",
                            kind=ErrorKind.transient))
    led.save()
    res = recover_posts(cfg, ["dns"], action="retry", reason="studio_retry_transient")
    assert res.ok and res.detail["retried"] == 1
    assert Ledger.load(cfg).posts["dns"].state is PostState.queued


def _rate_fail(cfg, pid, *, account_id="ig1", sub=None, retry=0):
    from fanops.models import Moment, MomentState, Source
    f = cfg.clips / f"{pid}.mp4"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        if "src_1" not in led.sources:
            led.add_source(Source(id="src_1", source_path="/v.mp4", duration=10.0))
        if "mom_1" not in led.moments:
            led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                                  reason="r", state=MomentState.clipped))
        if pid not in led.clips:
            led.add_clip(Clip(id=pid, parent_id="mom_1", path=str(f), state=ClipState.captioned))
        led.add_post(Post(id=pid, parent_id=pid, account="a", account_id=account_id,
                          platform=Platform.instagram, caption="c", state=PostState.failed,
                          error_kind=ErrorKind.rate_limit, error_reason="postiz 429 (body withheld)",
                          submission_id=sub, daemon_transient_retry=retry,
                          scheduled_time="2026-08-31T07:00:00Z"))


def test_requeue_rate_limit_one_per_account_id(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("FANOPS_POSTIZ_PUBLISH_PER_MIN", "4")
    cfg = Config(root=tmp_path)
    _rate_fail(cfg, "r1", account_id="ig1")
    _rate_fail(cfg, "r2", account_id="ig1")
    assert _requeue_rate_limited_for_daemon(cfg) == 1
    led = Ledger.load(cfg)
    queued = [p.id for p in led.posts.values() if p.state is PostState.queued]
    failed = [p.id for p in led.posts.values() if p.state is PostState.failed]
    assert len(queued) == 1 and len(failed) == 1
    assert led.posts[queued[0]].error_kind is None
    assert led.posts[failed[0]].error_kind is ErrorKind.rate_limit


def test_requeue_rate_limit_skips_real_submission_id(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    _rate_fail(cfg, "real", account_id="ig1", sub="cmtgxb3ma000ep87wxbmawjms")
    assert _requeue_rate_limited_for_daemon(cfg) == 0
    assert Ledger.load(cfg).posts["real"].state is PostState.failed


_SID = "cmtgxb3ma000ep87wxbmawjms"
_DEAD = {"state": "ERROR", "releaseURL": None, "releaseId": None}


def _dead_fail(cfg, pid, *, account_id="ig1", sid=_SID, url="https://cdn.example/v.mp4"):
    from fanops.models import Moment, MomentState, Source
    f = cfg.clips / f"{pid}.mp4"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        if "src_1" not in led.sources:
            led.add_source(Source(id="src_1", source_path="/v.mp4", duration=10.0))
        if "mom_1" not in led.moments:
            led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                                  reason="r", state=MomentState.clipped))
        if pid not in led.clips:
            led.add_clip(Clip(id=pid, parent_id="mom_1", path=str(f), state=ClipState.captioned))
        led.add_post(Post(id=pid, parent_id=pid, account="a", account_id=account_id,
                          platform=Platform.instagram, caption="c", state=PostState.failed,
                          error_kind=ErrorKind.unknown, error_reason="reconciled: poster reports failed (no detail)",
                          submission_id=sid, media_urls=[url],
                          scheduled_time="2026-08-31T07:00:00Z"))


def test_requeue_proven_dead_rearms_when_media_fetchable(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    _dead_fail(cfg, "d1")
    assert _requeue_proven_dead_for_daemon(cfg, postiz_rows={_SID: _DEAD}) == 1
    p = Ledger.load(cfg).posts["d1"]
    assert p.state is PostState.queued
    assert p.submission_id is None
    assert p.error_kind is None


def test_requeue_proven_dead_skips_when_release_url_present(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    _dead_fail(cfg, "d1")
    rows = {_SID: {"state": "ERROR", "releaseURL": "https://youtube.com/watch?v=x", "releaseId": None}}
    assert _requeue_proven_dead_for_daemon(cfg, postiz_rows=rows) == 0
    assert Ledger.load(cfg).posts["d1"].state is PostState.failed


def test_requeue_proven_dead_skips_when_release_id_present(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    _dead_fail(cfg, "d1")
    rows = {_SID: {"state": "ERROR", "releaseURL": None, "releaseId": "17841456789012345"}}
    assert _requeue_proven_dead_for_daemon(cfg, postiz_rows=rows) == 0
    assert Ledger.load(cfg).posts["d1"].state is PostState.failed


def test_requeue_proven_dead_one_per_account_id(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    _dead_fail(cfg, "d1", sid="sid_a")
    _dead_fail(cfg, "d2", sid="sid_b")
    rows = {"sid_a": dict(_DEAD), "sid_b": dict(_DEAD)}
    assert _requeue_proven_dead_for_daemon(cfg, postiz_rows=rows) == 1
    led = Ledger.load(cfg)
    queued = [p.id for p in led.posts.values() if p.state is PostState.queued]
    failed = [p.id for p in led.posts.values() if p.state is PostState.failed]
    assert len(queued) == 1 and len(failed) == 1


def test_requeue_proven_dead_rewrites_tsnet_then_rearms(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setattr(
        "fanops.post.run.get_media_uploader",
        lambda cfg, backend: (lambda cfg, path, **kw: "https://uploads.example/rewritten.mp4"))
    cfg = Config(root=tmp_path)
    ts = "id|https://molhams-macbook-pro-2.tail72be94.ts.net/uploads/v.mp4"
    _dead_fail(cfg, "d1", url=ts)
    assert _requeue_proven_dead_for_daemon(cfg, postiz_rows={_SID: _DEAD}) == 1
    p = Ledger.load(cfg).posts["d1"]
    assert p.state is PostState.queued
    assert p.media_urls == ["https://uploads.example/rewritten.mp4"]


def test_requeue_proven_dead_skips_unfetchable_without_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path)
    ts = "id|https://molhams-macbook-pro-2.tail72be94.ts.net/uploads/v.mp4"
    _dead_fail(cfg, "d1", url=ts)
    with Ledger.transaction(cfg) as led:
        led.clips["d1"].path = str(cfg.clips / "missing.mp4")
    assert _requeue_proven_dead_for_daemon(cfg, postiz_rows={_SID: _DEAD}) == 0
    assert Ledger.load(cfg).posts["d1"].state is PostState.failed


# submission_id idempotency is owned by
# test_publish_transient_retry.py::test_idempotency_skips_resubmit_when_submission_id_exists
# (an identical copy lived here; that one seeds created_at, so it is the stronger seed).
