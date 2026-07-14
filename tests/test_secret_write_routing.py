# tests/test_secret_write_routing.py — MOL-360: secret WRITE boundary routes to keyring, not .env
import json
import os

import pytest

from fanops.config import Config
from fanops import secret_provider
from fanops.studio import golive
from tests.keyring_fake import MemKeyring, install_mem_keyring


@pytest.fixture(autouse=True)
def _mem_keyring(monkeypatch):
    return install_mem_keyring(monkeypatch)


def _clean(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY", "FANOPS_LIVE", "META_GRAPH_TOKEN",
              "META_GRAPH_TOKEN__STAN"):
        monkeypatch.delenv(k, raising=False)
    return Config(root=tmp_path)


def test_is_secret_env_key_global_and_per_handle():
    assert secret_provider.is_secret_env_key("POSTIZ_API_KEY")
    assert secret_provider.is_secret_env_key("ZERNIO_API_KEY")
    assert secret_provider.is_secret_env_key("META_GRAPH_TOKEN")
    assert secret_provider.is_secret_env_key("META_GRAPH_TOKEN__STAN")
    assert not secret_provider.is_secret_env_key("POSTIZ_URL")
    assert not secret_provider.is_secret_env_key("FANOPS_LIVE")


def test_dual_write_secret_stores_keyring_not_env(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("POSTIZ_URL=https://old.example\nPOSTIZ_API_KEY=legacy-plain\n")
    err = golive._dual_write(cfg, "POSTIZ_API_KEY", "ring-secret")
    assert err is None
    env = (tmp_path / ".env").read_text()
    assert "POSTIZ_URL=https://old.example" in env
    assert "POSTIZ_API_KEY" not in env
    assert "ring-secret" not in env
    assert MemKeyring.get_password("fanops", "POSTIZ_API_KEY") == "ring-secret"
    assert os.environ["POSTIZ_API_KEY"] == "ring-secret"


def test_dual_write_non_secret_still_writes_env(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    err = golive._dual_write(cfg, "POSTIZ_URL", "https://postiz.example.com")
    assert err is None
    assert (tmp_path / ".env").read_text() == "POSTIZ_URL=https://postiz.example.com\n"
    assert os.environ["POSTIZ_URL"] == "https://postiz.example.com"
    assert MemKeyring.get_password("fanops", "POSTIZ_URL") is None


def test_set_postiz_config_routes_api_key_to_keyring(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", lambda c: True)
    res = golive.set_postiz_config(cfg, "https://postiz.example.com", "SECRETKEY")
    assert res.ok is True
    env = (tmp_path / ".env").read_text()
    assert "POSTIZ_URL=https://postiz.example.com" in env
    assert "POSTIZ_API_KEY" not in env
    assert "SECRETKEY" not in env
    assert MemKeyring.get_password("fanops", "POSTIZ_API_KEY") == "SECRETKEY"
    assert os.environ["POSTIZ_API_KEY"] == "SECRETKEY"
    assert "SECRETKEY" not in repr(res)


def test_set_zernio_config_routes_key_to_keyring(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.zernio, "zernio_check_auth", lambda c: True)
    res = golive.set_zernio_config(cfg, "sk_SECRETKEY")
    assert res.ok is True
    env = (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else ""
    assert "ZERNIO_API_KEY" not in env
    assert "sk_SECRETKEY" not in env
    assert MemKeyring.get_password("fanops", "ZERNIO_API_KEY") == "sk_SECRETKEY"
    assert os.environ["ZERNIO_API_KEY"] == "sk_SECRETKEY"
    assert "sk_SECRETKEY" not in json.dumps(res.detail)


def test_set_meta_creds_routes_per_handle_token_to_keyring(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@stan", "account_id": "", "platforms": ["instagram"], "status": "active"},
    ]}))
    res = golive.set_meta_creds(cfg, "stan", "ig-stan-99", "pa-tok")
    assert res.ok is True
    env = (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else ""
    assert "META_GRAPH_TOKEN__STAN" not in env
    assert "pa-tok" not in env
    assert MemKeyring.get_password("fanops", "META_GRAPH_TOKEN__STAN") == "pa-tok"
    assert os.environ["META_GRAPH_TOKEN__STAN"] == "pa-tok"
    assert "pa-tok" not in repr(res)


def test_keyring_write_round_trip_via_config(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", lambda c: True)
    golive.set_postiz_config(cfg, "https://postiz.example.com", "round-trip-key")
    assert cfg.postiz_api_key == "round-trip-key"


def test_set_secret_rejects_newline(monkeypatch):
    install_mem_keyring(monkeypatch)
    with pytest.raises(ValueError, match="newline"):
        secret_provider.set_secret("POSTIZ_API_KEY", "bad\nline")


def test_dual_write_does_not_scrub_env_when_keyring_write_vanishes(tmp_path, monkeypatch):
    """THE defect: writes are keyring-only and _dual_write SCRUBS the plaintext .env fallback on a
    'successful' write. If the backend accepts the write but drops the value, the old code erased the
    secret from BOTH stores. set_secret now verifies read-back, so _dual_write returns an error AND
    leaves the legacy .env value (and os.environ) intact — the secret is never lost."""
    class _DropKeyring:
        @staticmethod
        def set_password(service, username, password): return None      # accepts...
        @staticmethod
        def get_password(service, username): return None                # ...but drops it
        @staticmethod
        def delete_password(service, username): return None
    import importlib, sys
    monkeypatch.setitem(sys.modules, "keyring", _DropKeyring)
    sp = importlib.reload(secret_provider)
    monkeypatch.setattr(golive, "secret_provider", sp, raising=False)

    cfg = _clean(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("POSTIZ_API_KEY=legacy-still-here\n")
    monkeypatch.setenv("POSTIZ_API_KEY", "legacy-still-here")

    err = golive._dual_write(cfg, "POSTIZ_API_KEY", "new-secret-that-wont-persist")

    assert err is not None                                              # operator is told it failed
    assert "POSTIZ_API_KEY=legacy-still-here" in (tmp_path / ".env").read_text()  # fallback preserved
    assert os.environ["POSTIZ_API_KEY"] == "legacy-still-here"          # process env untouched
    # restore the real in-memory keyring for any later test in this module
    install_mem_keyring(monkeypatch)
