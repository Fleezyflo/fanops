# tests/test_meta_graph.py
# The read-only Meta Graph HASHTAG client. Pure-fixture (mocked `get`), no real network. Covers:
# ig_hashtag_search -> node id, top_media -> (verbatim like_count, co-occurring tags), typed Meta
# refusals (GraphRefused / GraphUnreachable — never a bare None for an error), throttle backoff ->
# GraphThrottled (Meta's own refusal is the ONLY governor — the local 30/7-day budget fiction is
# deleted), the token NEVER appearing in any logged / exception string (METRICS_CLIENT_AUTH_DISCIPLINE),
# and the IPv4-only default transport.
import pytest
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


# ── resolve_hashtag: the search endpoint funds NOVEL tags only ──────────────────────────────────

def test_resolve_hashtag_parses_first_data_id(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"ig_hashtag_search": _Resp(200, {"data": [{"id": "177"}]})})
    assert meta_graph.resolve_hashtag(cfg, "#hiphop", get=get) == "177"
    assert get.calls[0][1]["q"] == "hiphop"                        # `q` carries no leading '#'

def test_resolve_hashtag_none_means_no_such_hashtag(tmp_path, monkeypatch):
    """None is reserved for Meta's empty match (HTTP 200, empty data). Nothing else may collapse into it."""
    cfg = _cfg(tmp_path, monkeypatch)
    assert meta_graph.resolve_hashtag(cfg, "#x", get=_router({"ig_hashtag_search": _Resp(200, {"data": []})})) is None

def test_resolve_hashtag_raises_graph_refused_on_meta_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"ig_hashtag_search": _Resp(400, {"error": {
        "message": "This API call could not be completed due to resource limits",
        "type": "OAuthException", "code": 18, "error_subcode": 2207034}})})
    with pytest.raises(meta_graph.GraphRefused) as ei:
        meta_graph.resolve_hashtag(cfg, "#a", get=get)
    exc = ei.value
    assert exc.code == 18 and exc.subcode == 2207034 and exc.type == "OAuthException"
    assert "resource limits" in exc.message
    assert _TOKEN not in str(exc)

def test_resolve_hashtag_no_creds_never_touches_the_network(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, token=None)
    get = _router({})
    assert meta_graph.resolve_hashtag(cfg, "#a", get=get) is None
    assert get.calls == []                                         # short-circuits BEFORE any request

def test_graph_get_raises_unreachable_on_transport_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    import requests
    def boom(url, params=None, timeout=None): raise requests.exceptions.ConnectionError("down")
    with pytest.raises(meta_graph.GraphUnreachable):
        meta_graph.resolve_hashtag(cfg, "#x", get=boom)


# ── measure_and_harvest: ONE top_media fetch serves the metric AND the discovery harvest ────────

def test_measure_and_harvest_reads_the_verbatim_like_count_and_the_cotags(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"top_media": _Resp(200, {"data": [
        {"caption": "bars #alpha #Beta", "like_count": 100, "comments_count": 5},
        {"caption": "#alpha", "like_count": 50, "comments_count": 1}]})})
    metric, cotags = meta_graph.measure_and_harvest(cfg, "id-x", get=get)
    assert metric == 100.0                                         # first play_count/like_count, never a sum
    assert cotags == {"#alpha": 2, "#beta": 1}                     # tallied + normalized

def test_measure_skips_a_media_with_likes_hidden(tmp_path, monkeypatch):
    # Meta hides like_count on some posts (probed live): a leading like-less item is SKIPPED, not zero.
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"top_media": _Resp(200, {"data": [{"caption": "", "comments_count": 8},
                                                     {"caption": "", "like_count": 777}]})})
    assert meta_graph.measure_and_harvest(cfg, "id-x", get=get)[0] == 777.0

def test_measure_and_harvest_unmeasured_on_empty_media(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"top_media": _Resp(200, {"data": []})})
    assert meta_graph.measure_and_harvest(cfg, "id-x", get=get) == (None, {})

