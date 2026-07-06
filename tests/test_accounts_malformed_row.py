"""MOL-79: Accounts.load must guard against ONE malformed accounts.json row failing the whole
registry. accounts.json is hand-edited by the operator ("paste account_id, set status:active"),
so a stray null or a trailing-comma artifact is the likely mistake — it must degrade to "one
account skipped", not "the whole pipeline and Studio down". Mirrors the sibling Personas.load
per-row leniency, but a skipped row must NOT vanish quietly: it surfaces at the doctor/health
level naming the bad row (a silently-dropped account the operator only discovers when posts stop
routing would be worse than the loud failure it replaces)."""
import json
import pytest
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.doctor import doctor_report


def _seed(cfg, accounts):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": accounts}))


def test_null_row_skipped_valid_rows_kept(tmp_path):
    # [valid, null, valid] — a stray null from a hand-edit typo must not lose the two valid rows.
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        None,
        {"handle": "@c", "account_id": "3", "platforms": ["tiktok"], "status": "active"},
    ])
    accts = Accounts.load(cfg)
    assert [a.handle for a in accts.accounts] == ["a", "c"]


def test_missing_required_field_row_skipped_valid_rows_kept(tmp_path):
    # [valid, dict-missing-required-`handle`, valid] — a dict that can't build an Account must be
    # skipped per-row, not raise ControlFileError for the whole registry.
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        {"account_id": "2", "platforms": ["instagram"], "status": "active"},  # no handle -> ValidationError
        {"handle": "@c", "account_id": "3", "platforms": ["tiktok"], "status": "active"},
    ])
    accts = Accounts.load(cfg)
    assert [a.handle for a in accts.accounts] == ["a", "c"]


def test_skipped_row_surfaced_in_doctor_naming_the_bad_row(tmp_path):
    # TIGHTENED ACCEPTANCE (review round): a skipped row must be VISIBLE at the doctor/health
    # surface — where accounts.json integrity is already reported — naming the bad row, NOT buried
    # as a debug log line. Otherwise a silently-dropped account trades a loud failure for a quiet one.
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"},
        None,  # bad row at index 1
        {"account_id": "2", "platforms": ["instagram"], "status": "active"},  # bad row at index 2
    ])
    report = doctor_report(cfg)
    # The accounts.json integrity check must FAIL, and its hint must name the skipped row(s).
    acct_check = next(c for c in report["checks"] if c["label"].startswith("accounts.json valid"))
    assert acct_check["ok"] is False
    assert "1" in acct_check["hint"] and "2" in acct_check["hint"]
    assert "skip" in acct_check["hint"].lower() or "malformed" in acct_check["hint"].lower()


def test_happy_path_all_valid_unchanged(tmp_path):
    # The all-valid happy path must be byte-identical: every row loads, doctor's accounts check
    # reports no skipped-row problem.
    cfg = Config(root=tmp_path)
    rows = [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram", "tiktok"], "status": "active"},
        {"handle": "@b", "account_id": "2", "platforms": ["tiktok"], "status": "active"},
    ]
    _seed(cfg, rows)
    accts = Accounts.load(cfg)
    assert [a.handle for a in accts.accounts] == ["a", "b"]
    report = doctor_report(cfg)
    acct_check = next(c for c in report["checks"] if c["label"].startswith("accounts.json valid"))
    # No malformed-row problem on an all-valid file (the check may still flag OTHER validate()
    # issues, but never a skipped/malformed-row note).
    assert "skip" not in acct_check["hint"].lower() and "malformed" not in acct_check["hint"].lower()


def test_io_error_still_raises(tmp_path):
    # Scope guard: a per-row-tolerant load must NOT swallow a genuinely-corrupt file (invalid JSON
    # is not a per-row problem) — that still fails loud so the operator sees it.
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text("{not valid json")
    with pytest.raises(Exception):
        Accounts.load(cfg)
