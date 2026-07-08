# tests/test_controlio.py — MOL-295: fail-LOUD load_validated for control files
import json
import pytest
from pydantic import BaseModel, Field

from fanops.controlio import load_validated
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
