import json
import os
import fcntl
import threading
import pytest
from fanops.config import Config
from fanops.errors import LockBusyError
from fanops.accounts import Accounts, write_integration, add_account, set_status, remove_account

def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))

def test_load_and_active(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram", "tiktok"], "status": "active"},
        {"handle": "@b", "account_id": "", "platforms": ["instagram"], "status": "planned"},
    ])
    accts = Accounts.load(cfg)
    assert [a.handle for a in accts.active()] == ["@a"]

def test_no_secret_fields(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    dumped = Accounts.load(cfg).accounts[0].model_dump()
    assert not any(k in dumped for k in ("password", "token", "secret", "credential", "api_key"))

def test_resolve_account_id(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active"}])
    accts = Accounts.load(cfg)
    assert accts.resolve_account_id("@a") == "98432"
    with pytest.raises(KeyError):
        accts.resolve_account_id("@missing")

def test_active_account_requires_account_id(tmp_path):
    # An active account with no Blotato id is a config error surfaced early.
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    accts = Accounts.load(cfg)
    problems = accts.validate()
    assert any("account_id" in p for p in problems)

def test_surfaces_matrix_carries_id(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram", "tiktok"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["tiktok"], "status": "active"},
    ])
    accts = Accounts.load(cfg)
    pairs = {(s.account, s.account_id, s.platform.value) for s in accts.surfaces()}
    assert pairs == {("@a", "1", "instagram"), ("@a", "1", "tiktok"), ("@b", "2", "tiktok")}

def test_resolve_account_id_raises_on_empty_id(tmp_path):
    # A known handle with no Blotato id must fail loud, not return "".
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "planned"}])
    accts = Accounts.load(cfg)
    with pytest.raises(KeyError):
        accts.resolve_account_id("@a")

def test_validate_flags_missing_platforms(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": [], "status": "active"}])
    problems = Accounts.load(cfg).validate()
    assert any("platforms" in p for p in problems)

def test_validate_flags_duplicate_handles(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@a", "account_id": "2", "platforms": ["tiktok"], "status": "active"},
    ])
    problems = Accounts.load(cfg).validate()
    assert any("duplicate" in p for p in problems)

def test_surfaces_excludes_planned_accounts(tmp_path):
    # The load-bearing invariant: planned accounts NEVER produce surfaces (never post).
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@live", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"handle": "@soon", "account_id": "2", "platforms": ["instagram", "tiktok"], "status": "planned"},
    ])
    accts = Accounts.load(cfg)
    handles = {s.account for s in accts.surfaces()}
    assert handles == {"@live"}            # @soon (planned) excluded entirely

def test_load_missing_file_is_empty_registry(tmp_path):
    # No accounts.json -> empty registry, not a crash.
    cfg = Config(root=tmp_path)
    accts = Accounts.load(cfg)             # nothing seeded
    assert accts.accounts == [] and accts.active() == [] and accts.validate() == []


# ---- M1: per-platform integration model (the real go-live fix). A handle's Instagram and TikTok are
# DIFFERENT Postiz integrations, so each (handle, platform) must resolve to its OWN id. `integrations`
# is additive: a legacy single `account_id` stays the fallback so existing accounts.json just works.
from fanops.models import Platform

def test_surfaces_carry_per_platform_integration_id(tmp_path):
    # A 2-platform handle with per-platform integrations -> each surface carries its OWN id, not one shared id.
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "fallback", "platforms": ["instagram", "tiktok"],
                 "status": "active", "integrations": {"instagram": "ig_1", "tiktok": "tk_9"}}])
    pairs = {(s.account, s.account_id, s.platform.value) for s in Accounts.load(cfg).surfaces()}
    assert pairs == {("@a", "ig_1", "instagram"), ("@a", "tk_9", "tiktok")}

def test_surfaces_fall_back_to_account_id_when_platform_unmapped(tmp_path):
    # instagram has a per-platform id; tiktok has none -> tiktok falls back to the shared account_id.
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "shared", "platforms": ["instagram", "tiktok"],
                 "status": "active", "integrations": {"instagram": "ig_1"}}])
    pairs = {(s.account_id, s.platform.value) for s in Accounts.load(cfg).surfaces()}
    assert pairs == {("ig_1", "instagram"), ("shared", "tiktok")}

