# tests/test_ledger_schema.py — Phase 4a: ledger schema_version + migration scaffolding. The real
# robustness win is refusing a NEWER-than-code ledger (pydantic extra="ignore" would otherwise drop
# its future fields on the next save — silent data loss).
import json
import pytest
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.errors import ControlFileError
from fanops.models import Source, SourceState


def _seed(cfg):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.catalogued))


def test_save_stamps_schema_version(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION

def test_pre_versioning_ledger_still_loads(tmp_path):
    # A ledger written before schema_version existed (no key, = v0) must load unchanged (v0->v1 no-op).
    cfg = Config(root=tmp_path); _seed(cfg)
    raw = json.loads(cfg.ledger_path.read_text()); raw.pop("schema_version", None)
    cfg.ledger_path.write_text(json.dumps(raw))
    assert "s1" in Ledger.load(cfg).sources

def test_load_refuses_newer_schema_than_code(tmp_path):
    # A ledger from a FUTURE fanops must NOT be loaded-then-saved: extra="ignore" would silently DROP
    # the future fields on the next save (data loss). Refuse loudly with a typed, one-line error.
    cfg = Config(root=tmp_path); _seed(cfg)
    raw = json.loads(cfg.ledger_path.read_text()); raw["schema_version"] = SCHEMA_VERSION + 5
    cfg.ledger_path.write_text(json.dumps(raw))
    with pytest.raises(ControlFileError, match="schema|upgrade"):
        Ledger.load(cfg)

def test_roundtrip_upgrades_unversioned_on_save(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    raw = json.loads(cfg.ledger_path.read_text()); raw.pop("schema_version", None)
    cfg.ledger_path.write_text(json.dumps(raw))
    with Ledger.transaction(cfg):                 # load (v0) -> save (stamps current)
        pass
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION
