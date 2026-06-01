# tests/test_corrupt_control_files.py
"""A hand-edit typo in ledger.json / accounts.json (the documented operator step,
README 'paste the numeric account_id, set status:active') must produce a clear
one-line `<file> invalid: <reason>` instead of a raw JSONDecodeError/ValidationError
traceback. The loaders raise ControlFileError; the CLI turns it into a clean nonzero exit."""
import json
import pytest
from fanops.config import Config
from fanops.errors import ControlFileError
from fanops.ledger import Ledger
from fanops.accounts import Accounts


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ---- loader guards: malformed JSON ----

def test_ledger_load_corrupt_json_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.ledger_path, '{"sources": {,}}')           # trailing-comma typo: not valid JSON
    with pytest.raises(ControlFileError) as ei:
        Ledger.load(cfg)
    msg = str(ei.value)
    assert "ledger.json invalid:" in msg                  # names the file + the word "invalid"
    assert "Traceback" not in msg                          # a reason, not a stack trace


def test_accounts_load_corrupt_json_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.accounts_path, '{"accounts": [oops]}')      # bareword: not valid JSON
    with pytest.raises(ControlFileError) as ei:
        Accounts.load(cfg)
    assert "accounts.json invalid:" in str(ei.value)


# ---- loader guards: valid JSON, schema-violating content ----

def test_ledger_load_schema_violation_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    # Valid JSON, but a Source is missing its required `id` -> pydantic ValidationError
    _write(cfg.ledger_path, json.dumps({"sources": {"src_1": {"source_path": "/x.mp4"}}}))
    with pytest.raises(ControlFileError) as ei:
        Ledger.load(cfg)
    assert "ledger.json invalid:" in str(ei.value)


def test_accounts_load_schema_violation_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    # platforms must be a list; a string trips pydantic
    _write(cfg.accounts_path, json.dumps({"accounts": [{"handle": "@a", "platforms": "instagram"}]}))
    with pytest.raises(ControlFileError) as ei:
        Accounts.load(cfg)
    assert "accounts.json invalid:" in str(ei.value)


# ---- the happy path is unchanged ----

def test_ledger_load_valid_still_works(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.ledger_path, json.dumps({"sources": {}, "moments": {}, "clips": {}, "posts": {}}))
    led = Ledger.load(cfg)                                  # must not raise
    assert led.sources == {}


def test_accounts_load_valid_still_works(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg.accounts_path, json.dumps(
        {"accounts": [{"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    accts = Accounts.load(cfg)                              # must not raise
    assert [a.handle for a in accts.accounts] == ["@a"]
