# tests/test_meta_account_creds.py
# Per-account Meta credentials (the audit's config/model gap): META_IG_USER_ID + the Graph access token
# were a SINGLE GLOBAL credential, so every Graph read (list_user_media / insights / hashtag reads) saw
# ONE handle regardless of which account a post belonged to. This proves the per-account resolution +
# threading: given a handle, resolve ITS ig_user_id + token; missing per-account -> global fallback
# (existing single-account setups stay byte-identical); the token is WRITE-ONLY (never echoed). Pure
# fixtures, no real network — every Graph call injects a mock `get`.
import json
from fanops.config import Config
from fanops import meta_graph
from fanops.accounts import Account, set_ig_user_id
from fanops.models import Platform


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json body")
        return self._body


def _router(routes):
    """routes: substring-of-url -> _Resp (or callable(params)->_Resp). Records (url, params) per call."""
    calls = []
    def get(url, params=None, timeout=None):
        calls.append((url, params))
        for frag, resp in routes.items():
            if frag in url:
                return resp(params) if callable(resp) else resp
        return _Resp(404, None)
    get.calls = calls
    return get


def _write_accounts(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))


# ── resolve_meta_creds: the single source of truth ────────────────────────────────────────────────────

def test_resolve_per_account_creds_win_over_global(tmp_path, monkeypatch):
    # Global creds are the @markmakmouly-only shape today; @stan has its OWN ig id + token.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    monkeypatch.setenv("META_GRAPH_TOKEN__STAN", "pa-tok")
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-stan-99"}])
    creds = meta_graph.resolve_meta_creds(cfg, handle="@stan")
    assert creds.ig_user_id == "ig-stan-99"
    assert creds.token == "pa-tok"


def test_resolve_falls_back_to_global_when_no_per_account(tmp_path, monkeypatch):
    # @markmakmouly has NO per-account ig id and NO per-handle token -> resolve the global creds verbatim.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@markmakmouly", "account_id": "", "platforms": ["instagram"],
                           "status": "active"}])
    creds = meta_graph.resolve_meta_creds(cfg, handle="@markmakmouly")
    assert creds.ig_user_id == "ig-global-1"
    assert creds.token == "tok-global"


def test_resolve_no_handle_is_global(tmp_path, monkeypatch):
    # A call with NO account in context (hashtag discovery, niche-wide) uses the global creds exactly as today.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    creds = meta_graph.resolve_meta_creds(cfg, handle=None)
    assert (creds.ig_user_id, creds.token) == ("ig-global-1", "tok-global")


def test_resolve_partial_per_account_id_only_falls_back_token_to_global(tmp_path, monkeypatch):
    # A handle with its own ig id but NO per-handle token: the id is per-account, the token falls back to
    # the global (a common shape — one app token, many IG business ids under it).
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-stan-99"}])
    creds = meta_graph.resolve_meta_creds(cfg, handle="@stan")
    assert creds.ig_user_id == "ig-stan-99"
    assert creds.token == "tok-global"


def test_resolve_unknown_handle_is_global(tmp_path, monkeypatch):
    # A handle absent from accounts.json -> global fallback, never raises (a read path must degrade).
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    creds = meta_graph.resolve_meta_creds(cfg, handle="@nobody")
    assert (creds.ig_user_id, creds.token) == ("ig-global-1", "tok-global")


def test_resolve_no_creds_at_all_is_none_pair(tmp_path, monkeypatch):
    # No global, no per-account -> (None, None): the existing degraded (never-crash) behavior.
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    cfg = Config(root=tmp_path)
    creds = meta_graph.resolve_meta_creds(cfg, handle=None)
    assert (creds.ig_user_id, creds.token) == (None, None)


# ── threading: list_user_media uses the RIGHT handle's id/token ───────────────────────────────────────

