# tests/test_quarantine_immutable.py — WS6 (audit x-f1) + MOL-779: _quarantine stamps via Ledger.set_*_state
# (model_copy inside the owner), so a future frozen Source/Moment/Clip cannot raise INSIDE the except
# handler and wedge the whole pass — the exact failure F03 added _quarantine to prevent.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState, Moment, MomentState
import fanops.pipeline as pipeline


def test_quarantine_survives_via_owner_model_copy(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="e1", state=SourceState.catalogued, source_path="/tmp/x.mp4"))
    logs = []
    # MUST NOT raise — one bad unit is skipped, never wedges the pass.
    pipeline._quarantine(led, "e1", SourceState.error, "source", ValueError("boom"),
                         lambda *a, **k: logs.append((a, k)))
    assert led.sources["e1"].state is SourceState.error
    assert led.sources["e1"].error_reason and "boom" in led.sources["e1"].error_reason
    assert logs and logs[0][0][:3] == ("source", "e1", "error")


def test_quarantine_replaces_not_mutates_the_collection_entry(tmp_path):
    """The stamp must land in the ledger (so it persists), and the original object stays untouched
    (immutability — no hidden in-place side effect on a reference the stage loop may still hold)."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_moment(Moment(id="e1", parent_id="s1", state=MomentState.picked, start=0.0, end=1.0, reason="r"))
    original = led.moments["e1"]
    pipeline._quarantine(led, "e1", MomentState.error, "moments", RuntimeError("nope"),
                         lambda *a, **k: None)
    assert led.moments["e1"] is not original          # a NEW object replaced the entry
    assert original.state is MomentState.picked       # the original is unmutated
    assert led.moments["e1"].state is MomentState.error
