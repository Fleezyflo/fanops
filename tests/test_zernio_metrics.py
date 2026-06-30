"""Slice 5 — Zernio reconcile + metrics. ZernioMetricsClient reads per-post TikTok analytics into the
lift/learning loop (mirrors PostizMetricsClient); ZernioStatusClient resolves a parked post's live state +
TikTok permalink for reconcile (mirrors BlotatoStatusClient — Zernio HAS a real single-post lookup). The
analytics/status response SHAPES are INTEGRATION CHECKPOINTS: the maps accept the documented aliases +
common nestings, locked offline here, verified live by the operator. The headline proof is per-post
DISPATCH: with the operator's real deployment (global=postiz for IG, a per-account tiktok->zernio
override), one metrics pull / one reconcile pass routes EACH submission to its own backend's client."""
import pytest
from datetime import datetime, timezone
from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform
from fanops.timeutil import iso_z
from fanops.accounts import add_account, set_backend
from fanops.post.metrics import (ZernioMetricsClient, ZernioStatusClient,
                                  _map_zernio_analytics, _ZERNIO_STATE_MAP)
from fanops.track import _default_list_posts, pull_metrics
from fanops.reconcile import _default_get_status, reconcile_posts

_PUB = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class _R:
    def __init__(self, code, body): self.status_code = code; self._b = body; self.text = str(body)
    def json(self):
        if isinstance(self._b, Exception): raise self._b
        return self._b


def _zenv(monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("ZERNIO_API_URL", raising=False)
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)


def _published(pid, sub, account="@tt", platform=Platform.tiktok):
    return Post(id=pid, parent_id="c", account=account, account_id="z1", platform=platform,
                caption="x", state=PostState.published, submission_id=sub, published_at=iso_z(_PUB), public_url="dryrun://c")


# ---------------------------------------------------------------- analytics shape mapping ----
def test_map_analytics_flat_dict():
    out = _map_zernio_analytics({"likes": 7, "comments": 2, "shares": 3, "saves": 9, "reach": 1000, "views": 5000})
    assert out == {"likes": 7.0, "comments": 2.0, "shares": 3.0, "saves": 9.0, "reach": 1000.0, "views": 5000.0}

def test_map_analytics_labeled_array():
    arr = [{"label": "Likes", "value": "4"}, {"metric": "Shares", "count": 6}, {"name": "Saves", "total": "8"}]
    assert _map_zernio_analytics(arr) == {"likes": 4.0, "shares": 6.0, "saves": 8.0}

def test_map_analytics_nested_under_metrics_or_insights():
    assert _map_zernio_analytics({"metrics": {"saves": 11}}) == {"saves": 11.0}
    assert _map_zernio_analytics({"insights": {"views": 12}}) == {"views": 12.0}
    assert _map_zernio_analytics({"data": {"likes": 13}}) == {"likes": 13.0}

def test_map_analytics_tiktok_field_aliases():
    # TikTok's own field names: diggCount=likes, playCount=views, collectCount=saves, shareCount, commentCount.
    out = _map_zernio_analytics({"diggCount": 100, "playCount": 9000, "collectCount": 12,
                                 "shareCount": 4, "commentCount": 7})
    assert out == {"likes": 100.0, "views": 9000.0, "saves": 12.0, "shares": 4.0, "comments": 7.0}

def test_map_analytics_drops_unknown_and_uncoercible():
    out = _map_zernio_analytics({"saves": 5, "title": "hello", "weird": "NaN-ish", "nested": {"a": 1}})
    assert out == {"saves": 5.0}                 # unknown labels + non-numeric dropped; lift_score whitelists anyway

def test_map_analytics_non_container_is_empty():
    assert _map_zernio_analytics(None) == {} and _map_zernio_analytics("oops") == {} and _map_zernio_analytics(7) == {}


