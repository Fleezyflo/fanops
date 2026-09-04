# tests/test_graph_insights.py
# Leg 2 Task 2 (Read): meta_graph.media_insights branches on media_product_type (reels vs feed video),
# normalizes metric names to lift keys, and discriminates scope refusal from transient failure.
# Pure-fixture (injected `get=`).
import pytest
from fanops.config import Config
from fanops import meta_graph
from fanops.errors import MetaInsightsScopeError

_TOKEN = "SECRET-meta-token-xyz"


def _cfg(tmp_path, monkeypatch, *, token=_TOKEN, ig="ig-123"):
    monkeypatch.setenv("META_GRAPH_TOKEN", token) if token else monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.setenv("META_IG_USER_ID", ig) if ig else monkeypatch.delenv("META_IG_USER_ID", raising=False)
    return Config(root=tmp_path)


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json body")
        return self._body


def _get(resp_or_fn):
    calls = []
    def get(url, params=None, timeout=None):
        calls.append((url, params))
        return resp_or_fn(params) if callable(resp_or_fn) else resp_or_fn
    get.calls = calls
    return get


def _insights_body(pairs):
    # Graph /{media}/insights shape: {"data": [{"name": ..., "values": [{"value": N}]}, ...]}
    return {"data": [{"name": n, "values": [{"value": v}]} for n, v in pairs]}


# ---- reels branch: full set incl. avg watch time ------------------------------------------------

# ---- the one Meta-derived table: insights_metrics_for(product_type) is the SOLE request source ---------
# _MEDIA_METRICS maps each FanOps-consumed metric to the product types Meta declares valid (transcribed
# from the official ig-media/insights reference). A metric invalid for a type is NOT in the derived set ->
# it is unconstructable in the request. Deprecated names (plays/impressions) are absent by design.

_DEPRECATED = ("plays", "impressions", "clips_replays_count",
               "ig_reels_aggregated_all_plays_count", "video_views")


def test_media_metrics_table_is_v21_valid(tmp_path, monkeypatch):
    # The single sync point with Meta, DEFENDED: no deprecated metric may live in the table (that is what
    # let `plays` rot), and the Graph URL stays pinned to the version the table is valid for. This FAILS at
    # CI the day someone re-adds a deprecated metric — surfacing the class at CI, not in production.
    assert not (set(meta_graph._MEDIA_METRICS) & set(_DEPRECATED)), meta_graph._MEDIA_METRICS
    assert _cfg(tmp_path, monkeypatch).meta_graph_url.endswith("v21.0")


def test_insights_metrics_for_post_includes_avg_watch():
    m = meta_graph.insights_metrics_for("post")                  # service token → REELS metric set
    assert "ig_reels_avg_watch_time" in m                        # REELS-only metric IS in the post/reels set
    for k in ("reach", "views", "likes", "comments", "saved", "shares"):
        assert k in m                                            # the shared metrics land too


def test_insights_metrics_for_rejects_meta_vocabulary():
    # MOL-775: raw Meta AD|FEED|STORY|REELS is not accepted on the authored path (service tokens only).
    # ImportedMedia still resolves Meta keys via _metrics_for_graph_product_type / media_insights fallback.
    for pt in ("REELS", "FEED", "STORY", "AD", "reels", None, "unexpected"):
        assert meta_graph.insights_metrics_for(pt) == [], pt


def test_insights_metrics_for_never_contains_a_deprecated_name():
    # The whole class killer: no derived set for ANY accepted token can contain a deprecated metric,
    # because the table doesn't hold one. This is what made `plays` unrequestable.
    for pt in ("post", "story", None, "unexpected", "REELS"):
        derived = meta_graph.insights_metrics_for(pt)
        assert not (set(derived) & set(_DEPRECATED)), (pt, derived)


def test_metrics_for_graph_product_type_feed_excludes_reels_only():
    m = meta_graph._metrics_for_graph_product_type("FEED")
    assert "ig_reels_avg_watch_time" not in m
    for k in ("reach", "views", "likes", "comments", "saved"):
        assert k in m


def test_insights_metrics_for_service_tokens():
    # Service post-type tokens (Postiz) map to the Meta table keys used in _MEDIA_METRICS.
    post_m = meta_graph.insights_metrics_for("post")
    story_m = meta_graph.insights_metrics_for("story")
    assert "ig_reels_avg_watch_time" in post_m
    assert "ig_reels_avg_watch_time" not in story_m
    assert meta_graph.insights_metrics_for(None) == []
    assert meta_graph.insights_metrics_for("weird") == []


