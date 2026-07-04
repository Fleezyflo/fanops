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
                                  _map_zernio_analytics, _zernio_analytics_payload, _ZERNIO_STATE_MAP)
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

def test_zernio_analytics_payload_flat_platform_row():
    # Live TikTok (2026-07): lift keys sit flat on platformAnalytics[] — no analytics{} wrapper.
    body = {"platformAnalytics": [{"platform": "tiktok", "likes": 100, "views": 9000, "shares": 4,
                                   "comments": 7, "status": "published"}]}
    assert _map_zernio_analytics(_zernio_analytics_payload(body)) == {"likes": 100.0, "views": 9000.0,
                                                                        "shares": 4.0, "comments": 7.0}

def test_zernio_analytics_payload_platform_metrics_key():
    body = {"platformAnalytics": [{"platform": "tiktok", "metrics": {"likes": 5, "views": 100}}]}
    assert _map_zernio_analytics(_zernio_analytics_payload(body)) == {"likes": 5.0, "views": 100.0}

def test_zernio_analytics_payload_prefers_platform_over_top_level_zeros():
    # Top-level analytics{} can be platform-agnostic zeros while the TikTok row carries the real signal.
    body = {"analytics": {"impressions": 0, "likes": 0, "views": 0},
            "platformAnalytics": [{"platform": "tiktok", "diggCount": 100, "playCount": 9000, "shareCount": 4}]}
    assert _map_zernio_analytics(_zernio_analytics_payload(body)) == {"likes": 100.0, "views": 9000.0, "shares": 4.0}

def test_metrics_client_flat_platform_analytics_row(tmp_path, monkeypatch, mocker):
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    body = {"analytics": {"likes": 0, "views": 0},
            "platformAnalytics": [{"platform": "tiktok", "likes": 42, "views": 8000, "shares": 3}]}
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, body))
    rows = ZernioMetricsClient(cfg, submission_ids=["zid"]).list_posts("30d")
    assert rows == [{"postSubmissionId": "zid", "metrics": {"likes": 42.0, "views": 8000.0, "shares": 3.0},
                     "_raw_labels": ["platform", "likes", "views", "shares"]}]


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
    # Leg 2: IG is measured via Meta Graph (SOLE IG source), so the IG post carries the resolved media_id
    # + cut_seconds the GraphInsightsClient reads; TikTok stays on Zernio. One pass, two measurement sources.
    ig = _published("ig", "psid", account="@ig", platform=Platform.instagram)
    ig = ig.model_copy(update={"media_id": "M_ig", "cut_seconds": 20.0,
                               "public_url": "https://www.instagram.com/reel/AAA/"})
    led.add_post(ig)
    led.add_post(_published("tt", "zsid", account="@tt", platform=Platform.tiktok))
    return led