# ---------------------------------------------------------------- ZernioMetricsClient ----
def test_metrics_client_fetches_per_post_rows(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    spy = mocker.patch("fanops.post.metrics.requests.get",
                       return_value=_R(200, {"likes": 3, "saves": 30}))
    rows = ZernioMetricsClient(cfg, submission_ids=["zid"]).list_posts("30d")
    assert spy.call_args[0][0].endswith("/analytics") and spy.call_args[1]["params"]["postId"] == "zid"                # per-post analytics endpoint
    assert rows == [{"postSubmissionId": "zid", "metrics": {"likes": 3.0, "saves": 30.0}, "_raw_labels": ["likes", "saves"]}]

def test_metrics_client_no_ids_no_network(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    spy = mocker.patch("fanops.post.metrics.requests.get")
    assert ZernioMetricsClient(cfg, submission_ids=None).list_posts("30d") == []
    spy.assert_not_called()                                            # None -> [] (no crash for cmd_track/cutover callers)

def test_metrics_client_401_raises_autherror_halts(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(401, {"error": "sk_leak?"}))
    with pytest.raises(ZernioAuthError) as ei:
        ZernioMetricsClient(cfg, submission_ids=["zid"]).list_posts("30d")
    assert "sk_test" not in str(ei.value) and "sk_leak" not in str(ei.value)   # body + key WITHHELD

def test_metrics_client_per_post_failure_is_skipped_not_emitted_as_empty(tmp_path, monkeypatch, mocker):
    # operability follow-up: a per-post 5xx/transport failure SKIPS that id (its prior metrics survive,
    # re-polled next pass) — it no longer emits a metrics={} row that record_metrics would WHOLESALE-zero
    # the post with. Still isolated: one bad id never aborts the pass or loses the others.
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    def by_id(url, **kw):
        sid = (kw.get("params") or {}).get("postId", "")
        return _R(503, "down") if sid == "bad" else _R(200, {"saves": 2})
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_id)
    rows = ZernioMetricsClient(cfg, submission_ids=["bad", "ok"]).list_posts("30d")
    assert [r["postSubmissionId"] for r in rows] == ["ok"]             # bad SKIPPED, no empty row, no raise
    assert rows[0]["metrics"] == {"saves": 2.0}                        # the healthy post still measured

def test_metrics_client_missing_key_raises_at_construction(tmp_path, monkeypatch):
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False); monkeypatch.setenv("FANOPS_POSTER", "zernio")
    with pytest.raises(ZernioAuthError):
        ZernioMetricsClient(Config(root=tmp_path), submission_ids=["zid"])


# ---------------------------------------------------------------- ZernioStatusClient ----
def test_status_published_returns_permalink(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@mark/video/7300000000000000000"
    spy = mocker.patch("fanops.post.metrics.requests.get",
                       return_value=_R(200, {"status": "published", "permalink": url}))
    assert ZernioStatusClient(cfg).get_status("zid") == {"status": "published", "publicUrl": url}
    assert "posts/zid" in spy.call_args[0][0]

def test_status_failed(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"status": "error"}))
    assert ZernioStatusClient(cfg).get_status("zid") == {"status": "failed"}

def test_status_unknown_state_parks_never_failed(tmp_path, monkeypatch, mocker):
    # processing/queued/anything-unrecognized -> 'scheduled' (parked) — NEVER guessed failed (a failed is
    # re-queueable -> the double-post hazard for a possibly-live post).
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"status": "processing"}))
    assert ZernioStatusClient(cfg).get_status("zid") == {"status": "scheduled"}

def test_status_permalink_from_nested_and_aliases(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    body = {"post": {"state": "posted", "postUrl": "https://www.tiktok.com/@x/video/1"}}
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, body))
    assert ZernioStatusClient(cfg).get_status("zid") == {"status": "published", "publicUrl": "https://www.tiktok.com/@x/video/1"}

def test_status_permalink_from_platforms_array(tmp_path, monkeypatch, mocker):
    # Live Zernio shape (2026-06-30): status + platformPostUrl under post.platforms[0].
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@hrmnyco/video/7656936928327027969"
    body = {"post": {"platforms": [{"status": "published", "platformPostUrl": url}]}}
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, body))
    assert ZernioStatusClient(cfg).get_status("zid") == {"status": "published", "publicUrl": url}

def test_status_401_raises_autherror(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(401, {"k": "sk_secret"}))
    with pytest.raises(ZernioAuthError) as ei:
        ZernioStatusClient(cfg).get_status("zid")
    assert "sk_secret" not in str(ei.value)

def test_state_map_terminal_aliases():
    for s in ("published", "posted", "live", "complete", "completed", "success"):
        assert _ZERNIO_STATE_MAP[s] == "published"
    for s in ("failed", "error", "rejected", "cancelled"):
        assert _ZERNIO_STATE_MAP[s] == "failed"


# ---------------------------------------------------------------- single-backend dispatch ----
def test_default_list_posts_global_zernio_binds_zernio_client(tmp_path, monkeypatch):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    fetch = _default_list_posts(cfg, submission_ids=["zid"])
    assert fetch.__self__.__class__ is ZernioMetricsClient                # global=zernio -> Zernio metrics client

def test_default_get_status_global_zernio_binds_zernio_client(tmp_path, monkeypatch):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    poll = _default_get_status(cfg)
    assert poll.__self__.__class__ is ZernioStatusClient                  # global=zernio -> Zernio status client (bound)




