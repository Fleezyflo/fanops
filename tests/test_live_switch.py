"""M2 — the global LIVE switch as a read-abstraction. `cfg.is_live` is the operator's dryrun↔live state,
sourced from FANOPS_LIVE when set, else derived from the legacy FANOPS_POSTER (so the running Postiz
deployment keeps working with NO .env edit). `is_live_backend` (the live+creds gate) is redefined through
it. BEHAVIOR-IDENTICAL when FANOPS_LIVE is unset — only the read plumbing moves; M3 flips the write side
(go_live → the switch) together with per-channel providers."""
import pytest
from fanops.config import Config


@pytest.fixture(autouse=True)
def _isolate_switch_env(monkeypatch):
    # isolate from the repo .env (which carries FANOPS_POSTER/POSTIZ_API_KEY) so these tests are hermetic.
    for k in ("FANOPS_LIVE", "FANOPS_POSTER", "POSTIZ_API_KEY", "ZERNIO_API_KEY", "BLOTATO_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_is_live_from_fanops_live(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1"); assert Config(root=tmp_path).is_live is True
    monkeypatch.setenv("FANOPS_LIVE", "0"); assert Config(root=tmp_path).is_live is False


def test_is_live_back_compat_from_legacy_poster(tmp_path, monkeypatch):
    # FANOPS_LIVE unset -> derive from FANOPS_POSTER so the LIVE deployment keeps publishing untouched.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); assert Config(root=tmp_path).is_live is True
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); assert Config(root=tmp_path).is_live is True
    monkeypatch.setenv("FANOPS_POSTER", "dryrun"); assert Config(root=tmp_path).is_live is False


def test_is_live_unset_both_is_dryrun(tmp_path):
    assert Config(root=tmp_path).is_live is False                  # nothing set -> not live


def test_fanops_live_overrides_legacy_poster(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("FANOPS_LIVE", "0")
    assert Config(root=tmp_path).is_live is False                  # the explicit switch wins over the legacy derivation


def test_is_live_unknown_value_is_not_live(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "maybe"); assert Config(root=tmp_path).is_live is False   # never present unknown as live


def test_is_live_truthy_spellings(tmp_path, monkeypatch):
    for v in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("FANOPS_LIVE", v); assert Config(root=tmp_path).is_live is True, v


def test_is_live_backend_requires_live_and_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1"); monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    assert Config(root=tmp_path).is_live_backend is True
    monkeypatch.delenv("POSTIZ_API_KEY")
    assert Config(root=tmp_path).is_live_backend is False          # live but no creds -> not a live backend
    monkeypatch.setenv("POSTIZ_API_KEY", "pk"); monkeypatch.setenv("FANOPS_LIVE", "0")
    assert Config(root=tmp_path).is_live_backend is False          # creds but not live -> the switch wins


def test_is_live_backend_byte_identical_to_legacy_when_switch_unset(tmp_path, monkeypatch):
    # the un-migrated deployment: FANOPS_LIVE unset, behavior must match the old "creds for poster_backend".
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    assert Config(root=tmp_path).is_live_backend is True
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    assert Config(root=tmp_path).is_live_backend is False
