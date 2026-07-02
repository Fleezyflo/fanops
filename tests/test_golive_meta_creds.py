# tests/test_golive_meta_creds.py — the Studio "Go Live" per-account Meta credential surface: set ONE
# handle's IG user id (non-secret -> accounts.json) + its Graph access token (SECRET -> a per-handle .env
# key, dual-written like POSTIZ_API_KEY). Load-bearing properties: the ig id lands in accounts.json; the
# token is WRITE-ONLY (dual-written to .env + os.environ, NEVER echoed in the result); the resolver then
# picks the per-handle creds for that handle. Env isolation mirrors test_studio_golive: undo golive's
# direct os.environ writes after every test so a per-handle token never leaks.
import json
import os
import pytest
from fanops.config import Config
from fanops.studio import golive
from fanops import meta_graph

_ENV_KEYS = ("META_IG_USER_ID", "META_GRAPH_TOKEN", "META_GRAPH_TOKEN__STAN", "META_GRAPH_TOKEN__MARKMAKMOULY")
_ENV_BASELINE = {k: os.environ.get(k) for k in _ENV_KEYS}

@pytest.fixture(autouse=True)
def _restore_env():
    yield
    for k, v in _ENV_BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)             # clean start + registers the key for teardown-restore
    return Config(root=tmp_path)

def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def test_set_meta_creds_writes_ig_id_to_accounts_and_token_write_only(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    res = golive.set_meta_creds(cfg, "@stan", "ig-stan-99", "pa-tok")
    assert res.ok is True
    # the ig id (non-secret) landed in accounts.json
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["ig_user_id"] == "ig-stan-99"
    # the token (SECRET) is dual-written to a PER-HANDLE .env key + os.environ, never accounts.json
    env = (tmp_path / ".env").read_text()
    assert "pa-tok" in env                     # durable
    assert "META_GRAPH_TOKEN__STAN=pa-tok" in env
    assert os.environ["META_GRAPH_TOKEN__STAN"] == "pa-tok"   # in-process (no restart)
    assert "pa-tok" not in json.dumps(raw)     # NEVER in accounts.json (secrets live in .env)
    # WRITE-ONLY: the token must NEVER appear in the result
    assert "pa-tok" not in repr(res)


def test_set_meta_creds_then_resolver_picks_per_account(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("META_IG_USER_ID", "ig-global-1")
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok-global")
    _seed(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    golive.set_meta_creds(cfg, "@stan", "ig-stan-99", "pa-tok")
    creds = meta_graph.resolve_meta_creds(cfg, handle="@stan")
    assert creds.ig_user_id == "ig-stan-99"
    assert creds.token == "pa-tok"
    # a DIFFERENT handle with no per-account creds still resolves the global (byte-identical)
    assert meta_graph.resolve_meta_creds(cfg, handle="@markmakmouly").token == "tok-global"


def test_set_meta_creds_id_only_leaves_token_unchanged(tmp_path, monkeypatch):
    # A blank token updates only the ig id (mirrors set_postiz_config's blank-key = URL-only). No token write.
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    res = golive.set_meta_creds(cfg, "@stan", "ig-stan-99", "")
    assert res.ok is True
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["ig_user_id"] == "ig-stan-99"
    # blank token -> the per-handle key is NOT written
    env = (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else ""
    assert "META_GRAPH_TOKEN__STAN" not in env


def test_set_meta_creds_unknown_handle_clean_error(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    res = golive.set_meta_creds(cfg, "@nobody", "ig-x", "T")
    assert res.ok is False
    assert "@nobody" in res.error
    # and no token leaked into .env on the failed write
    env = (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else ""
    assert "T" not in env or "META_GRAPH_TOKEN__NOBODY" not in env


def test_set_meta_creds_blank_handle_clean_error(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@stan", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    res = golive.set_meta_creds(cfg, "", "ig-x", "T")
    assert res.ok is False
