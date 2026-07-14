# tests/test_secret_provider.py — MOL-359: keyring read layer fail-open behind Config secrets
import importlib
import logging
import sys

import pytest

from fanops.config import Config
from fanops import secret_provider


def _reload_secret_provider(monkeypatch, keyring_mod):
    """Install a fake keyring module and reload secret_provider so the lazy import sees it."""
    monkeypatch.setitem(sys.modules, "keyring", keyring_mod)
    return importlib.reload(secret_provider)


def test_get_secret_returns_keyring_value(monkeypatch):
    class _Kr:
        @staticmethod
        def get_password(service, username):
            assert service == "fanops"
            return " ring-secret " if username == "POSTIZ_API_KEY" else None
    sp = _reload_secret_provider(monkeypatch, _Kr())
    assert sp.get_secret("POSTIZ_API_KEY") == "ring-secret"


def test_get_secret_absent_returns_none(monkeypatch):
    class _Kr:
        @staticmethod
        def get_password(service, username):
            return None
    sp = _reload_secret_provider(monkeypatch, _Kr())
    assert sp.get_secret("POSTIZ_API_KEY") is None


def test_get_secret_import_error_is_silent(monkeypatch, caplog):
    """Absent [keyring] extra is the DOCUMENTED optional state, not a fault: read fails open to env
    with NO warning (else every secret-property read of a healthy env-only install spams a WARNING)."""
    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    sp = importlib.reload(secret_provider)
    with caplog.at_level(logging.WARNING):
        assert sp.get_secret("POSTIZ_API_KEY") is None
    assert not any("keyring" in r.getMessage().lower() for r in caplog.records)


def test_get_secret_backend_error_fail_open(monkeypatch, caplog):
    """keyring INSTALLED but backend broken CAN mask an operator secret — still worth a breadcrumb."""
    class _Kr:
        @staticmethod
        def get_password(service, username):
            raise RuntimeError("No recommended backend")
    sp = _reload_secret_provider(monkeypatch, _Kr())
    with caplog.at_level(logging.WARNING):
        assert sp.get_secret("ZERNIO_API_KEY") is None
    assert any("fail-open" in r.getMessage().lower() or "ZERNIO_API_KEY" in r.getMessage() for r in caplog.records)


def test_set_secret_verifies_readback(monkeypatch):
    """A backend that ACCEPTS set_password but DROPS the value (get returns None) is a failed write,
    not a success — set_secret must raise so the caller never scrubs the .env fallback (see
    _dual_write). 'Written' means 'retrievable'."""
    class _DropKr:
        @staticmethod
        def set_password(service, username, password):
            return None                      # accepts the write...
        @staticmethod
        def get_password(service, username):
            return None                      # ...but never persisted it
    sp = _reload_secret_provider(monkeypatch, _DropKr())
    with pytest.raises(OSError, match="round-trip"):
        sp.set_secret("POSTIZ_API_KEY", "vanishes")


def test_set_secret_readback_mismatch_raises(monkeypatch):
    """A backend that returns a DIFFERENT value than written is also a failed write."""
    class _WrongKr:
        @staticmethod
        def set_password(service, username, password):
            return None
        @staticmethod
        def get_password(service, username):
            return "something-else"
    sp = _reload_secret_provider(monkeypatch, _WrongKr())
    with pytest.raises(OSError, match="round-trip"):
        sp.set_secret("POSTIZ_API_KEY", "intended")


def test_set_secret_round_trips_on_good_backend(monkeypatch):
    """A real backend that persists the value is accepted with no error."""
    class _GoodKr:
        _v: dict = {}
        @staticmethod
        def set_password(service, username, password):
            _GoodKr._v[(service, username)] = password
        @staticmethod
        def get_password(service, username):
            return _GoodKr._v.get((service, username))
    sp = _reload_secret_provider(monkeypatch, _GoodKr())
    sp.set_secret("POSTIZ_API_KEY", "persists")     # must not raise
    assert sp.get_secret("POSTIZ_API_KEY") == "persists"


def test_resolve_secret_keyring_wins(monkeypatch):
    monkeypatch.setattr(secret_provider, "get_secret", lambda k, quiet=False: "kr" if k == "POSTIZ_API_KEY" else None)
    assert secret_provider.resolve_secret("POSTIZ_API_KEY", "env") == "kr"


def test_resolve_secret_falls_back_unchanged(monkeypatch):
    monkeypatch.setattr(secret_provider, "get_secret", lambda k, quiet=False: None)
    assert secret_provider.resolve_secret("POSTIZ_API_KEY", "env-val") == "env-val"
    assert secret_provider.resolve_secret("POSTIZ_API_KEY", None) is None


