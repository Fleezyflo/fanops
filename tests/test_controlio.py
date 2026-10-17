# tests/test_controlio.py — MOL-295: fail-LOUD load_validated for control files + atomic write helpers
import json
import stat

import pytest
from pydantic import BaseModel, Field

from fanops.controlio import load_validated, write_bytes_atomic, write_json_atomic, write_text_atomic
from fanops.errors import ControlFileError


class _TuningOverrides(BaseModel):
    offbrand_en: list[str] = Field(default_factory=list)
    offbrand_ar: list[str] = Field(default_factory=list)
    lift_weights: dict[str, float] = Field(default_factory=dict)


def test_load_validated_missing_file_raises(tmp_path):
    p = tmp_path / "tuning.json"
    with pytest.raises(ControlFileError, match="missing"):
        load_validated(p, _TuningOverrides)


def test_load_validated_bad_json_raises(tmp_path):
    p = tmp_path / "tuning.json"
    p.write_text("{not json")
    with pytest.raises(ControlFileError, match="JSON parse"):
        load_validated(p, _TuningOverrides)


def test_load_validated_schema_violation_raises(tmp_path):
    p = tmp_path / "tuning.json"
    p.write_text(json.dumps({"offbrand_en": "must-be-list"}))
    with pytest.raises(ControlFileError, match="invalid"):
        load_validated(p, _TuningOverrides)


def test_load_validated_accepts_valid_file(tmp_path):
    p = tmp_path / "tuning.json"
    p.write_text(json.dumps({"offbrand_en": ["\\bpls\\b"], "lift_weights": {"saves": 4.0}}))
    m = load_validated(p, _TuningOverrides)
    assert m.offbrand_en == ["\\bpls\\b"] and m.lift_weights["saves"] == 4.0


def test_write_json_atomic_writes_dict_and_list(tmp_path):
    p = tmp_path / "manifest.json"
    write_json_atomic(p, {"x": 1})
    assert json.loads(p.read_text()) == {"x": 1}
    write_json_atomic(tmp_path / "intaken.json", ["a", "b"])
    assert json.loads((tmp_path / "intaken.json").read_text()) == ["a", "b"]


def test_write_json_atomic_creates_parent_dir(tmp_path):
    target = tmp_path / "sub" / "manifest.json"
    write_json_atomic(target, {"x": 1})
    assert json.loads(target.read_text()) == {"x": 1}


def test_write_text_atomic_writes_content_and_mode(tmp_path):
    p = tmp_path / "note.txt"
    write_text_atomic(p, "hello\n", mode=0o640)
    assert p.read_text() == "hello\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o640


def test_write_bytes_atomic_writes_content_and_mode(tmp_path):
    p = tmp_path / "blob.bin"
    write_bytes_atomic(p, b"\x00\xff", mode=0o600)
    assert p.read_bytes() == b"\x00\xff"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600



