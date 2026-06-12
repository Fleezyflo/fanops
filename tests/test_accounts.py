import json
import pytest
from fanops.config import Config
from fanops.accounts import Accounts

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