def test_default_list_posts_mixed_routes_each_post_to_its_backend(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = _mixed_ledger(cfg)
    def by_url(url, **kw):
        if "zernio" in url: return _R(200, {"saves": 40})                  # the TikTok post -> Zernio analytics
        return _R(200, [])                                                 # NOTHING else hits requests.get (IG uses Graph)
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    mocker.patch("fanops.meta_graph.media_insights",
                 return_value={"reach": 500, "saves": 9})                  # the IG post -> Meta Graph (sole IG source)
    rows = _default_list_posts(cfg, posts=list(led.posts.values()))("30d")
    by_sub = {r["postSubmissionId"]: r["metrics"] for r in rows}
    assert by_sub["zsid"] == {"saves": 40.0}                               # zernio post measured via Zernio
    assert by_sub["psid"] == {"reach": 500, "saves": 9}                    # IG post measured via Meta Graph

def test_pull_metrics_mixed_backends_analyzes_both(tmp_path, monkeypatch, mocker):
    # Headline integration proof (Leg 2): ONE pull_metrics pass measures IG-via-Meta-Graph AND
    # TikTok-via-Zernio — the two measurement sources concat into one analyzed pass.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://p.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.setenv("META_GRAPH_TOKEN", "mtok"); monkeypatch.setenv("META_IG_USER_ID", "ig-1")
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); led = _mixed_ledger(cfg)
    def by_url(url, **kw):
        if "zernio" in url: return _R(200, {"saves": 50})
        return _R(200, [])                                                 # IG doesn't hit requests.get (Graph mocked)
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    # media_insights is patched (not list_user_media) — the IG post already carries media_id via _mixed_ledger,
    # so resolve_media_ids is a no-op and the Graph reader lands the row directly.
    mocker.patch("fanops.meta_graph.media_insights", return_value={"saves": 20})
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
                      caption="x", state=PostState.needs_reconcile, submission_id="zsid", public_url="dryrun://tt"))
    led.add_post(Post(id="ig", parent_id="c", account="@ig", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.needs_reconcile, submission_id="psid",
                      scheduled_time="2099-01-01T00:00:00Z", public_url="dryrun://ig"))
    # The TikTok url must oEmbed-verify to the ZERNIO-REPORTED tiktok username to rest. The real Zernio status
    # body carries that username on post.platforms[].accountId (keyed by _id == post.account_id "z1"); get_status
    # surfaces it, and the live oEmbed author ("tt") must equal it. This exercises the real username extraction
    # end-to-end (no injected get_status).
    tt_url = "https://www.tiktok.com/@tt/video/7"
    def by_url(url, **kw):
        if "oembed" in url: return _R(200, {"author_unique_id": "tt", "author_url": "https://www.tiktok.com/@tt"})
        if "zernio" in url: return _R(200, {"post": {"platforms": [{"platform": "tiktok", "status": "published",
                                                                    "platformPostUrl": tt_url,
                                                                    "accountId": {"_id": "z1", "username": "tt"}}]}})
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


# ================================================================ T8 — TikTok permalink live-verify ====
# A captured TikTok URL must be PROVEN a real live post for that handle (symmetric with IG's media_id) — else
# Zernio handing back a dead/wrong URL passes on paper. verify_tiktok_permalink confirms via TikTok oEmbed
# (author_url/author_unique_id/author == handle); the HTTP getter is injected so tests never touch the network.
from fanops.post.metrics import (verify_tiktok_permalink, zernio_permalink_from_analytics,
                                  zernio_reported_tiktok_username)


class _OE:
    # a tiny fake requests.Response for the oEmbed / analytics getter (mirrors _R but named for T8 clarity).
    def __init__(self, code, body): self.status_code = code; self._b = body; self.text = str(body)
    def json(self):
        if isinstance(self._b, Exception): raise self._b
        return self._b


def test_verify_tiktok_permalink_author_matches_accepted(tmp_path):
    # oEmbed author_unique_id == the post's handle (bare, no @) -> the URL is a real live post for that handle.
    cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@mark/video/7300000000000000000"
    calls = {}
    def get(u, **kw):
        calls["url"] = u; calls["params"] = kw.get("params")
        return _OE(200, {"author_unique_id": "mark", "author_name": "Mark",
                         "author_url": "https://www.tiktok.com/@mark", "title": "clip"})
    assert verify_tiktok_permalink(cfg, url, "@mark", get=get) is True
    assert "tiktok.com/oembed" in calls["url"]                     # hits the oEmbed endpoint
    assert (calls["params"] or {}).get("url") == url               # with the candidate url as the query


def test_verify_tiktok_permalink_author_mismatch_rejected(tmp_path):
    # oEmbed author != handle -> the URL belongs to a DIFFERENT account (Zernio handed back a wrong url).
    # REJECTED -> the post must NOT rest (T4 gate keeps it parked).
    cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@someoneelse/video/7"
    def get(u, **kw):
        return _OE(200, {"author_unique_id": "someoneelse",
                         "author_url": "https://www.tiktok.com/@someoneelse"})
    assert verify_tiktok_permalink(cfg, url, "@mark", get=get) is False