def test_resolve_account_id_per_platform_distinct(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "fallback", "platforms": ["instagram", "tiktok"],
                 "status": "active", "integrations": {"instagram": "ig_1", "tiktok": "tk_9"}}])
    accts = Accounts.load(cfg)
    assert accts.resolve_account_id("@a", Platform.instagram) == "ig_1"
    assert accts.resolve_account_id("@a", Platform.tiktok) == "tk_9"

def test_resolve_account_id_platform_falls_back_to_account_id(tmp_path):
    # An unmapped platform falls back to the shared account_id (back-compat path).
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "shared", "platforms": ["instagram", "youtube"],
                 "status": "active", "integrations": {"instagram": "ig_1"}}])
    accts = Accounts.load(cfg)
    assert accts.resolve_account_id("@a", Platform.youtube) == "shared"

def test_resolve_account_id_no_platform_uses_account_id(tmp_path):
    # Legacy call with no platform arg keeps returning the shared account_id (existing callers unchanged).
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active",
                 "integrations": {"instagram": "ig_1"}}])
    assert Accounts.load(cfg).resolve_account_id("@a") == "98432"

def test_resolve_account_id_platform_unmapped_no_fallback_raises(tmp_path):
    # No per-platform id AND no shared account_id -> fail loud (an empty id must never reach the poster).
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram", "tiktok"],
                 "status": "active", "integrations": {"instagram": "ig_1"}}])
    accts = Accounts.load(cfg)
    assert accts.resolve_account_id("@a", Platform.instagram) == "ig_1"
    with pytest.raises(KeyError):
        accts.resolve_account_id("@a", Platform.tiktok)

def test_validate_flags_per_platform_unmapped_channel(tmp_path):
    # instagram is mapped, tiktok is not and there's no shared account_id -> validate flags tiktok by name.
    # R2: also pair instagram with its backend so the new co-constraint rule passes; this test pins the
    # *missing id* problem, not the routing-drift problem.
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram", "tiktok"],
                 "status": "active", "integrations": {"instagram": "ig_1"},
                 "backends": {"instagram": "postiz"}}])
    problems = Accounts.load(cfg).validate()
    assert any("tiktok" in p for p in problems)
    # R2: the only instagram mention allowed is one that doesn't carry the missing-id phrasing.
    bad_ig = [p for p in problems if "instagram" in p and "no account_id" in p]
    assert not bad_ig, f"the mapped channel is NOT flagged for a missing id: {bad_ig}"

def test_validate_passes_fully_per_platform_mapped(tmp_path):
    # R2: a fully-mapped account pairs each integration with its backend (the canonical happy path).
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram", "tiktok"],
                 "status": "active", "integrations": {"instagram": "ig_1", "tiktok": "tk_9"},
                 "backends": {"instagram": "postiz", "tiktok": "postiz"}}])
    assert Accounts.load(cfg).validate() == []

def test_validate_legacy_single_account_id_still_passes(tmp_path):
    # BACK-COMPAT: a legacy account (shared account_id, NO integrations) validates via the fallback.
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "98432", "platforms": ["instagram", "tiktok"], "status": "active"}])
    assert Accounts.load(cfg).validate() == []


# ---- M2.1: writers backing the UI onboarding. write_integration maps ONE (handle, platform) channel;
# add_account onboards a brand-new account — both atomic raw-dict writes (siblings + unknown fields kept).
def test_write_integration_sets_nested_id_and_preserves_siblings(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "", "platforms": ["instagram", "tiktok"], "status": "active",
         "integrations": {"instagram": "ig_old"}, "note": "keep me"},
        {"handle": "@b", "account_id": "x", "platforms": ["tiktok"], "status": "active"},
    ])
    assert write_integration(cfg, "@a", "tiktok", "tk_42") == "@a"
    raw = json.loads(cfg.accounts_path.read_text())
    a = next(x for x in raw["accounts"] if x["handle"] == "@a")
    b = next(x for x in raw["accounts"] if x["handle"] == "@b")
    assert a["integrations"] == {"instagram": "ig_old", "tiktok": "tk_42"}   # added, existing kept
    assert a["note"] == "keep me"                                            # unknown field preserved
    assert b == {"handle": "@b", "account_id": "x", "platforms": ["tiktok"], "status": "active"}  # sibling untouched

