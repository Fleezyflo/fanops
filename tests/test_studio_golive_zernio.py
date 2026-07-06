# tests/test_studio_golive_zernio.py — Zernio slice 4: connect Zernio + per-account backend routing
# ENTIRELY in the Go-Live tab. set_zernio_config (key-only, hosted) dual-writes + tests; set_account_backend
# routes a channel to a backend, GATED for a live backend (creds present + confirm — the per-account
# "go live" gate, mirroring go_live). Env isolation: the autouse fixture restores ZERNIO_API_KEY +
# FANOPS_POSTER to baseline (the os.environ-leak guard) so a write never leaks into a later test.
import json
import os
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.studio import golive, views

_KEYS = ("ZERNIO_API_KEY", "FANOPS_POSTER")
_BASELINE = {k: os.environ.get(k) for k in _KEYS}

@pytest.fixture(autouse=True)
def _restore_env():
    yield
    for k, v in _BASELINE.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)

def _seed(cfg, rows):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": rows}))

def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


# ---- set_zernio_config: dual-write (key only), tested, key NEVER returned ----
def test_set_zernio_config_dual_writes_and_tests(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.zernio, "zernio_check_auth", lambda c: True)
    res = golive.set_zernio_config(cfg, "sk_SECRETKEY")
    assert res.ok is True
    assert "ZERNIO_API_KEY=sk_SECRETKEY" in (tmp_path / ".env").read_text()   # durable
    assert os.environ["ZERNIO_API_KEY"] == "sk_SECRETKEY"                      # in-process
    assert "sk_SECRETKEY" not in json.dumps(res.detail)                        # key NEVER echoed

def test_set_zernio_config_blank_key_rejected(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    res = golive.set_zernio_config(cfg, "")
    assert res.ok is False and not (tmp_path / ".env").exists()

def test_set_zernio_config_auth_failure_redacted(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    def boom(c): raise ZernioAuthError("denied SENTINEL")
    monkeypatch.setattr(golive.zernio, "zernio_check_auth", boom)
    res = golive.set_zernio_config(cfg, "sk_x")
    assert res.ok is False and "SENTINEL" not in res.error and "sk_x" not in res.error

def test_set_zernio_config_unreachable_clean(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.zernio, "zernio_check_auth", lambda c: False)
    res = golive.set_zernio_config(cfg, "sk_x")
    assert res.ok is False and "sk_x" not in res.error


# ---- set_account_backend: live backend gated by creds + confirm ----
def test_set_account_backend_live_requires_confirm(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk_x")
    _seed(cfg, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"], "status": "active"}])
    res = golive.set_account_backend(cfg, "tk", "tiktok", "zernio", confirmed=False)
    assert res.ok is False                                                     # live backend needs confirm

def test_set_account_backend_live_requires_creds(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)                                        # no ZERNIO_API_KEY
    _seed(cfg, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"], "status": "active"}])
    res = golive.set_account_backend(cfg, "tk", "tiktok", "zernio", confirmed=True)
    assert res.ok is False and "ZERNIO_API_KEY" in res.error                   # not ready: no creds

def test_set_account_backend_live_writes_with_creds_and_confirm(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk_x")
    # H3: a live route requires a real per-platform integration id (not just the legacy shared account_id).
    _seed(cfg, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_x"}}])
    res = golive.set_account_backend(cfg, "tk", "tiktok", "zernio", confirmed=True)
    assert res.ok is True
    from fanops.accounts import Accounts
    from fanops.models import Platform
    assert Accounts.load(cfg).resolve_backend("tk", Platform.tiktok) == "zernio"

def test_set_account_backend_default_clears_no_confirm(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"],
                 "status": "active", "backends": {"tiktok": "zernio"}}])
    res = golive.set_account_backend(cfg, "tk", "tiktok", "default", confirmed=False)  # clearing needs no confirm
    assert res.ok is True
    from fanops.accounts import Accounts
    from fanops.models import Platform
    assert Accounts.load(cfg).resolve_backend("tk", Platform.tiktok) is None

def test_set_account_backend_unknown_handle_clean(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); _seed(cfg, [])
    res = golive.set_account_backend(cfg, "ghost", "tiktok", "default", confirmed=False)
    assert res.ok is False


# ---- refresh_zernio_accounts: picklist ----
def test_refresh_zernio_accounts_returns_list(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk_x")
    from fanops.post.zernio import ZernioAccount
    monkeypatch.setattr(golive.zernio, "zernio_list_accounts",
                        lambda c: [ZernioAccount("acc_abc", "fan1", "tiktok")])
    res = golive.refresh_zernio_accounts(cfg)
    assert res.ok is True and res.detail["accounts"][0].id == "acc_abc"


# ---- read-model: status carries zernio_key_set; channel carries its backend ----
def test_golive_status_carries_zernio_key_set(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    assert views.golive_status(cfg).zernio_key_set is False
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_x")
    assert views.golive_status(cfg).zernio_key_set is True

def test_golive_channel_carries_backend_override(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"],
                 "status": "active", "backends": {"tiktok": "zernio"}}])
    acct = views.golive_accounts(cfg)[0]
    assert acct.channels[0].backend == "zernio"


# ---- panel renders the Zernio connect block + a per-channel backend selector ----
def test_golive_panel_renders_zernio_and_backend_selector(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    _seed(cfg, [{"handle": "@tk", "account_id": "acc", "platforms": ["tiktok"], "status": "active"}])
    html = _client(cfg).get("/golive").data
    assert b"Zernio" in html
    assert b"do_golive_zernio_config" in html or b"/golive/zernio-config" in html
    assert b"/golive/account/backend" in html