def test_measure_and_harvest_raises_on_meta_refusal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    get = _router({"top_media": _Resp(400, {"error": {"code": 100, "message": "unsupported get request"}})})
    with pytest.raises(meta_graph.GraphRefused) as ei:
        meta_graph.measure_and_harvest(cfg, "id-x", get=get)
    assert ei.value.code == 100


# ── Meta's refusals are the only governor ───────────────────────────────────────────────────────

def test_throttle_code_backs_off_then_raises_graph_throttled(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    slept: list = []
    monkeypatch.setattr(meta_graph, "_sleep", lambda s: slept.append(s))
    get = _router({"ig_hashtag_search": _Resp(400, {"error": {"code": 4, "message": "rate limit"}})})
    with pytest.raises(meta_graph.GraphThrottled):
        meta_graph.resolve_hashtag(cfg, "#a", get=get)
    assert len(slept) == meta_graph._MAX_RL_RETRIES                # backed off before giving up
    assert len(get.calls) == meta_graph._MAX_RL_RETRIES + 1

def test_non_throttle_meta_error_is_graph_refused_not_none(tmp_path, monkeypatch):
    # code 18 is Meta's own error object — raise it; never collapse to None (that invented "no such tag").
    cfg = _cfg(tmp_path, monkeypatch)
    slept: list = []
    monkeypatch.setattr(meta_graph, "_sleep", lambda s: slept.append(s))
    get = _router({"ig_hashtag_search": _Resp(400, {"error": {"code": 18, "error_subcode": 2207034,
                                                               "message": "resource limits"}})})
    with pytest.raises(meta_graph.GraphRefused) as ei:
        meta_graph.resolve_hashtag(cfg, "#a", get=get)
    assert ei.value.code == 18 and ei.value.subcode == 2207034
    assert slept == [] and len(get.calls) == 1                     # no retry ladder, no wait

def test_token_never_logged(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    try:
        meta_graph.resolve_hashtag(cfg, "#a", get=_router({}))
    except meta_graph.GraphRefused as e:
        assert _TOKEN not in str(e)
    try:
        meta_graph.measure_and_harvest(cfg, "id-a", get=_router({}))
    except meta_graph.GraphRefused as e:
        assert _TOKEN not in str(e)
    log_text = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert _TOKEN not in log_text                                  # the token must never reach run.log


# ── transport ───────────────────────────────────────────────────────────────────────────────────

def test_default_get_is_ipv4_session(tmp_path, monkeypatch):
    # Default Graph transport (get=None) must ride the IPv4-forcing Session — not bare requests.get —
    # so macOS does not pay the AAAA blackhole tax. Injectable get= still bypasses (existing tests).
    cfg = _cfg(tmp_path, monkeypatch)
    seen = []
    def spy(url, **kw):
        seen.append(url); return _Resp(200, {"data": [{"id": "1"}]})
    monkeypatch.setattr(meta_graph, "_default_get", spy)
    assert meta_graph.resolve_hashtag(cfg, "#x") == "1"           # get=None -> _default_get
    assert seen and "ig_hashtag_search" in seen[0]

def test_ipv4_adapter_forces_af_inet(monkeypatch):
    # The adapter pins urllib3's allowed_gai_family to AF_INET for the duration of send() only.
    import socket
    import urllib3.util.connection as uc
    from unittest.mock import MagicMock
    families = []
    adapter = meta_graph._IPv4HTTPAdapter()
    def fake_send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        families.append(uc.allowed_gai_family()); return MagicMock(status_code=200)
    monkeypatch.setattr(meta_graph.HTTPAdapter, "send", fake_send)
    before = uc.allowed_gai_family()
    adapter.send(MagicMock())
    assert families == [socket.AF_INET]                           # during send: IPv4-only
    assert uc.allowed_gai_family() == before                      # restored after send

def test_ipv4_session_mounts_ipv4_adapter():
    s = meta_graph._ipv4_session()
    assert isinstance(s.get_adapter("https://graph.facebook.com/"), meta_graph._IPv4HTTPAdapter)
