"""Guard for the deprecated hook machinery removed by the hook-author-vision overhaul:
  Phase 2 — the editor+critic cascade fields (hook_edited / hook_judged / hook_rounds / hook_feedback)
            + modules (hookedit / hookjudge) + schemas (HookEdit*/HookJudge*).
  Phase 3 — the 6-label hook_pattern taxonomy: the Moment/MomentPick/Post field, hookcheck's
            HOOK_PATTERNS / normalize_hook_pattern, and the "hook_pattern" creative-variation axis.
A ledger payload that still carries these keys (the LIVE ledger's 51 moments do — R1) loads clean
because Pydantic ignores extra input keys (no model_config -> default extra='ignore')."""
import importlib
import pytest
from fanops.models import Moment, MomentState

_LEGACY_MOMENT = {                                  # shape a real (pre-deletion) ledger moment carries
    "id": "moment_abc", "parent_id": "src_1", "state": MomentState.decided.value,
    "content_token": "0.00-3.00", "start": 0.0, "end": 3.0, "reason": "r",
    "transcript_excerpt": "x", "hook": "wait for the drop", "signal_score": 0.5,
    "hook_edited": True, "hook_judged": True, "hook_rounds": 2, "hook_feedback": "too generic",
}

def test_legacy_cascade_keys_load_clean_and_fields_are_gone():
    m = Moment.model_validate(_LEGACY_MOMENT)       # R1: old ledger keys must not raise (extra='ignore')
    assert m.id == "moment_abc" and m.hook == "wait for the drop"
    for dead in ("hook_edited", "hook_judged", "hook_rounds", "hook_feedback"):
        assert not hasattr(m, dead), f"{dead} must be deleted from Moment (cascade removed)"

def test_editor_and_critic_modules_are_deleted():
    for gone in ("fanops.hookedit", "fanops.hookjudge"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(gone)

def test_editor_and_critic_models_are_deleted():
    import fanops.models as models
    for sym in ("HookEditItem", "HookEditDecision", "HookJudgeItem", "HookJudgeDecision"):
        assert not hasattr(models, sym), f"{sym} must be deleted from models"

# ---- Phase 3: the 6-label hook_pattern taxonomy is fully removed ----

def test_legacy_hook_pattern_key_loads_clean_and_field_is_gone():
    m = Moment.model_validate({**_LEGACY_MOMENT, "hook_pattern": "open_loop"})   # R1: live ledger carries it
    assert m.hook == "wait for the drop"
    assert not hasattr(m, "hook_pattern"), "hook_pattern must be deleted from Moment"

def test_hook_pattern_field_removed_from_all_models():
    from fanops.models import Moment, MomentPick, Post
    for cls in (Moment, MomentPick, Post):
        assert "hook_pattern" not in cls.model_fields, f"{cls.__name__}.hook_pattern must be deleted"

def test_hook_pattern_taxonomy_removed_from_hookcheck():
    import fanops.hookcheck as hc
    for sym in ("HOOK_PATTERNS", "_PATTERN_ALIASES", "normalize_hook_pattern"):
        assert not hasattr(hc, sym), f"hookcheck.{sym} must be deleted"

def test_hook_pattern_removed_from_variation_axes():
    from fanops.caption import VARIATION_AXES
    assert "hook_pattern" not in VARIATION_AXES                                  # the taxonomy's A/B-axis footprint
    assert set(VARIATION_AXES) == {"hook_string", "caption_angle", "hook_placement"}
