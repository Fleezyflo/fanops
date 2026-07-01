# tests/test_graph_insights.py
# Leg 2 Task 2 (Read) + Task 3 (Land): Meta Graph media insights as the SOLE IG performance source.
# Task 2 here: meta_graph.media_insights branches on media_product_type (reels vs feed video), normalizes
# the returned metric names to lift keys, and DISCRIMINATES a permission/scope refusal (loud typed
# MetaInsightsScopeError) from a transient transport failure (None). Pure-fixture (injected `get=`).
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

def test_media_insights_reels_normalizes_full_set(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    body = _insights_body([("reach", 1000), ("plays", 1200), ("saved", 40),
                           ("shares", 12), ("likes", 300), ("comments", 25),
                           ("ig_reels_avg_watch_time", 8000)])
    got = _get(_Resp(200, body))
    out = meta_graph.media_insights(cfg, "M1", "REELS", get=got)
    assert out["reach"] == 1000 and out["saves"] == 40 and out["shares"] == 12
    assert out["likes"] == 300 and out["comments"] == 25
    assert out["views"] == 1200                                  # plays -> views (Meta rename tolerated)
    assert out["avg_watch_ms"] == 8000                           # raw avg-watch ms, retention derived downstream


def test_media_insights_reels_requests_reels_metric_names(tmp_path, monkeypatch):
    # LOCKED #3: a REELS media must request the reels metric list (must include avg-watch), never a blind
    # list that 400s on a non-applicable metric.
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 1)])))
    meta_graph.media_insights(cfg, "M1", "REELS", get=got)
    _url, params = got.calls[0]
    assert "ig_reels_avg_watch_time" in params["metric"]


def test_media_insights_feed_omits_reels_only_metric(tmp_path, monkeypatch):
    # A non-reel (FEED/VIDEO) media must NOT request ig_reels_avg_watch_time (it 400s for feed) -> no
    # avg_watch_ms in the result; reach/saves/etc still land.
    cfg = _cfg(tmp_path, monkeypatch)
    got = _get(_Resp(200, _insights_body([("reach", 500), ("saved", 9)])))
    out = meta_graph.media_insights(cfg, "M2", "FEED", get=got)
    _url, params = got.calls[0]
    assert "ig_reels_avg_watch_time" not in params["metric"]
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
