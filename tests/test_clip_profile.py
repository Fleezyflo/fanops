# tests/test_clip_profile.py — per-account clip_profile (Account.clip_profile +
# Config.resolve_clip_profile + the set_clip_profile mutator). A review/P4 label;
# render does not apply band lo/hi (the pick is the cut). Falls back to FANOPS_CLIP_PROFILE when unset.
import json
import pytest
from fanops.config import Config
from fanops.accounts import Accounts, set_clip_profile, add_account
from fanops.bands import PROFILE_NAMES
from fanops.errors import ControlFileError


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def _acct(handle, **extra):
    return {"handle": handle, "account_id": "1", "platforms": ["instagram"], "status": "active", **extra}


def test_profile_names_exported():
    # the validatable set of length/content profiles bands.band_for knows
    assert PROFILE_NAMES == {"talk", "song", "short", "medium", "long"}

def test_account_clip_profile_defaults_none(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("a")])
    assert Accounts.load(cfg).accounts[0].clip_profile is None     # absent field -> None (additive, no migration)

def test_account_clip_profile_persists_when_set(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("a", clip_profile="short")])
    assert Accounts.load(cfg).accounts[0].clip_profile == "short"

def test_load_unknown_clip_profile_does_not_crash(tmp_path):
    # Refuse the junk, or doctor/validate names it. Silent persist of an unknown profile is the hole
    # (THEATRE-FIX-F, expected RED while 'weird' reloads inert and validate() is silent).
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("a", clip_profile="weird")])
    try:
        loaded = Accounts.load(cfg)
    except (ValueError, ControlFileError):
        return
    kept = [a for a in loaded.accounts if (a.clip_profile or "").strip().lower() == "weird"]
    if not kept:
        return
    problems = " ".join(loaded.validate()).lower()
    assert "weird" in problems or "clip_profile" in problems


def test_resolve_clip_profile_uses_account_override(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("a", clip_profile="long")])
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_clip_profile(a) == "long"                   # account's own length wins

def test_resolve_clip_profile_falls_back_to_global(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("a")])                                      # no per-account override
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_clip_profile(a) == "talk"                   # default global
    monkeypatch.setenv("FANOPS_CLIP_PROFILE", "song")
    assert Config(root=tmp_path).resolve_clip_profile(a) == "song"  # global override flows through

def test_resolve_clip_profile_none_account(tmp_path):
    cfg = Config(root=tmp_path)
    assert cfg.resolve_clip_profile(None) == "talk"                # no account -> the global profile

def test_resolve_clip_profile_blank_override_falls_back(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("a", clip_profile="   ")])                  # whitespace-only override is no override
    a = Accounts.load(cfg).accounts[0]
    assert cfg.resolve_clip_profile(a) == "talk"

def test_resolved_profiles_select_distinct_bands(tmp_path):
    # Review label only: clip_profile is a P4/review dim. Render does not apply band lo/hi
    # (fit_window ignores them; the pick is the cut), so this is not an M2-length claim.
    cfg = Config(root=tmp_path)
    _seed(cfg, [_acct("short", clip_profile="short"), _acct("long", clip_profile="long")])
    accts = Accounts.load(cfg).accounts
    labels = [cfg.resolve_clip_profile(a) for a in accts]
    assert labels == ["short", "long"]


def test_set_clip_profile_sets_and_clears_preserving_siblings(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        _acct("a", note="keep me"),
        {"handle": "@b", "account_id": "x", "platforms": ["tiktok"], "status": "active"},
    ])
    assert set_clip_profile(cfg, "@a", "medium") == "a"
    a = next(x for x in json.loads(cfg.accounts_path.read_text())["accounts"] if x["handle"] == "a")
    assert a["clip_profile"] == "medium" and a["note"] == "keep me"  # set + sibling/unknown field intact
    set_clip_profile(cfg, "@a", "")                                 # blank clears
    a = next(x for x in json.loads(cfg.accounts_path.read_text())["accounts"] if x["handle"] == "a")
    assert a["clip_profile"] is None
    b = next(x for x in json.loads(cfg.accounts_path.read_text())["accounts"] if x["handle"] == "b")
    assert b["account_id"] == "x"                                   # sibling untouched throughout

def test_set_clip_profile_rejects_unknown(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a")])
    with pytest.raises(ValueError):
        set_clip_profile(cfg, "@a", "epic")                        # not a known profile -> never written

def test_set_clip_profile_unknown_handle_raises(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a")])
    with pytest.raises(KeyError):
        set_clip_profile(cfg, "@nope", "short")

def test_set_clip_profile_round_trips_through_load(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [_acct("a")])
    set_clip_profile(cfg, "@a", "long")
    assert cfg.resolve_clip_profile(Accounts.load(cfg).accounts[0]) == "long"

def test_add_account_with_clip_profile_persists(tmp_path):
    cfg = Config(root=tmp_path)
    assert add_account(cfg, "@a", ["instagram"], clip_profile="short") == "a"
    assert Accounts.load(cfg).accounts[0].clip_profile == "short"

def test_add_account_rejects_unknown_clip_profile(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        add_account(cfg, "@a", ["instagram"], clip_profile="epic")