def test_verify_tiktok_permalink_normalizes_at_and_case(tmp_path):
    # Handle is stored WITH @ (accounts.json) and can differ in case; oEmbed returns the bare lowercase
    # username. The compare normalizes both sides (strip @, lowercase) so @Mark == author_unique_id "mark".
    cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@Mark/video/7"
    def get(u, **kw): return _OE(200, {"author_url": "https://www.tiktok.com/@mark"})   # only author_url present
    assert verify_tiktok_permalink(cfg, url, "@Mark", get=get) is True


def test_verify_tiktok_permalink_network_or_404_fails_closed(tmp_path):
    # oEmbed 404 (a dead/removed video) or a transport error is NOT proof the post is live -> fail CLOSED
    # (False), never accept an unverifiable URL.
    cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@mark/video/7"
    assert verify_tiktok_permalink(cfg, url, "@mark", get=lambda u, **kw: _OE(404, "gone")) is False
    import requests as _rq
    def boom(u, **kw): raise _rq.exceptions.ConnectionError("no route")
    assert verify_tiktok_permalink(cfg, url, "@mark", get=boom) is False


def test_verify_tiktok_permalink_bad_url_fails_closed(tmp_path):
    # A non-https / empty candidate never even reaches oEmbed -> False (safe_public_url rejects it first).
    cfg = Config(root=tmp_path)
    called = []
    def get(u, **kw): called.append(u); return _OE(200, {"author_unique_id": "mark"})
    assert verify_tiktok_permalink(cfg, "not-a-url", "@mark", get=get) is False
    assert verify_tiktok_permalink(cfg, None, "@mark", get=get) is False
    assert called == []                                            # never hit the network for a malformed url


# ================================================================ zernio_reported_tiktok_username ====
# The Zernio status body (GET /posts/{id}) is authoritative for WHICH TikTok username a post went to:
# post.platforms[].accountId.username, keyed by accountId._id == the account's integration id (accounts.json
# integrations.tiktok == post.account_id). verify_tiktok_permalink must compare the LIVE oEmbed author to
# THIS Zernio-reported username, NOT our internal handle (@hrmny-blog posts to tiktok.com/@wahed_bared).
_LIVE_ZERNIO_TIKTOK_ROW = {
    "platform": "tiktok",
    "accountId": {"_id": "6a37ea985f7d1751ab2e7e92", "username": "wahed_bared", "displayName": "wahed_bared"},
    "platformSpecificData": {"__usernameSnapshot": "wahed_bared", "tiktokUsername": "wahed_bared"},
    "platformPostId": "7658622855357173009",
    "platformPostUrl": "https://www.tiktok.com/@wahed_bared/video/7658622855357173009",
    "status": "published"}


def test_zernio_reported_username_matches_integration_id():
    # The real live shape: the tiktok row whose accountId._id == the post's integration id -> its username.
    body = {"post": {"platforms": [_LIVE_ZERNIO_TIKTOK_ROW]}}
    assert zernio_reported_tiktok_username(body, "6a37ea985f7d1751ab2e7e92") == "wahed_bared"


def test_zernio_reported_username_picks_the_matching_row_among_many():
    # Two tiktok rows (two accounts on one post): the ONE whose _id matches wins — not the first row.
    other = {"platform": "tiktok", "accountId": {"_id": "OTHER_ID", "username": "someone_else"},
             "platformPostUrl": "https://www.tiktok.com/@someone_else/video/1", "status": "published"}
    body = {"post": {"platforms": [other, _LIVE_ZERNIO_TIKTOK_ROW]}}
    assert zernio_reported_tiktok_username(body, "6a37ea985f7d1751ab2e7e92") == "wahed_bared"
    assert zernio_reported_tiktok_username(body, "OTHER_ID") == "someone_else"