def test_list_user_media_uses_per_account_creds(tmp_path, monkeypatch):
    # With per-account creds passed in, the /media path is keyed on the PER-ACCOUNT ig id and the token
    # param carries the per-account token — NOT the global.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({"ig-stan-99/media": _Resp(200, {"data": [{"id": "m1", "permalink": "https://p/1"}]})})
    creds = meta_graph.MetaCreds(ig_user_id="ig-stan-99", token="pa-tok")
    out = meta_graph.list_user_media(cfg, get=get, creds=creds)
    assert [m["id"] for m in out] == ["m1"]
    url, params = get.calls[0]
    assert "ig-stan-99/media" in url                       # keyed on the per-account id, not ig-global-1
    assert params["access_token"] == "pa-tok"     # per-account token on the wire, not tok-global


def test_list_user_media_default_creds_is_global_byte_identical(tmp_path, monkeypatch):
    # No creds= passed -> resolves the GLOBAL creds (the existing single-account behavior, byte-identical).
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({"ig-global-1/media": _Resp(200, {"data": [{"id": "g1", "permalink": "https://p/g"}]})})
    out = meta_graph.list_user_media(cfg, get=get)
    assert [m["id"] for m in out] == ["g1"]
    url, params = get.calls[0]
    assert "ig-global-1/media" in url
    assert params["access_token"] == "tok-global"


def test_list_user_media_empty_per_account_creds_fail_open(tmp_path, monkeypatch):
    # Per-account creds with a missing token -> fail-open [] with NO network call (mirrors the no-creds guard).
    monkeypatch.delenv("META_IG_USER_ID", raising=False)
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    cfg = Config(root=tmp_path)
    get = _router({})
    creds = meta_graph.MetaCreds(ig_user_id="ig-stan-99", token=None)
    assert meta_graph.list_user_media(cfg, get=get, creds=creds) == []
    assert get.calls == []                                  # no creds -> nothing enumerated, no HTTP


def test_media_insights_uses_per_account_token(tmp_path, monkeypatch):
    # media_insights with per-account creds sends the per-account token; the metric request is unchanged.
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    cfg = Config(root=tmp_path)
    get = _router({"m1/insights": _Resp(200, {"data": [{"name": "reach", "values": [{"value": 42}]}]})})
    creds = meta_graph.MetaCreds(ig_user_id="ig-stan-99", token="pa-tok")
    out = meta_graph.media_insights(cfg, "m1", "REELS", get=get, creds=creds)
    assert out == {"reach": 42}
    _url, params = get.calls[0]
    assert params["access_token"] == "pa-tok"


# ── the per-account setter persists the ig id to accounts.json (non-secret), preserving siblings ──────

def test_set_ig_user_id_persists_and_preserves_siblings(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@stan", "account_id": "acc-x", "platforms": ["instagram"],
                           "status": "active", "integrations": {"instagram": "ig_1"}},
                          {"handle": "@other", "account_id": "acc-y", "platforms": ["tiktok"], "status": "active"}])
    set_ig_user_id(cfg, "@stan", "ig-stan-99")
    raw = json.loads(cfg.accounts_path.read_text())
    rows = {r["handle"]: r for r in raw["accounts"]}
    assert rows["@stan"]["ig_user_id"] == "ig-stan-99"
    assert rows["@stan"]["integrations"] == {"instagram": "ig_1"}   # sibling field preserved
    assert rows["@stan"]["account_id"] == "acc-x"
    assert "@other" in rows and rows["@other"]["account_id"] == "acc-y"  # sibling account untouched


def test_set_ig_user_id_blank_clears(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    _write_accounts(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"],
                           "status": "active", "ig_user_id": "ig-stan-99"}])
    set_ig_user_id(cfg, "@stan", "")
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0].get("ig_user_id") in (None, "")


def test_account_model_ig_user_id_default_none(tmp_path):
    # Additive field: a legacy account row with no ig_user_id loads fine, default None (byte-identical).
    a = Account(handle="@legacy", platforms=[Platform.instagram])
    assert a.ig_user_id is None
