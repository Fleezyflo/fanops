# MOL-164: Account.handle canonical at the write boundary — one root guarantee retires ~15 downstream patches.
import json
import subprocess
from pathlib import Path

import pytest

from fanops.config import Config
from fanops.accounts import Accounts, add_account, write_integration
from fanops.models import Platform, validate_account_handle


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def test_account_add_rejects_or_canonicalizes_bad_handle(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError, match="invalid handle"):
        add_account(cfg, "@Foo Bar/Baz", ["instagram"])
    assert add_account(cfg, "@Foo", ["instagram"]) == "foo"
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["handle"] == "foo"


def test_stored_handle_is_canonical(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@Legacy", "account_id": "1", "platforms": ["instagram"], "status": "active"}])
    for a in Accounts.load(cfg).accounts:
        assert a.handle == validate_account_handle(a.handle)


def test_downstream_normalize_calls_removed():
    root = Path(__file__).resolve().parents[1] / "src" / "fanops"
    out = subprocess.run(
        ["rg", "-c", "normalize_account_handle", str(root)],
        capture_output=True, text=True, check=True,
    )
    total = sum(int(line.split(":")[-1]) for line in out.stdout.strip().splitlines() if line)
    assert total <= 2, f"expected definition + at most one safety-net caller, got {total}:\n{out.stdout}"


def test_legacy_handle_migrates(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [{"handle": "@legacy", "account_id": "", "platforms": ["instagram"], "status": "active",
                 "integrations": {}}])
    write_integration(cfg, "@legacy", "instagram", "ig_legacy")
    accts = Accounts.load(cfg)
    a = next(x for x in accts.accounts if x.handle == "legacy")
    assert a.integrations.get("instagram") == "ig_legacy"
    assert accts.resolve_account_id("legacy", Platform.instagram) == "ig_legacy"
    raw = json.loads(cfg.accounts_path.read_text())
    assert raw["accounts"][0]["handle"] == "legacy"