def test_zernio_reported_username_sole_row_fallback_when_no_id():
    # When no integration id is supplied (or it matches nothing) but there is exactly ONE tiktok row, use it —
    # this is the bound-method dispatch path (get_status has no post in scope to pass an id).
    body = {"post": {"platforms": [_LIVE_ZERNIO_TIKTOK_ROW]}}
    assert zernio_reported_tiktok_username(body, None) == "wahed_bared"
    assert zernio_reported_tiktok_username(body, "NON_MATCHING_ID") == "wahed_bared"   # sole tiktok row


def test_zernio_reported_username_fallback_keys():
    # accountId.username absent -> __usernameSnapshot -> tiktokUsername -> displayName, in that order.
    snap = {"platform": "tiktok", "accountId": {"_id": "X"},
            "platformSpecificData": {"__usernameSnapshot": "snap_user"}}
    assert zernio_reported_tiktok_username({"platforms": [snap]}, "X") == "snap_user"
    tk = {"platform": "tiktok", "accountId": {"_id": "X"}, "platformSpecificData": {"tiktokUsername": "tk_user"}}
    assert zernio_reported_tiktok_username({"platforms": [tk]}, "X") == "tk_user"
    disp = {"platform": "tiktok", "accountId": {"_id": "X", "displayName": "disp_user"}}
    assert zernio_reported_tiktok_username({"platforms": [disp]}, "X") == "disp_user"


def test_zernio_reported_username_none_when_absent_fails_closed():
    # No tiktok row / no username anywhere -> None (fail-closed: the caller must NOT accept the post).
    assert zernio_reported_tiktok_username({"post": {"platforms": []}}, "X") is None
    assert zernio_reported_tiktok_username({"post": {"platforms": [{"platform": "instagram",
                                                                    "accountId": {"_id": "X", "username": "ig"}}]}}, "X") is None
    assert zernio_reported_tiktok_username({}, "X") is None
    assert zernio_reported_tiktok_username(None, "X") is None
    # a tiktok row that carries NO username field at all (and >1 so the sole-row fallback can't fire) -> None
    r1 = {"platform": "tiktok", "accountId": {"_id": "A"}}
    r2 = {"platform": "tiktok", "accountId": {"_id": "B"}}
    assert zernio_reported_tiktok_username({"platforms": [r1, r2]}, "A") is None


def test_status_published_surfaces_reported_username(tmp_path, monkeypatch, mocker):
    # ZernioStatusClient.get_status carries the Zernio-reported tiktok username OUT to reconcile (so the
    # REST-gate verifies against it WITHOUT a second fetch). Additive: only present when derivable.
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    body = {"post": {"platforms": [_LIVE_ZERNIO_TIKTOK_ROW]}}
    mocker.patch("fanops.post.metrics.requests.get", return_value=_R(200, body))
    out = ZernioStatusClient(cfg).get_status("zid")
    assert out["status"] == "published"
    assert out["publicUrl"] == "https://www.tiktok.com/@wahed_bared/video/7658622855357173009"
    assert out["tiktokUsername"] == "wahed_bared"                  # the sole-tiktok-row username rides out


# ================================================================ verify against REPORTED username ====
def test_verify_tiktok_permalink_matches_zernio_reported_username(tmp_path):
    # THE REAL DEFECT CASE: the live url oEmbed-authors to "wahed_bared"; the expected username (Zernio's
    # reported accountId.username) is "wahed_bared" -> CONFIRMED. The internal handle "hrmny-blog" is NEVER
    # what we compare against (that was the bug: it never equals the real tiktok username).
    cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@wahed_bared/video/7658622855357173009"
    def get(u, **kw): return _OE(200, {"author_unique_id": "wahed_bared",
                                       "author_url": "https://www.tiktok.com/@wahed_bared"})
    assert verify_tiktok_permalink(cfg, url, "wahed_bared", get=get) is True
    # and proof it is NOT the internal handle being compared: hrmny-blog would fail against this same body
    assert verify_tiktok_permalink(cfg, url, "hrmny-blog", get=get) is False


