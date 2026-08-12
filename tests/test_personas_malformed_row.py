"""Foundation-honesty wave6: Personas.load must match Accounts.load per-row leniency.

personas.json is hand-edited; ONE stray null / missing-id row must degrade to "that row
skipped" (recorded in skipped_rows, logged, surfaced via validate() -> doctor), not crash
the whole registry — and must NOT silently continue non-dict rows the way the pre-wave6
loader did. Corrupt JSON / wrong top-level shape still fail loud (ControlFileError)."""
import json
import logging
import pytest
from fanops.config import Config
from fanops.errors import ControlFileError
from fanops.personas import Personas
from fanops.doctor import doctor_report


def _seed(cfg, personas):
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text(json.dumps({"personas": personas}))


def test_null_row_skipped_valid_rows_kept(tmp_path):
    # [valid, null, valid] — a stray null must not lose the two valid rows (was silent continue).
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"id": "a", "name": "A", "voice": "va", "niche": ["hiphop"]},
        None,
        {"id": "c", "name": "C", "voice": "vc", "niche": ["hiphop"]},
    ])
    reg = Personas.load(cfg)
    assert [p.id for p in reg.personas] == ["a", "c"]
    assert any("row 1" in s for s in reg.skipped_rows)


def test_missing_required_field_row_skipped_valid_rows_kept(tmp_path):
    # [valid, dict-missing-required-`id`, valid] — ValidationError is per-row, not whole-file.
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"id": "a", "name": "A", "voice": "va", "niche": ["hiphop"]},
        {"name": "no-id", "voice": "vx", "niche": ["hiphop"]},
        {"id": "c", "name": "C", "voice": "vc", "niche": ["hiphop"]},
    ])
    reg = Personas.load(cfg)
    assert [p.id for p in reg.personas] == ["a", "c"]
    assert any("row 1" in s for s in reg.skipped_rows)


def test_skipped_row_logged_and_surfaced_in_validate_and_doctor(tmp_path, caplog):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"id": "a", "name": "A", "voice": "va", "niche": ["hiphop"]},
        None,  # bad row at index 1
        {"name": "no-id", "voice": "vx", "niche": ["hiphop"]},  # bad row at index 2
    ])
    with caplog.at_level(logging.WARNING):
        reg = Personas.load(cfg)
    problems = reg.validate()
    assert any("row 1" in p and "malformed, skipped" in p for p in problems)
    assert any("row 2" in p and "malformed, skipped" in p for p in problems)
    assert any("personas.json" in r.getMessage() and "skipped" in r.getMessage() for r in caplog.records)

    report = doctor_report(cfg)
    persona_check = next(c for c in report["checks"] if c["label"].startswith("personas.json valid"))
    assert persona_check["ok"] is False
    assert "1" in persona_check["hint"] and "2" in persona_check["hint"]
    assert "skip" in persona_check["hint"].lower() or "malformed" in persona_check["hint"].lower()


def test_happy_path_all_valid_unchanged(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, [
        {"id": "a", "name": "A", "voice": "va", "niche": ["hiphop"]},
        {"id": "b", "name": "B", "voice": "vb", "niche": ["hiphop"]},
    ])
    reg = Personas.load(cfg)
    assert [p.id for p in reg.personas] == ["a", "b"]
    assert reg.skipped_rows == []
    assert reg.validate() == []
    report = doctor_report(cfg)
    persona_check = next(c for c in report["checks"] if c["label"].startswith("personas.json valid"))
    assert persona_check["ok"] is True
    assert "skip" not in persona_check["hint"].lower() and "malformed" not in persona_check["hint"].lower()


def test_io_error_still_raises(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text("{not valid json")
    with pytest.raises(ControlFileError):
        Personas.load(cfg)


def test_wrong_toplevel_shape_raises(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.personas_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.personas_path.write_text(json.dumps([{"id": "a"}]))
    with pytest.raises(ControlFileError) as ei:
        Personas.load(cfg)
    assert "personas.json invalid:" in str(ei.value)
