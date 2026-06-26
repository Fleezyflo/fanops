"""Dead-knob removal: clip_count (unenforced hint), energy=medium (no-op), and persona intake (never
reached output) are GONE — not relabelled, not capped. The fields no longer exist on the model or the
write boundary, and energy rejects 'medium'. These knobs did nothing to published output, so removal is
behavior-inert (the full suite stays green)."""
import inspect
import pytest
from fanops.config import Config
from fanops.personas import Persona, ENERGY_LEVELS
from fanops.persona_store import add_persona, update_persona


def test_clip_count_field_and_params_removed():
    assert "clip_count" not in Persona.model_fields
    assert "clip_count" not in inspect.signature(add_persona).parameters
    assert "clip_count" not in inspect.signature(update_persona).parameters


def test_intake_field_and_params_removed():
    assert "intake" not in Persona.model_fields
    assert "intake" not in inspect.signature(add_persona).parameters
    assert "intake" not in inspect.signature(update_persona).parameters


def test_energy_medium_value_removed():
    assert ENERGY_LEVELS == frozenset({"low", "high"})


def test_energy_medium_rejected_at_write_boundary(tmp_path):
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError):
        add_persona(cfg, "X", energy="medium")
