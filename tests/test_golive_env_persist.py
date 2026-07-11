"""go_live must persist FANOPS_LIVE across reloads and must not leave a stale FANOPS_POSTER=dryrun
line that load_dotenv(override=True) re-injects on process startup (cli.main).

Regression: M3b go_live writes FANOPS_LIVE=1 only; .env.example seeds FANOPS_POSTER=dryrun; the
pre-M3b go_dryrun wrote FANOPS_POSTER=dryrun. Operators see LIVE=1 + POSTER=dryrun and think the
flip reverted."""
from __future__ import annotations
import json
import os
import pytest
from fanops.config import Config
from fanops.studio import golive

_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY",
         "FANOPS_RESPONDER")
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


def _seed_live_ready(cfg: Config):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "backends": {"instagram": "postiz"}}]}))


def test_go_live_clears_stale_fanops_poster_dryrun(tmp_path, monkeypatch):
    """A .env.example FANOPS_POSTER=dryrun must not survive a successful go_live."""
    cfg = _clean(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("FANOPS_POSTER=dryrun\nPOSTIZ_API_KEY=k\n")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _seed_live_ready(cfg)
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is True
    body = (tmp_path / ".env").read_text()
    assert "FANOPS_LIVE=1" in body
    assert "FANOPS_POSTER=dryrun" not in body
    assert os.environ.get("FANOPS_POSTER") in (None, "")


def test_go_live_persists_fanops_live_across_config_reload(tmp_path, monkeypatch):
    """Simulate Studio restart / daemon tick: a fresh Config() must still read live."""
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _seed_live_ready(cfg)
    assert golive.go_live(cfg, confirmed=True).ok is True
    reloaded = Config(root=tmp_path)
    assert reloaded.is_live is True
    assert "FANOPS_LIVE=1" in (tmp_path / ".env").read_text()


def test_set_postiz_config_after_go_live_does_not_clobber_fanops_live(tmp_path, monkeypatch):
    """Other Go-Live dual-writes must preserve FANOPS_LIVE=1 (advance/studio reload safety)."""
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    _seed_live_ready(cfg)
    assert golive.go_live(cfg, confirmed=True).ok is True
    monkeypatch.setattr(golive.postiz, "postiz_check_auth", lambda c: True)
    assert golive.set_postiz_config(cfg, "https://postiz.example.com", "").ok is True
    body = (tmp_path / ".env").read_text()
    assert "FANOPS_LIVE=1" in body
    assert Config(root=tmp_path).is_live is True


def test_go_dryrun_does_not_write_fanops_poster_dryrun(tmp_path, monkeypatch):
    """go_dryrun must only flip FANOPS_LIVE=0 — never re-seed FANOPS_POSTER=dryrun (pre-M3b bug)."""
    cfg = _clean(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("FANOPS_LIVE=1\nFANOPS_POSTER=postiz\nPOSTIZ_API_KEY=k\n")
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz")
    assert golive.go_dryrun(cfg).ok is True
    body = (tmp_path / ".env").read_text()
    assert "FANOPS_LIVE=0" in body
    assert "FANOPS_POSTER=dryrun" not in body
    assert "FANOPS_POSTER=postiz" in body