def test_media_insights_reels_normalizes_full_set(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # Meta's REAL v21 reels response uses `views` (not the deprecated `plays`).
    body = _insights_body([("reach", 1000), ("views", 1200), ("saved", 40),
                           ("shares", 12), ("likes", 300), ("comments", 25),
                           ("ig_reels_avg_watch_time", 8000)])
    got = _get(_Resp(200, body))
    out = meta_graph.media_insights(cfg, "M1", "REELS", get=got)
    assert out["reach"] == 1000 and out["saves"] == 40 and out["shares"] == 12
    assert out["likes"] == 300 and out["comments"] == 25
    assert out["views"] == 1200                                  # views is the v21 metric
    assert out["avg_watch_ms"] == 8000                           # raw avg-watch ms, retention derived downstream


def test_media_insights_reels_request_omits_deprecated_plays(tmp_path, monkeypatch):
    # The request is derived from the Meta table: a REELS pull must send `views` and the reels-only
    # avg-watch, and must NEVER send the deprecated `plays` (the whole-request 400 cause).
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 1)])))
    meta_graph.media_insights(cfg, "M1", "REELS", get=got)
    _url, params = got.calls[0]
    metric = params["metric"]
    assert "views" in metric and "ig_reels_avg_watch_time" in metric
    assert "plays" not in metric and "impressions" not in metric


def test_media_insights_feed_omits_reels_only_metric(tmp_path, monkeypatch):
    # A FEED media derives the feed set: NO ig_reels_avg_watch_time (Meta: REELS-only) -> no avg_watch_ms in
    # the result; reach/saves/etc still land. No `plays` either.
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 500), ("saved", 9)])))
    out = meta_graph.media_insights(cfg, "M2", "FEED", get=got)
    _url, params = got.calls[0]
    assert "ig_reels_avg_watch_time" not in params["metric"]
    assert "plays" not in params["metric"]
    assert out["reach"] == 500 and out["saves"] == 9
    assert "avg_watch_ms" not in out


# ---- scope refusal is LOUD (typed) vs transient is None -----------------------------------------

def test_media_insights_permission_error_raises_scope_error(tmp_path, monkeypatch):
    # A Meta permission/OAuth refusal (missing instagram_manage_insights) must raise the typed, LOUD
    # MetaInsightsScopeError — fail CLOSED, never silently None (which reads as 'no data', wrong).
    cfg = _cfg(tmp_path, monkeypatch)
    perm = {"error": {"code": 10, "type": "OAuthException",
                      "message": "(#10) Application does not have permission for this action"}}
    with pytest.raises(MetaInsightsScopeError):
        meta_graph.media_insights(cfg, "M1", "REELS", get=_get(_Resp(400, perm)))


def test_media_insights_scope_error_withholds_token(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    perm = {"error": {"code": 200, "type": "OAuthException", "message": "Permissions error"}}
    try:
        meta_graph.media_insights(cfg, "M1", "REELS", get=_get(_Resp(403, perm)))
        assert False, "expected MetaInsightsScopeError"
    except MetaInsightsScopeError as e:
        assert _TOKEN not in str(e)                              # the access_token never leaks into the message


def test_media_insights_transient_5xx_returns_none(tmp_path, monkeypatch):
    # A 5xx / transport blip is TRANSIENT -> None (re-poll next pass), NOT a scope error (don't cry wolf).
    cfg = _cfg(tmp_path, monkeypatch)
    assert meta_graph.media_insights(cfg, "M1", "REELS", get=_get(_Resp(500, None))) is None


def test_media_insights_network_exception_returns_none(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    import requests
    def boom(url, params=None, timeout=None):
        raise requests.exceptions.ConnectionError("down")
    assert meta_graph.media_insights(cfg, "M1", "REELS", get=boom) is None


def test_media_insights_no_creds_returns_none(tmp_path, monkeypatch):
    # No token/ig id -> can't read -> None (transient-shaped; the daemon simply keeps prior snapshots).
    cfg = _cfg(tmp_path, monkeypatch, token=None)
    assert meta_graph.media_insights(cfg, "M1", "REELS", get=_get(_Resp(200, _insights_body([("reach", 1)])))) is None


# ---- M2 residual: an unresolved product_type must NOT build an empty-metric request --------------

def test_media_insights_none_product_type_builds_no_request(tmp_path, monkeypatch):
    # LIVE RESIDUAL (post_4eb7c0802e79): media_id resolved but post_type=None -> insights_metrics_for(None)
    # is [] -> today media_insights sends an EMPTY `metric=` -> Meta 400 OAuthException -> _is_scope_error
    # (untouched) writes a FALSE scope-block. Honor the docstring ("the client skips an unresolved one"):
    # an empty derived set must be refused PRE-FLIGHT -> ZERO HTTP calls (None, transient-shaped, re-resolve
    # next pass), never a malformed request.
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 1)])))       # would answer 200 IF called
    out = meta_graph.media_insights(cfg, "M1", None, get=got)
    assert out is None                                           # transient-shaped skip, keep prior snapshot
    assert got.calls == []                                       # no request built -> no empty `metric=` sent