def test_write_integration_creates_map_when_absent(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    write_integration(cfg, "@a", "instagram", 7)                            # numeric id coerces to str
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["integrations"] == {"instagram": "7"}

def test_write_integration_updates_every_duplicate_handle_row(tmp_path):
    # M3: handles SHOULD be unique (add_account rejects a dup), but a hand-edited accounts.json with a
    # duplicate handle must NOT leave the 2nd copy diverged. write_integration now scans ALL rows (no
    # break) — mirroring set_status and remove_account — so a (handle, platform) maps consistently
    # across every matching row instead of only the first.
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@dup", "account_id": "1", "platforms": ["instagram"], "status": "active", "integrations": {}},
        {"handle": "@dup", "account_id": "2", "platforms": ["instagram"], "status": "active", "integrations": {}}])
    write_integration(cfg, "@dup", "instagram", "ig_123")
    dups = [a for a in json.loads(cfg.accounts_path.read_text())["accounts"] if a["handle"] == "@dup"]
    assert len(dups) == 2
    assert all(a.get("integrations", {}).get("instagram") == "ig_123" for a in dups)   # BOTH updated, no divergence

def test_write_integration_reload_resolves_per_platform(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram", "tiktok"], "status": "active"}])
    write_integration(cfg, "@a", "instagram", "ig_1")
    write_integration(cfg, "@a", "tiktok", "tk_1")
    accts = Accounts.load(cfg)
    assert accts.resolve_account_id("@a", Platform.instagram) == "ig_1"
    assert accts.resolve_account_id("@a", Platform.tiktok) == "tk_1"

def test_write_integration_unknown_handle_raises(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    with pytest.raises(KeyError):
        write_integration(cfg, "@nope", "instagram", "x")

def test_write_integration_rejects_unknown_platform(tmp_path):
    # defense-in-depth at the control-file boundary: a typo'd/crafted platform must NOT be silently
    # written (it would never match a Platform.value and the channel would stay invisibly unmapped).
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active"}])
    with pytest.raises(ValueError):
        write_integration(cfg, "@a", "insagram", "x")   # typo of instagram
    raw = json.loads(cfg.accounts_path.read_text())
    assert "integrations" not in raw["accounts"][0] or raw["accounts"][0].get("integrations") == {}

def test_add_account_appends_with_defaults(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    assert add_account(cfg, "@b", ["instagram", "tiktok"], persona="raw studio") == "@b"
    raw = json.loads(cfg.accounts_path.read_text())
    b = next(x for x in raw["accounts"] if x["handle"] == "@b")
    assert b["status"] == "active" and b["access"] == "postiz"   # UI-added defaults
    assert b["account_id"] == "" and b["integrations"] == {}     # mapped afterward
    assert b["platforms"] == ["instagram", "tiktok"] and b["persona"] == "raw studio"
    assert len(raw["accounts"]) == 2                             # @a untouched

def test_add_account_to_absent_file_creates_it(tmp_path):
    cfg = Config(root=tmp_path)                                  # nothing seeded
    add_account(cfg, "@new", ["youtube"])
    accts = Accounts.load(cfg)
    assert [a.handle for a in accts.accounts] == ["@new"]
    assert accts.accounts[0].status.value == "active"

def test_add_account_rejects_duplicate_handle(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    with pytest.raises(ValueError):
        add_account(cfg, "@a", ["tiktok"])

def test_add_account_rejects_unknown_platform(tmp_path):
    # input validation at the control-file boundary: never write an account that won't reload.
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        add_account(cfg, "@a", ["instagram", "myspace"])

def test_add_account_requires_handle(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        add_account(cfg, "   ", ["instagram"])


# ---- finalization: complete the accounts CRUD (set_status + remove_account, atomic raw-dict) ----
def test_set_status_flips_and_preserves_siblings_and_unknown_fields(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "", "platforms": ["instagram"], "status": "active",
         "integrations": {"instagram": "ig_1"}, "note": "keep me"},
        {"handle": "@b", "account_id": "x", "platforms": ["tiktok"], "status": "active"},
    ])
    assert set_status(cfg, "@a", "planned") == "@a"
    raw = json.loads(cfg.accounts_path.read_text())
    a = next(x for x in raw["accounts"] if x["handle"] == "@a")
    b = next(x for x in raw["accounts"] if x["handle"] == "@b")
    assert a["status"] == "planned" and a["integrations"] == {"instagram": "ig_1"} and a["note"] == "keep me"
    assert b == {"handle": "@b", "account_id": "x", "platforms": ["tiktok"], "status": "active"}  # sibling untouched
    assert "@a" not in [x.handle for x in Accounts.load(cfg).active()]   # demoted -> no longer active

def test_set_status_rejects_unknown_status(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    with pytest.raises(ValueError):
        set_status(cfg, "@a", "deleted")                  # not an AccountStatus value -> never write an unloadable status

def test_set_status_unknown_handle_raises(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    with pytest.raises(KeyError):
        set_status(cfg, "@nope", "planned")

def test_set_status_demotes_ALL_duplicate_rows(tmp_path):
    # defense-in-depth (review): a hand-edited file with a duplicate handle must not leave a 2nd copy active.
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@dup", "account_id": "1", "platforms": ["instagram"], "status": "active"},
                {"handle": "@dup", "account_id": "2", "platforms": ["tiktok"], "status": "active"}])
    set_status(cfg, "@dup", "planned")
    assert [x["status"] for x in json.loads(cfg.accounts_path.read_text())["accounts"]] == ["planned", "planned"]

def test_remove_account_drops_only_target_preserves_siblings(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@TBD-1", "account_id": "dryrun", "platforms": ["instagram"], "status": "active"},
        {"handle": "@keep", "account_id": "x", "platforms": ["tiktok"], "status": "active", "note": "keep me"},
    ])
    assert remove_account(cfg, "@TBD-1") == "@TBD-1"
    raw = json.loads(cfg.accounts_path.read_text())
    handles = [x["handle"] for x in raw["accounts"]]
    assert handles == ["@keep"]                            # only the target dropped
    assert raw["accounts"][0]["note"] == "keep me"         # sibling + unknown field intact

def test_remove_last_account_leaves_valid_empty_registry(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [{"handle": "@only", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    remove_account(cfg, "@only")
    assert json.loads(cfg.accounts_path.read_text())["accounts"] == []   # empty but valid
    assert Accounts.load(cfg).active() == []                              # reloads clean

def test_remove_account_unknown_handle_raises(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg, [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    with pytest.raises(KeyError):
        remove_account(cfg, "@nope")


# ---- HIGH (audit Slice 4): the 5 mutators must serialize their read-modify-write under a file lock ----
def test_accounts_lock_path_in_config(tmp_path):
    cfg = Config(root=tmp_path)
    assert cfg.accounts_lock_path == cfg.control / "accounts.lock"

def test_mutator_serializes_on_the_accounts_lock(tmp_path, monkeypatch):
    # A live holder of the accounts lock must EXCLUDE a mutator (it waits the shortened timeout then
    # raises LockBusyError) — proves the read-modify-write runs under cfg.accounts_lock_path, so two
    # concurrent Studio/daemon writers can't lost-update. Mirrors test_ledger_lock's held-flock proof.
    cfg = Config(root=tmp_path)
    add_account(cfg, "@base", ["instagram"])                       # seed (itself must acquire+release the lock)
    monkeypatch.setattr("fanops.ledger._DEFAULT_LOCK_TIMEOUT", 0.3)   # don't wait the 30s default
    cfg.accounts_lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(cfg.accounts_lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)                                  # a live holder takes the lock
    try:
        with pytest.raises(LockBusyError):
            set_status(cfg, "@base", "planned")                    # must contend on the held lock
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)

def test_concurrent_add_account_no_lost_update(tmp_path):
    # The real lost-update: two threads each add a DIFFERENT account. Without a serialized read-modify-
    # write both load the same base, both append their own, and the last full-snapshot write wins —
    # silently dropping the other. Looped so the unlocked failure is reliable, not a lucky pass.
    cfg = Config(root=tmp_path)
    for it in range(8):
        a, b = f"@a{it}", f"@b{it}"
        start = threading.Barrier(2)
        def w(h):
            start.wait(); add_account(cfg, h, ["instagram"])
        ts = [threading.Thread(target=w, args=(a,)), threading.Thread(target=w, args=(b,))]
        for t in ts: t.start()
        for t in ts: t.join()
        handles = {x.handle for x in Accounts.load(cfg).accounts}
        assert a in handles and b in handles, f"iter {it}: lost update -> {sorted(handles)}"