def test_verify_tiktok_permalink_missing_expected_username_fails_closed(tmp_path):
    # No expected username (Zernio reported none) -> fail closed regardless of what oEmbed says.
    cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@wahed_bared/video/7"
    def get(u, **kw): return _OE(200, {"author_unique_id": "wahed_bared"})
    assert verify_tiktok_permalink(cfg, url, None, get=get) is False
    assert verify_tiktok_permalink(cfg, url, "", get=get) is False


# ================================================================ the FULL real case, end-to-end ====
def test_reconcile_tiktok_rests_when_oembed_author_equals_zernio_reported_username(tmp_path, monkeypatch, mocker):
    # THE EXACT BROKEN LIVE CASE, end-to-end: hrmny-blog (internal handle) posted to tiktok.com/@wahed_bared.
    # Zernio's status body reports accountId.username "wahed_bared" for the post's integration id; the live url
    # oEmbed-authors to "wahed_bared". They AGREE -> the post RESTS published. Before the fix this parked
    # because the oEmbed author ("wahed_bared") was compared to the internal handle ("hrmny-blog").
    _zenv(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    integ = "6a37ea985f7d1751ab2e7e92"
    led.add_post(Post(id="tt", parent_id="c", account="@hrmny-blog", account_id=integ, platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id="zreal_1"))
    url = "https://www.tiktok.com/@wahed_bared/video/7658622855357173009"
    status_body = {"post": {"platforms": [_LIVE_ZERNIO_TIKTOK_ROW]}}
    def by_url(u, **kw):
        if "oembed" in u:                                          # the REST-gate live verify
            return _OE(200, {"author_unique_id": "wahed_bared", "author_url": "https://www.tiktok.com/@wahed_bared"})
        return _R(200, status_body)                               # the GET /posts/{id} status body (carries the username)
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    led = reconcile_posts(led, cfg)                               # real per-post dispatch (no injected get_status)
    p = led.posts["tt"]
    assert p.state is PostState.published and p.public_url == url  # oEmbed author == Zernio-reported username -> rests


def test_reconcile_tiktok_parks_when_oembed_author_mismatches_zernio_reported(tmp_path, monkeypatch, mocker):
    # A genuine mismatch: oEmbed says the live video's author is "wahed_bared" but Zernio reports the post went
    # to "someone_else" for this integration id -> REJECTED (parked). A real identity mismatch still fails closed.
    _zenv(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    integ = "6a37ea985f7d1751ab2e7e92"
    led.add_post(Post(id="tt", parent_id="c", account="@hrmny-blog", account_id=integ, platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id="zreal_1"))
    mismatched_row = {**_LIVE_ZERNIO_TIKTOK_ROW,
                      "accountId": {"_id": integ, "username": "someone_else"}}
    def by_url(u, **kw):
        if "oembed" in u: return _OE(200, {"author_unique_id": "wahed_bared"})
        return _R(200, {"post": {"platforms": [mismatched_row]}})
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    led = reconcile_posts(led, cfg)
    p = led.posts["tt"]
    assert p.state is PostState.needs_reconcile                   # oEmbed author != Zernio-reported username -> parked
    from fanops.reconcile import _UNVERIFIED_PREFIX
    assert (p.error_reason or "").startswith(_UNVERIFIED_PREFIX)


def test_reconcile_tiktok_parks_when_zernio_reports_no_username(tmp_path, monkeypatch, mocker):
    # Zernio's status body carries NO derivable username for the post's integration id (fail-closed input) ->
    # the REST-gate has nothing authoritative to compare against -> parked, never rested on an unproven shape.
    _zenv(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="tt", parent_id="c", account="@hrmny-blog", account_id="INTEG_X", platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id="zreal_1"))
    url = "https://www.tiktok.com/@wahed_bared/video/7658622855357173009"
    # a tiktok row with a url but NO accountId/username at all, plus a decoy so the sole-row fallback can't fire
    row_no_user = {"platform": "tiktok", "platformPostUrl": url, "status": "published"}
    decoy = {"platform": "tiktok", "platformPostUrl": "https://www.tiktok.com/@x/video/2", "status": "published"}
    def by_url(u, **kw):
        if "oembed" in u: return _OE(200, {"author_unique_id": "wahed_bared"})
        return _R(200, {"post": {"platforms": [row_no_user, decoy]}})
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    led = reconcile_posts(led, cfg)
    p = led.posts["tt"]
    assert p.state is PostState.needs_reconcile                   # no reported username -> fail-closed -> parked
    from fanops.reconcile import _UNVERIFIED_PREFIX
    assert (p.error_reason or "").startswith(_UNVERIFIED_PREFIX)


# ---- the verifier WIRED into the reconcile REST-gate (the T4<->T8 seam closes) --------------------------
def _tt_post(led, pid, sub, account="@mark"):
    led.add_post(Post(id=pid, parent_id="c", account=account, account_id="z1", platform=Platform.tiktok,
                      caption="x", state=PostState.needs_reconcile, submission_id=sub))


def test_reconcile_tiktok_rests_only_when_oembed_author_matches(tmp_path, monkeypatch, mocker):
    # END-TO-END: a TikTok post whose Zernio status is published + a url whose oEmbed author == the ZERNIO-REPORTED
    # tiktok username (surfaced in the status dict as tiktokUsername) RESTS published (the identity is live-verified).
    # The oEmbed getter is patched at the module level so the reconcile REST-gate's live verify runs against the
    # fake, no network. Zernio reports "mark" and the live oEmbed author is "mark" -> they agree.
    _zenv(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _tt_post(led, "tt", "zreal_1", account="@mark")
    url = "https://www.tiktok.com/@mark/video/7"
    def oembed_get(u, **kw): return _OE(200, {"author_unique_id": "mark", "author_url": "https://www.tiktok.com/@mark"})
    mocker.patch("fanops.post.metrics.requests.get", side_effect=oembed_get)   # the oEmbed verify getter
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": url, "tiktokUsername": "mark"})
    p = led.posts["tt"]
    assert p.state is PostState.published and p.public_url == url   # live-verified -> rests


def test_reconcile_tiktok_stays_parked_when_oembed_author_mismatch(tmp_path, monkeypatch, mocker):
    # A url whose oEmbed author != the Zernio-reported username (Zernio reported the post went to "mark" but the
    # live url's author is "intruder" — a wrong/dead url) is REJECTED by the REST-gate -> the post STAYS parked
    # (needs_reconcile), never rests on an unverified url. FAIL CLOSED.
    _zenv(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _tt_post(led, "tt", "zreal_1", account="@mark")
    url = "https://www.tiktok.com/@intruder/video/7"
    def oembed_get(u, **kw): return _OE(200, {"author_unique_id": "intruder"})
    mocker.patch("fanops.post.metrics.requests.get", side_effect=oembed_get)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": url, "tiktokUsername": "mark"})
    p = led.posts["tt"]
    assert p.state is PostState.needs_reconcile                    # author mismatch -> parked, not rested
    from fanops.reconcile import _UNVERIFIED_PREFIX
    assert (p.error_reason or "").startswith(_UNVERIFIED_PREFIX)


# ---- CONDITIONAL capture fallback: Zernio /analytics body carries a permalink the status endpoint lacked --
def test_zernio_permalink_from_analytics_extracts_url(tmp_path, monkeypatch, mocker):
    # If ZernioStatusClient returns no url, the /analytics?postId= body may still carry one (a field the
    # metrics mapper drops). zernio_permalink_from_analytics fetches it and extracts the permalink.
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    url = "https://www.tiktok.com/@mark/video/7656936928327027969"
    def get(u, **kw):
        assert u.endswith("/analytics") and (kw.get("params") or {}).get("postId") == "zid"
        return _OE(200, {"platformAnalytics": [{"platform": "tiktok", "shareUrl": url, "playCount": 9000}]})
    assert zernio_permalink_from_analytics(cfg, "zid", get=get) == url


def test_zernio_permalink_from_analytics_none_when_absent(tmp_path, monkeypatch):
    # No url-shaped field anywhere in the analytics body -> None (the fallback yields nothing; the post stays
    # parked and surfaced, never silently stuck).
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    def get(u, **kw): return _OE(200, {"platformAnalytics": [{"platform": "tiktok", "playCount": 9000}]})
    assert zernio_permalink_from_analytics(cfg, "zid", get=get) is None


def test_zernio_permalink_from_analytics_fails_soft(tmp_path, monkeypatch):
    # A 5xx / transport error on the analytics fetch returns None (soft) — the fallback is best-effort; the
    # post simply stays parked, never crashes the reconcile pass.
    _zenv(monkeypatch); cfg = Config(root=tmp_path)
    import requests as _rq
    def boom(u, **kw): raise _rq.exceptions.ConnectionError("down")
    assert zernio_permalink_from_analytics(cfg, "zid", get=boom) is None
    assert zernio_permalink_from_analytics(cfg, "zid", get=lambda u, **kw: _OE(503, "x")) is None


def test_tiktok_analytics_fallback_unparks_after_oembed_verify(tmp_path, monkeypatch, mocker):
    # CONDITIONAL end-to-end: Zernio status is published but gives NO url; the /analytics body carries BOTH the
    # permalink AND the reported tiktok username (accountId on the platform row); the url is oEmbed-verified
    # against that reported username THEN accepted -> the post UN-PARKS to published. One /analytics fetch yields
    # both. account_id "z1" (from _tt_post) keys the accountId._id match; oEmbed author "mark" == reported "mark".
    _zenv(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _tt_post(led, "tt", "zreal_1", account="@mark")
    url = "https://www.tiktok.com/@mark/video/7656936928327027969"
    def by_url(u, **kw):
        if u.endswith("/analytics"):
            return _OE(200, {"platformAnalytics": [{"platform": "tiktok", "shareUrl": url,
                                                    "accountId": {"_id": "z1", "username": "mark"}}]})   # url + username
        if "oembed" in u:
            return _OE(200, {"author_unique_id": "mark", "author_url": "https://www.tiktok.com/@mark"})
        raise AssertionError(f"unexpected GET {u}")
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    # status endpoint (the get_status seam) reports published-with-no-url — the R1 park would fire, but the
    # analytics fallback back-fills a verified url + username so the post reaches a REAL terminal state.
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": None})
    p = led.posts["tt"]
    assert p.state is PostState.published and p.public_url == url   # analytics-backfilled + oEmbed-verified -> rests


def test_tiktok_analytics_fallback_still_parks_when_unverifiable(tmp_path, monkeypatch, mocker):
    # If neither the status endpoint nor the /analytics body yields a verifiable url, the post STAYS parked
    # (needs_reconcile) AND carries a clear surfaced reason — never silently stuck forever.
    _zenv(monkeypatch); cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _tt_post(led, "tt", "zreal_1", account="@mark")
    def by_url(u, **kw):
        if u.endswith("/analytics"):
            return _OE(200, {"platformAnalytics": [{"platform": "tiktok", "playCount": 9000}]})   # no url
        raise AssertionError(f"unexpected GET {u}")                # oEmbed never reached (no url to verify)
    mocker.patch("fanops.post.metrics.requests.get", side_effect=by_url)
    led = reconcile_posts(led, cfg, get_status=lambda sid: {"status": "published", "publicUrl": None})
    p = led.posts["tt"]
    assert p.state is PostState.needs_reconcile                    # no verifiable url anywhere -> parked
    assert (p.error_reason or "").strip()                          # a surfaced reason, never silent
