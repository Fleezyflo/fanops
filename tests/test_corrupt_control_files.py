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
from fanops.personas import Personas


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


def test_accounts_load_schema_violation_skips_row_and_surfaces_in_validate(tmp_path):
    # MOL-79 supersedes the old all-or-nothing raise: a malformed ROW inside a well-formed
    # {"accounts": [...]} envelope (here `platforms` is a string, tripping pydantic) is SKIPPED,
    # the well-formed rows still load, and the skip is surfaced via validate() (never silent).
    cfg = Config(root=tmp_path)
    _write(cfg.accounts_path, json.dumps({"accounts": [
        {"handle": "@bad", "platforms": "instagram"},                                          # row 0: platforms must be a list
        {"handle": "@ok", "account_id": "1", "platforms": ["instagram"], "status": "active"},  # row 1: well-formed
    ]}))
    accts = Accounts.load(cfg)                                  # must NOT raise
    assert [a.handle for a in accts.accounts] == ["@ok"]        # bad row skipped, good row loaded
    problems = accts.validate()
    assert any("row 0" in p and "malformed, skipped" in p for p in problems)  # surfaced, names the row


def test_accounts_load_wrong_toplevel_shape_raises_control_file_error(tmp_path):
    cfg = Config(root=tmp_path)
    # Valid JSON, but a bare list instead of {"accounts": [...]} — raw.get() would AttributeError.
    _write(cfg.accounts_path, json.dumps([{"handle": "@a"}]))
    with pytest.raises(ControlFileError) as ei:
        Accounts.load(cfg)
    assert "accounts.json invalid:" in str(ei.value)


def test_personas_load_corrupt_json_raises_control_file_error(tmp_path):
    # MOL-12 pin: Personas.load raises loudly on a corrupt personas.json — the hashtag-store refresh relies
    # on this raise reaching it (not being swallowed to []) so a broken control file can't clobber the store.
    cfg = Config(root=tmp_path)
    _write(cfg.personas_path, '{"personas": [oops]}')      # bareword: not valid JSON
    with pytest.raises(ControlFileError) as ei:
        Personas.load(cfg)
    msg = str(ei.value)
    assert "personas.json invalid:" in msg                 # names the file + the word "invalid"
    assert "Traceback" not in msg                          # a reason, not a stack trace


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
