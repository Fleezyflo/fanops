"""OPS-ZERNIO: zernio_dep_health follows doctor's authenticated /accounts posture.

No network. Auth is monkeypatched; a completed 401 or unreachable result is down.
"""
from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.health_probes import zernio_dep_health


def _cfg(tmp_path, monkeypatch, *, key=None):
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    monkeypatch.setattr("fanops.secret_provider.get_secret", lambda *a, **k: None)
    if key is not None:
        monkeypatch.setenv("ZERNIO_API_KEY", key)
    return Config(root=tmp_path)


def test_zernio_dep_health_skips_when_creds_absent(tmp_path, monkeypatch):
    def _no_auth(cfg):
        raise AssertionError("auth probe must not run without Zernio creds")
    monkeypatch.setattr("fanops.post.zernio.zernio_check_auth", _no_auth)
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch))
    assert row.name == "zernio" and row.ok is True
    assert row.detail == "skipped (not configured)"


def test_zernio_dep_health_up_when_auth_ok(tmp_path, monkeypatch):
    monkeypatch.setattr("fanops.post.zernio.zernio_check_auth", lambda cfg: True)
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch, key="sk_test"))
    assert row.name == "zernio" and row.ok is True
    assert row.detail == "reachable"


def test_zernio_dep_health_down_when_auth_false(tmp_path, monkeypatch):
    monkeypatch.setattr("fanops.post.zernio.zernio_check_auth", lambda cfg: False)
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch, key="sk_test"))
    assert row.name == "zernio" and row.ok is False
    assert "unreachable" in row.detail


def test_zernio_dep_health_down_on_zernio_auth_error(tmp_path, monkeypatch):
    def _boom(cfg):
        raise ZernioAuthError("401")
    monkeypatch.setattr("fanops.post.zernio.zernio_check_auth", _boom)
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch, key="sk_test"))
    assert row.name == "zernio" and row.ok is False
    assert "401" in row.detail


def test_zernio_dep_health_skips_when_reach_check_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("fanops.doctor._zernio_reach_check", lambda cfg, **k: None)
    row = zernio_dep_health(_cfg(tmp_path, monkeypatch, key="sk_test"))
    assert row.name == "zernio" and row.ok is True
    assert row.detail == "skipped (not configured)"
