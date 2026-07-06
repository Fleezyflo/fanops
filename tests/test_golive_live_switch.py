"""M3b — the global LIVE switch on the WRITE side. `go_live` no longer flips a backend; it writes
FANOPS_LIVE=1 (the operator-facing yes/no) gated on ≥1 active channel having a provider whose creds are
present, plus an explicit confirm. `go_dryrun` writes FANOPS_LIVE=0 (safe, no confirm). The publish
provider is per-channel (M3a); the global switch is now provider-agnostic. `Accounts.live_ready_channels`
is the shared readiness primitive (also feeds the status banner's mode label). A NEW deployment requires
an explicit per-channel provider; the running FANOPS_POSTER deployment keeps working via the bridge."""
import json
import os
import pytest
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.studio import golive
from fanops.studio import views


# os.environ-leak guard: go_live/go_dryrun DIRECT-write FANOPS_LIVE; restore the baseline so a flip never
# leaks into a later test (delenv of an already-absent key registers no restoration — pytest-os-environ-leak-guard).
_KEYS = ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_URL", "POSTIZ_API_KEY", "ZERNIO_API_KEY", "BLOTATO_API_KEY")
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


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


# ------------------------------------------------------------------ live_ready_channels ----
def test_ready_channels_explicit_provider_with_creds(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "backends": {"tiktok": "zernio"}}])
    ready = Accounts.load(cfg).live_ready_channels()
    assert ready == [("tk", "tiktok", "zernio")]


def test_ready_channels_excludes_provider_without_creds(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)                       # NO ZERNIO_API_KEY
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "backends": {"tiktok": "zernio"}}])
    assert Accounts.load(cfg).live_ready_channels() == []     # provider declared but key absent -> not ready


def test_ready_channels_excludes_channel_with_no_provider(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)                       # no explicit provider, no legacy global -> None
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    assert Accounts.load(cfg).live_ready_channels() == []


def test_ready_channels_bridges_legacy_global(tmp_path, monkeypatch):
    # the running deployment: a channel with no explicit provider is ready via the FANOPS_POSTER bridge.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    assert Accounts.load(cfg).live_ready_channels() == [("ig", "instagram", "postiz")]


def test_ready_channels_excludes_inactive_accounts(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "planned",
                 "backends": {"tiktok": "zernio"}}])
    assert Accounts.load(cfg).live_ready_channels() == []     # planned/warming/retired never count


# ------------------------------------------------------------------ go_live / go_dryrun ----
def test_go_live_writes_fanops_live_not_poster(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    # R2: validate() requires integrations[p] AND backends[p] paired (no drift) — pair them.
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}}])
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is True and res.detail["live"] is True
    assert os.environ["FANOPS_LIVE"] == "1"                                 # in-process
    assert "FANOPS_LIVE=1" in (tmp_path / ".env").read_text()               # durable
    assert "FANOPS_POSTER" not in (tmp_path / ".env").read_text()           # no longer the global backend setter
    assert cfg.is_live is True


def test_go_live_blocked_when_no_channel_has_a_provider(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)                       # active channel but no provider, no creds
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is False and "provider" in res.error.lower()
    assert cfg.is_live is False                               # NOT flipped
    assert not (tmp_path / ".env").exists() or "FANOPS_LIVE=1" not in (tmp_path / ".env").read_text()


def test_go_live_needs_confirm_even_when_ready(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    # R2: validate() requires integrations[p] AND backends[p] paired (no drift) — pair them.
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "integrations": {"tiktok": "tk_1"}, "backends": {"tiktok": "zernio"}}])
    res = golive.go_live(cfg, confirmed=False)
    assert res.ok is False and "confirm" in res.error.lower()
    assert cfg.is_live is False                               # ready, but withheld without the human gate


def test_go_live_back_compat_bridge_lets_running_deployment_reflip(tmp_path, monkeypatch):
    # the live Postiz deployment (FANOPS_POSTER=postiz still in .env) re-flips via the new switch: the IG
    # channel bridges to postiz, postiz has creds -> ready -> go_live writes FANOPS_LIVE=1.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    _seed(cfg, [{"handle": "@ig", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    res = golive.go_live(cfg, confirmed=True)
    assert res.ok is True and os.environ["FANOPS_LIVE"] == "1"


def test_go_dryrun_writes_fanops_live_zero(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path); monkeypatch.setenv("FANOPS_LIVE", "1")
    res = golive.go_dryrun(cfg)
    assert res.ok is True and res.detail["live"] is False
    assert os.environ["FANOPS_LIVE"] == "0"
    assert "FANOPS_LIVE=0" in (tmp_path / ".env").read_text()
    assert cfg.is_live is False


# ------------------------------------------------------------------ truthful mode label ----
def test_status_mode_shows_provider_when_live_via_fanops_live(tmp_path, monkeypatch):
    # the new deployment: live via FANOPS_LIVE + an explicit zernio channel. The banner must NOT say
    # "dryrun" (the global poster_backend is unset) — it shows the provider actually publishing.
    cfg = _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("ZERNIO_API_KEY", "sk")
    _seed(cfg, [{"handle": "@tk", "account_id": "a", "platforms": ["tiktok"], "status": "active",
                 "backends": {"tiktok": "zernio"}}])
    st = views.golive_status(cfg)
    assert st.is_live is True and st.mode == "zernio"          # never the contradictory "LIVE (dryrun)"


def test_status_mode_dryrun_when_not_live(tmp_path, monkeypatch):
    cfg = _clean(monkeypatch, tmp_path)
    assert views.golive_status(cfg).mode == "dryrun"
