"""Phase 2 (delete editor + critic cascade) guard: the Moment fields the cascade rode on
(hook_edited / hook_judged / hook_rounds / hook_feedback) are GONE, and a ledger payload that
still carries them (the LIVE ledger's 51 moments do — R1) loads clean because Pydantic ignores
extra input keys. Also asserts the editor/critic symbols themselves no longer import."""
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
