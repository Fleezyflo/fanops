"""OPS-ZERNIO: with creds, zernio_dep_health is a 2xx GET. No creds skips. No network."""
import requests

from fanops.config import Config
from fanops.health_probes import zernio_dep_health


def _cfg(tmp_path, monkeypatch, *, key=None):
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    monkeypatch.setattr("fanops.secret_provider.get_secret", lambda *a, **k: None)
    if key is not None:
        monkeypatch.setenv("ZERNIO_API_KEY", key)
        monkeypatch.setenv("ZERNIO_API_URL", "http://zernio.test/v1")
    return Config(root=tmp_path)


def test_zernio_dep_health_skips_when_creds_absent(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network")

    monkeypatch.setattr(requests, "get", boom)
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch))
    assert row.name == "zernio" and row.ok is True
    assert row.detail == "skipped (not configured)"


def test_zernio_dep_health_up_on_2xx(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout=3: type("R", (), {"status_code": 200})())
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch, key="sk_test"))
    assert row.name == "zernio" and row.ok is True
    assert row.detail == "reachable"


def test_zernio_dep_health_down_on_401(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout=3: type("R", (), {"status_code": 401})())
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch, key="sk_test"))
    assert row.name == "zernio" and row.ok is False
    assert row.detail == "HTTP 401"


def test_zernio_dep_health_down_on_connection_error(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch, key="sk_test"))
    assert row.name == "zernio" and row.ok is False
    assert row.detail == "unreachable"
