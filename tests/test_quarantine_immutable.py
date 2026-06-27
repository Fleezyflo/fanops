# tests/test_quarantine_immutable.py — WS6 (audit x-f1): _quarantine stamps the error state/reason by MUTATING
# the model in place (obj.state = ...; obj.error_reason = ...). That works only because Source/Moment/Clip are
# currently un-frozen pydantic models. The moment any of them gains `frozen=True` (a natural hardening — these
# are ledger records that nothing should mutate after construction), the per-unit quarantine path raises inside
# the except handler and WEDGES THE WHOLE PASS — the exact failure F03 added _quarantine to prevent. The fix
# stamps via an immutable model_copy(update=...) setter so quarantine survives a future frozen model.
from pydantic import BaseModel, ConfigDict
import fanops.pipeline as pipeline


class _FrozenRecord(BaseModel):
    model_config = ConfigDict(frozen=True)        # the future-hardened ledger record
    state: str = "ok"
    error_reason: str | None = None


def test_quarantine_survives_a_frozen_model():
    coll = {"e1": _FrozenRecord()}
    logs = []
    # MUST NOT raise even though the record is frozen — one bad unit is skipped, never wedges the pass.
    pipeline._quarantine(coll, "e1", "error", "source", ValueError("boom"), lambda *a, **k: logs.append((a, k)))
    assert coll["e1"].state == "error"
    assert coll["e1"].error_reason and "boom" in coll["e1"].error_reason
    assert logs and logs[0][0][:3] == ("source", "e1", "error")


def test_quarantine_replaces_not_mutates_the_collection_entry():
    """The stamp must land in the collection (so the ledger persists it), and the original object stays
    untouched (immutability — no hidden in-place side effect on a reference the stage loop may still hold)."""
    original = _FrozenRecord()
    coll = {"e1": original}
    pipeline._quarantine(coll, "e1", "error", "moments", RuntimeError("nope"), lambda *a, **k: None)
    assert coll["e1"] is not original          # a NEW object replaced the entry
    assert original.state == "ok"              # the original is unmutated
    assert coll["e1"].state == "error"
