"""Tests for the Darwin test gate (.claude/hooks/darwin_test_gate.py).

The guard is MACHINE-scoped, not repo-scoped: suite runs are denied on the operator's Mac (Darwin
— stacked local suites crash the host) and allowed on Linux (GitHub CI, Claude cloud sandboxes),
where the suite is supposed to run. It replaced the repo-wide `permissions.deny` pytest entries.
"""
import importlib.util, io, json, sys
from pathlib import Path

_HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "darwin_test_gate.py"
_spec = importlib.util.spec_from_file_location("darwin_test_gate", _HOOK)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _deny(cmd, system="Darwin", env=None):
    return gate.should_block(cmd, system, env or {})


def test_blocks_suite_invocations_on_darwin():
    for cmd in ("pytest -q", "pytest tests/test_clip.py -k render",
                "python -m pytest -q", "python3 -m pytest -q -m 'not integration'",
                "python3.12 -m pytest", ".venv/bin/pytest -q",
                "FANOPS_REQUIRE_STUDIO=1 pytest -q",
                "./scripts/check-full.sh", "bash scripts/check-full.sh",
                "cd /tmp && pytest -q", "ruff check . ; pytest -q"):
        assert _deny(cmd) is not None, cmd


def test_inert_on_linux_where_the_suite_runs():
    for cmd in ("pytest -q", "python3 -m pytest -q", "./scripts/check-full.sh"):
        assert _deny(cmd, system="Linux") is None, cmd


def test_operator_override_on_darwin():
    assert _deny("pytest -q", env={"FANOPS_LOCAL_TESTS": "1"}) is None
    assert _deny("FANOPS_LOCAL_TESTS=1 pytest -q") is None


def test_mentions_are_not_invocations():
    for cmd in ("grep -rn pytest tests/", "git commit -m 'wire the pytest gate'",
                "cat scripts/check-full.sh", "gh run view 123 --log | grep pytest",
                "echo pytest", "pip install pytest-cov"):
        assert _deny(cmd) is None, cmd


def test_main_deny_json_shape(monkeypatch, capsys):
    monkeypatch.setattr(gate.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("FANOPS_LOCAL_TESTS", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "python3 -m pytest -q"}})))
    assert gate.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "operator" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_main_silent_on_allow_malformed_and_non_bash(monkeypatch, capsys):
    monkeypatch.setattr(gate.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "git status"}})))
    assert gate.main() == 0 and capsys.readouterr().out == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert gate.main() == 0 and capsys.readouterr().out == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "Write", "tool_input": {"file_path": "x"}})))
    assert gate.main() == 0 and capsys.readouterr().out == ""
