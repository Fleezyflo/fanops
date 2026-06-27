# tests/test_gate_observability.py — WS2 (audit x-f2 / xc-3): the awaiting dict carries FOUR gate kinds
# (moments, moment_hooks, moment_casting, captions — pipeline.AwaitingCounts) but the operator-facing
# surfaces hardcoded the original moments/captions pair, so a stuck moment_casting (or moment_hooks) gate
# was INVISIBLE: no LOUD `_gates_blocked_note` alert, no `fanops status` count, no run.log breadcrumb —
# even though the convergence check correctly kept spinning. The fix derives every surface from the awaiting
# dict / the single GATE_KINDS tuple, so a future 5th gate can never be silently omitted.
from fanops.config import Config
from fanops.cli import _gates_blocked_note, cmd_status
import fanops.pipeline as pipeline


def test_gate_kinds_is_the_single_source_of_all_four():
    assert pipeline.GATE_KINDS == ("moments", "moment_hooks", "moment_casting", "captions")


def test_blocked_note_flags_a_stuck_moment_casting_gate():
    note = _gates_blocked_note({"awaiting": {"moments": 0, "moment_hooks": 0, "moment_casting": 5, "captions": 0}})
    assert note is not None and "moment_casting=5" in note    # the exact silent-stall the finding describes


def test_blocked_note_flags_a_stuck_moment_hooks_gate():
    note = _gates_blocked_note({"awaiting": {"moments": 0, "moment_hooks": 3, "moment_casting": 0, "captions": 0}})
    assert note is not None and "moment_hooks=3" in note


def test_blocked_note_quiet_when_every_gate_clear():
    assert _gates_blocked_note({"awaiting": {"moments": 0, "moment_hooks": 0, "moment_casting": 0, "captions": 0}}) is None
    assert _gates_blocked_note(None) is None


def test_status_surfaces_awaiting_moment_casting(tmp_path, capsys):
    cfg = Config(root=tmp_path)
    cmd_status(cfg)
    out = capsys.readouterr().out
    assert "awaiting_moment_casting=" in out                  # the casting gate count is now on `fanops status`
    assert "awaiting_moment_hooks=" in out                    # (already present, pinned so it can't regress)