def test_metrics_client_uses_analytics_query_postid(tmp_path, monkeypatch, mocker):
    # Live Zernio contract (docs.zernio.com 2026-06): GET /v1/analytics?postId= — NOT /analytics/posts/{id}.
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    spy = mocker.patch("fanops.post.metrics.requests.get",
                       return_value=_R(200, {"analytics": {"likes": 5, "saves": 2}}))
    rows = ZernioMetricsClient(cfg, submission_ids=["zid123"]).list_posts("30d")
    assert rows[0]["metrics"] == {"likes": 5.0, "saves": 2.0}
    url = spy.call_args[0][0]
    assert url.endswith("/analytics")
    assert spy.call_args[1]["params"]["postId"] == "zid123"
    assert "/analytics/posts/" not in url


def test_metrics_client_platform_analytics_shape(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    body = {"platformAnalytics": [{"platform": "tiktok", "analytics": {"likes": 9, "views": 1000, "saves": 3}}]}
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, body))
    rows = ZernioMetricsClient(cfg, submission_ids=["zid"]).list_posts("30d")
    assert rows[0]["metrics"] == {"likes": 9.0, "views": 1000.0, "saves": 3.0}


def test_metrics_client_202_sync_pending_skipped(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(202, {"message": "sync pending"}))
    assert ZernioMetricsClient(cfg, submission_ids=["zid"]).list_posts("30d") == []

# ---------------------------------------------------------------- per-post (mixed) dispatch ----
def _mixed_ledger(cfg):
    # IG via the global (postiz) + a TikTok account overridden to zernio — the operator's real deployment.
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    led = Ledger.load(cfg)
    led.add_post(_published("ig", "psid", account="@ig", platform=Platform.instagram))
    led.add_post(_published("tt", "zsid", account="@tt", platform=Platform.tiktok))
    return led

def test_default_list_posts_mixed_routes_each_post_to_its_backend(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = _mixed_ledger(cfg)
    def by_url(url, **kw):
        if "zernio" in url: return _R(200, {"saves": 40})                  # the TikTok post -> Zernio analytics
        return _R(200, [{"label": "Likes", "data": [{"total": "9", "date": "d"}]}])   # the IG post -> Postiz analytics
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    rows = _default_list_posts(cfg, posts=list(led.posts.values()))("30d")
    by_sub = {r["postSubmissionId"]: r["metrics"] for r in rows}
    assert by_sub["zsid"] == {"saves": 40.0}                               # zernio post measured via Zernio
    assert by_sub["psid"] == {"likes": 9.0}                                # postiz post measured via Postiz

def test_pull_metrics_mixed_backends_analyzes_both(tmp_path, monkeypatch, mocker):
    # Headline integration proof: ONE pull_metrics pass measures IG-via-Postiz AND TikTok-via-Zernio.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = _mixed_ledger(cfg)
    def by_url(url, **kw):
        if "zernio" in url: return _R(200, {"saves": 50})
        return _R(200, [{"label": "Saves", "data": [{"total": "20", "date": "d"}]}])
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    led = pull_metrics(led, cfg)
    assert led.posts["tt"].state is PostState.analyzed and led.posts["tt"].metrics["saves"] == 50.0
    assert led.posts["ig"].state is PostState.analyzed and led.posts["ig"].metrics["saves"] == 20.0

def test_default_get_status_mixed_routes_each_sid(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active"); set_backend(cfg, "@tt", "tiktok", "zernio")
    led = Ledger.load(cfg)
    led.add_post(Post(id="tt", parent_id="c", account="@tt", account_id="z1", platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id="zsid", public_url=f"dryrun://tt"))
    led.add_post(Post(id="ig", parent_id="c", account="@ig", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="psid",
                      scheduled_time="2099-01-01T00:00:00Z", public_url=f"dryrun://ig"))
    tt_url = "https://www.tiktok.com/@mark/video/7"
    def by_url(url, **kw):
        if "zernio" in url: return _R(200, {"status": "published", "permalink": tt_url})
        return _R(200, {"posts": [{"id": "psid", "state": "PUBLISHED", "releaseURL": "https://www.instagram.com/reel/X/"}]})
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    led = reconcile_posts(led, cfg)                                       # NO injected get_status -> real per-post dispatch
    assert led.posts["tt"].state is PostState.published and led.posts["tt"].public_url == tt_url
    assert led.posts["ig"].state is PostState.published                  # the IG post resolved through Postiz in the SAME pass


def test_default_list_posts_corrupt_accounts_degrades_to_global(tmp_path, monkeypatch, mocker):
    # A corrupt accounts.json must NOT crash the metrics read (publish surfaces that error loudly) — degrade
    # to the global backend for every post, the pre-routing behavior.
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text("{ this is not json")
    led = Ledger.load(cfg); led.add_post(_published("tt", "zsid"))
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, {"saves": 1}))
    rows = _default_list_posts(cfg, posts=list(led.posts.values()))("30d")   # global=zernio -> all routed to zernio
    assert rows == [{"postSubmissionId": "zsid", "metrics": {"saves": 1.0}, "_raw_labels": ["saves"]}]
