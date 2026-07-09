# tests/test_corrupt_control_files.py
"""Hand-edit typos in ledger.sqlite / accounts.json must produce clear ControlFileError messages."""
import json
import pytest
from fanops.config import Config
from fanops.errors import ControlFileError
from fanops.ledger import Ledger
from fanops.accounts import Accounts
from fanops.personas import Personas


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _write_legacy_json(cfg, text):
    _write(cfg.legacy_ledger_json_path, text)
    if cfg.ledger_path.exists():
        cfg.ledger_path.unlink()


def test_ledger_load_corrupt_json_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_bytes(b"not a sqlite db")
    with pytest.raises(ControlFileError) as ei:
        Ledger.load(cfg)
    msg = str(ei.value)
    assert "ledger.sqlite invalid:" in msg
    assert "Traceback" not in msg


def test_accounts_load_corrupt_json_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.accounts_path, '{"accounts": [oops]}')
    with pytest.raises(ControlFileError) as ei:
        Accounts.load(cfg)
    assert "accounts.json invalid:" in str(ei.value)


def test_ledger_load_schema_violation_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    _write_legacy_json(cfg, json.dumps({"sources": {"src_1": {"source_path": "/x.mp4"}}}))
    with pytest.raises(ControlFileError) as ei:
        Ledger.load(cfg)
    assert "ledger.sqlite invalid:" in str(ei.value)


def test_accounts_load_schema_violation_skips_row_and_surfaces_in_validate(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.accounts_path, json.dumps({"accounts": [
        {"handle": "@bad", "platforms": "instagram"},
        {"handle": "@ok", "account_id": "1", "platforms": ["instagram"], "status": "active"},
    ]}))
    accts = Accounts.load(cfg)
    assert [a.handle for a in accts.accounts] == ["ok"]
    problems = accts.validate()
    assert any("row 0" in p and "malformed, skipped" in p for p in problems)


def test_accounts_load_wrong_toplevel_shape_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.accounts_path, json.dumps([{"handle": "@a"}]))
    with pytest.raises(ControlFileError) as ei:
        Accounts.load(cfg)
    assert "accounts.json invalid:" in str(ei.value)


def test_personas_load_corrupt_json_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.personas_path, '{"personas": [oops]}')
    with pytest.raises(ControlFileError) as ei:
        Personas.load(cfg)
    msg = str(ei.value)
    assert "personas.json invalid:" in msg
    assert "Traceback" not in msg


def test_ledger_load_valid_still_works(tmp_path):
    cfg = Config(root=tmp_path)
    _write_legacy_json(cfg, json.dumps({"sources": {}, "moments": {}, "clips": {}, "posts": {}}))
    led = Ledger.load(cfg)
    assert led.sources == {}


def test_accounts_load_valid_still_works(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.accounts_path, json.dumps(
        {"accounts": [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    accts = Accounts.load(cfg)
    assert [a.handle for a in accts.accounts] == ["a"]
