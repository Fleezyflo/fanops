# tests/test_meta_graph.py
# M4 live half: the budget-aware, read-only Meta Graph hashtag-TREND client. Pure-fixture (mocked
# `get`), no real network. Covers: ig_hashtag_search -> id, top_media -> engagement trend score,
# transport fail-SOFT (per-tag None, never raises), the 30/7-day budget being fail-CLOSED + LOUD on
# unknown state, and the token NEVER appearing in any logged output (METRICS_CLIENT_AUTH_DISCIPLINE).
import json
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops import meta_graph

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

def _router(routes):
    """routes: dict mapping a substring-of-the-url -> _Resp (or a callable(params)->_Resp)."""
    calls = []
    def get(url, params=None, timeout=None):
        calls.append((url, params))
        for frag, resp in routes.items():
            if frag in url:
                return resp(params) if callable(resp) else resp
        return _Resp(404, None)
    get.calls = calls
    return get


def test_hashtag_id_parses_first_data_id(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"ig_hashtag_search": _Resp(200, {"data": [{"id": "177"}]})})
    assert meta_graph.hashtag_id(cfg, "#hiphop", get=get) == "177"

def test_hashtag_id_none_on_empty_or_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert meta_graph.hashtag_id(cfg, "#x", get=_router({"ig_hashtag_search": _Resp(200, {"data": []})})) is None
    assert meta_graph.hashtag_id(cfg, "#x", get=_router({"ig_hashtag_search": _Resp(400, None)})) is None

def test_trend_score_sums_top_media_engagement(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"ig_hashtag_search": _Resp(200, {"data": [{"id": "9"}]}),
                   "top_media": _Resp(200, {"data": [{"like_count": 100, "comments_count": 5},
                                                     {"like_count": 50, "comments_count": 1}]})})
    assert meta_graph.trend_score(cfg, "#rap", get=get) == 156.0

def test_trend_score_none_when_hashtag_unresolved(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"ig_hashtag_search": _Resp(200, {"data": []})})
    assert meta_graph.trend_score(cfg, "#x", get=get) is None

def test_graph_get_fail_soft_on_transport_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    import requests
    def boom(url, params=None, timeout=None): raise requests.exceptions.ConnectionError("down")
    assert meta_graph.hashtag_id(cfg, "#x", get=boom) is None      # never raises -> enhancement fails soft


def test_budget_remaining_fresh_is_full(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert meta_graph.budget_remaining(cfg) == meta_graph._BUDGET_LIMIT

def test_budget_counts_unique_tags_in_window_and_expires_old(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    old = (now - timedelta(days=10)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text(json.dumps({"queries": [
        {"tag": "#a", "ts": old}, {"tag": "#b", "ts": recent}, {"tag": "#b", "ts": recent}]}))
    # only #b counts (recent + unique); #a expired
    assert meta_graph.budget_remaining(cfg, now=now) == meta_graph._BUDGET_LIMIT - 1

def test_budget_fail_closed_on_corrupt_file(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text("{ not json")
    assert meta_graph.budget_remaining(cfg) is None                # None == fail-closed (unknown -> refuse)

def test_record_query_appends_and_decrements(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    meta_graph.record_query(cfg, "#new", now=now)
    assert meta_graph.budget_remaining(cfg, now=now) == meta_graph._BUDGET_LIMIT - 1


def test_sample_trends_no_creds_returns_empty(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, token=None)                 # no token -> own-reach only
    get = _router({})
    assert meta_graph.sample_trends(cfg, ["#a"], get=get) == {}
    assert get.calls == []                                        # never hits the network without creds

def test_sample_trends_fail_closed_when_budget_unknown(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text("CORRUPT")                 # budget unknown -> fail-closed
    get = _router({"ig_hashtag_search": _Resp(200, {"data": [{"id": "1"}]})})
    assert meta_graph.sample_trends(cfg, ["#a", "#b"], get=get) == {}
    assert get.calls == []                                        # refuses to query rather than risk Meta's cap

def test_sample_trends_stops_at_budget(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    # pre-spend the budget down to 1 remaining (29 unique recent queries)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text(json.dumps({"queries": [
        {"tag": f"#t{i}", "ts": now.isoformat()} for i in range(meta_graph._BUDGET_LIMIT - 1)]}))
    get = _router({"ig_hashtag_search": _Resp(200, {"data": [{"id": "1"}]}),
                   "top_media": _Resp(200, {"data": [{"like_count": 7}]})})
    out = meta_graph.sample_trends(cfg, ["#x", "#y", "#z"], get=get, now=now)
    assert len(out) == 1                                          # only one slot left -> one sampled

def test_token_never_logged(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.hashtag_budget_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtag_budget_path.write_text("CORRUPT")                 # forces the LOUD fail-closed log line
    meta_graph.sample_trends(cfg, ["#a"], get=_router({}), now=datetime(2026, 6, 19, tzinfo=timezone.utc))
    log_text = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert _TOKEN not in log_text                                 # the token must never reach run.log
