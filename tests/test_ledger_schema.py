# tests/test_ledger_schema.py
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.errors import ControlFileError
from fanops.models import Source, SourceState


def _seed(cfg):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.catalogued))

def _inject_legacy_raw(cfg, raw: dict) -> None:
    cfg.legacy_ledger_json_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.legacy_ledger_json_path.write_text(json.dumps(raw))
    if cfg.ledger_path.exists():
        cfg.ledger_path.unlink()

def test_save_stamps_schema_version(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert Ledger.load(cfg)._to_doc()["schema_version"] == SCHEMA_VERSION

def test_pre_versioning_ledger_still_loads(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    raw = Ledger.load(cfg)._to_doc(); raw.pop("schema_version", None)
    _inject_legacy_raw(cfg, raw)
    assert "s1" in Ledger.load(cfg).sources

def test_load_refuses_newer_schema_than_code(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    raw = Ledger.load(cfg)._to_doc(); raw["schema_version"] = SCHEMA_VERSION + 5
    _inject_legacy_raw(cfg, raw)
    with pytest.raises(ControlFileError, match="schema|upgrade"):
        Ledger.load(cfg)

def test_roundtrip_upgrades_unversioned_on_save(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    raw = Ledger.load(cfg)._to_doc(); raw.pop("schema_version", None)
    _inject_legacy_raw(cfg, raw)
    with Ledger.transaction(cfg):
        pass
    assert Ledger.load(cfg)._to_doc()["schema_version"] == SCHEMA_VERSION

def test_v1_ledger_migrates_to_current_with_empty_stitch_plans(tmp_path):
    assert SCHEMA_VERSION >= 2
    cfg = Config(root=tmp_path); _seed(cfg)
    raw = Ledger.load(cfg)._to_doc()
    raw["schema_version"] = 1; raw.pop("stitch_plans", None)
    _inject_legacy_raw(cfg, raw)
    led = Ledger.load(cfg)
    assert led.stitch_plans == {} and "s1" in led.sources

def test_module_docstring_names_every_persisted_map(tmp_path):
    import fanops.ledger as ledger_mod
    cfg = Config(root=tmp_path); _seed(cfg)
    doc = Ledger.load(cfg)._to_doc()
    persisted = {k for k in doc if k != "schema_version"}
    docstring = ledger_mod.__doc__ or ""
    missing = sorted(k for k in persisted if k not in docstring)
    assert not missing, f"module docstring omits persisted map(s): {missing}"

def test_stitch_plan_round_trips(tmp_path):
    from fanops.models import StitchPlan, StitchState
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.stitch_plans["sp1"] = StitchPlan(id="sp1", clip_id="clip_1", strategy_key="impact_cut")
    led = Ledger.load(cfg)
    assert "sp1" in led.stitch_plans and led.stitch_plans["sp1"].clip_id == "clip_1"
    assert led.stitch_plans["sp1"].state is StitchState.suggested