@pytest.mark.parametrize("prop,env_key,env_val", [
    ("postiz_api_key", "POSTIZ_API_KEY", "env-postiz"),
    ("zernio_api_key", "ZERNIO_API_KEY", "env-zernio"),
    ("meta_graph_token", "META_GRAPH_TOKEN", "env-meta"),
])
def test_config_secret_property_keyring_wins(monkeypatch, tmp_path, prop, env_key, env_val):
    monkeypatch.setenv(env_key, env_val)
    monkeypatch.setattr(secret_provider, "get_secret", lambda k, quiet=False: f"kr-{k}" if k == env_key else None)
    cfg = Config(root=tmp_path)
    assert getattr(cfg, prop) == f"kr-{env_key}"


@pytest.mark.parametrize("prop,env_key,env_val", [
    ("postiz_api_key", "POSTIZ_API_KEY", "env-postiz"),
    ("zernio_api_key", "ZERNIO_API_KEY", "env-zernio"),
    ("meta_graph_token", "META_GRAPH_TOKEN", "env-meta"),
])
def test_config_secret_property_env_fallback(monkeypatch, tmp_path, prop, env_key, env_val):
    monkeypatch.setenv(env_key, env_val)
    monkeypatch.setattr(secret_provider, "get_secret", lambda k, quiet=False: None)
    cfg = Config(root=tmp_path)
    assert getattr(cfg, prop) == env_val


def test_meta_token_for_per_handle_keyring_wins(monkeypatch, tmp_path):
    handle = "@markmakmouly"
    env_key = "META_GRAPH_TOKEN__MARKMAKMOULY"
    monkeypatch.setenv("META_GRAPH_TOKEN", "global-env")
    monkeypatch.setenv(env_key, "per-env")
    monkeypatch.setattr(secret_provider, "get_secret",
                        lambda k, quiet=False: "per-kr" if k == env_key else ("global-kr" if k == "META_GRAPH_TOKEN" else None))
    cfg = Config(root=tmp_path)
    assert cfg.meta_token_for(handle) == "per-kr"


def test_meta_token_for_global_keyring_when_no_per_handle(monkeypatch, tmp_path):
    monkeypatch.delenv("META_GRAPH_TOKEN", raising=False)
    monkeypatch.setattr(secret_provider, "get_secret",
                        lambda k, quiet=False: "global-kr" if k == "META_GRAPH_TOKEN" else None)
    cfg = Config(root=tmp_path)
    assert cfg.meta_token_for("@someone") == "global-kr"
    assert cfg.meta_token_for(None) == "global-kr"


def test_config_no_keyring_backend_byte_identical_to_env(monkeypatch, tmp_path, caplog):
    """Headless / no-backend CI: ImportError on keyring must not change the env read."""
    monkeypatch.setenv("POSTIZ_API_KEY", "only-env")
    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    importlib.reload(secret_provider)
    with caplog.at_level(logging.WARNING):
        cfg = Config(root=tmp_path)
        assert cfg.postiz_api_key == "only-env"


class _BrokenKr:
    @staticmethod
    def get_password(service, username):
        raise RuntimeError("Keychain locked")  # installed backend, but genuinely down


def test_broken_backend_warns_once(monkeypatch, caplog):
    """A present-but-broken keyring backend must log ONE breadcrumb per process, not one per read —
    get_secret runs on every secret-property read; the per-read warning once flooded studio.err 64k lines."""
    sp = _reload_secret_provider(monkeypatch, _BrokenKr())   # fresh reload -> _backend_warned = False
    with caplog.at_level(logging.WARNING, logger="fanops.secret_provider"):
        assert sp.get_secret("POSTIZ_API_KEY") is None
        assert sp.get_secret("POSTIZ_API_KEY") is None
        assert sp.get_secret("ZERNIO_API_KEY") is None
    warnings = [r for r in caplog.records if "keyring read unavailable" in r.getMessage()]
    assert len(warnings) == 1                                # exactly ONE across 3 broken reads


def test_broken_backend_quiet_never_warns(monkeypatch, caplog):
    """quiet=True suppresses the breadcrumb entirely, even on a broken backend."""
    sp = _reload_secret_provider(monkeypatch, _BrokenKr())
    with caplog.at_level(logging.WARNING, logger="fanops.secret_provider"):
        assert sp.get_secret("POSTIZ_API_KEY", quiet=True) is None
        assert sp.get_secret("META_GRAPH_TOKEN", quiet=True) is None
    assert not [r for r in caplog.records if "keyring read unavailable" in r.getMessage()]
