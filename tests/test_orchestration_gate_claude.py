"""Tests for the Claude Code adapter of the orchestration gate (.claude/hooks/orchestration_gate_claude.py).

The decision brain is .cursor/hooks/orchestration_gate.py (tested in test_orchestration_gate.py);
these tests pin the ADAPTER contract: Claude hook payloads in, permissionDecision JSON out, plus the
Claude-only write-protection that keys on the caller's agent_type.
"""
import json, os, subprocess, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ADAPTER = _ROOT / ".claude" / "hooks" / "orchestration_gate_claude.py"


def _run(payload, root, active=True):
    marker = Path(root) / ".orchestration" / "state" / "ACTIVE"
    if active:
        marker.parent.mkdir(parents=True, exist_ok=True); marker.write_text("")
    elif marker.exists():
        marker.unlink()
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    env.pop("FANOPS_ORCHESTRATED", None)
    out = subprocess.run([sys.executable, str(_ADAPTER)], input=json.dumps(payload),
                         capture_output=True, text=True, env=env, timeout=30)
    decision = {}
    if out.stdout.strip():
        decision = json.loads(out.stdout.strip()).get("hookSpecificOutput", {})
    return out.returncode, decision


def _bash(cmd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}}


def _spawn(stype, **extra):
    ti = {"subagent_type": stype, "prompt": "impl MOL-190", **extra}
    return {"hook_event_name": "PreToolUse", "tool_name": "Task", "tool_input": ti, "session_id": "s1"}


def _write(path, agent_type=None):
    p = {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": path}}
    if agent_type: p["agent_type"] = agent_type
    return p


def test_inert_when_no_wave(tmp_path):
    rc, d = _run(_bash("git reset --hard origin/main"), tmp_path, active=False)
    assert rc == 0 and not d
    rc, d = _run(_write(str(tmp_path / ".cursor/hooks/orchestration_gate.py")), tmp_path, active=False)
    assert rc == 0 and not d


def test_bash_destructive_and_stop_denied_while_active(tmp_path):
    rc, d = _run(_bash("git reset --hard origin/main"), tmp_path)
    assert d.get("permissionDecision") == "deny"
    rc, d = _run(_bash("python scripts/orchestrate.py stop"), tmp_path)
    assert d.get("permissionDecision") == "deny"
    rc, d = _run(_bash("python scripts/orchestrate.py status"), tmp_path)
    assert rc == 0 and not d


def test_bash_land_fails_closed_in_non_git_root(tmp_path):
    rc, d = _run(_bash("gh pr merge 12 --merge"), tmp_path)
    assert d.get("permissionDecision") == "deny"
    assert "enforcement machinery" in d.get("permissionDecisionReason", "")


def test_spawn_allowlist_and_ledger(tmp_path):
    rc, d = _run(_spawn("general-purpose"), tmp_path)
    assert d.get("permissionDecision") == "deny"
    rc, d = _run(_spawn("fanops-worker", model="auto"), tmp_path)
    assert rc == 0 and not d
    entries = [json.loads(ln) for ln in
               (Path(tmp_path) / ".orchestration/state/ledger.jsonl").read_text().splitlines()]
    assert any(e["event"] == "subagent_denied" and e["subagent_type"] == "general-purpose" for e in entries)
    assert any(e["event"] == "subagent_start" and e["subagent_type"] == "fanops-worker"
               and e.get("subagent_model") == "auto" for e in entries)


def test_orchestrator_spawn_denied_even_without_wave(tmp_path):
    rc, d = _run(_spawn("fanops-orchestrator"), tmp_path, active=False)
    assert d.get("permissionDecision") == "deny"
    assert "become the orchestrator" in d.get("permissionDecisionReason", "")
    rc, d = _run(_spawn("Explore"), tmp_path, active=False)
    assert rc == 0 and not d


def test_record_writes_only_by_fanops_worker(tmp_path):
    rec = str(tmp_path / ".orchestration/state/verified/MOL-190.json")
    rc, d = _run(_write(rec, agent_type="fanops-worker"), tmp_path)
    assert rc == 0 and not d
    rc, d = _run(_write(rec), tmp_path)  # main agent (the orchestrator)
    assert d.get("permissionDecision") == "deny" and "verifier" in d.get("permissionDecisionReason", "")
    rc, d = _run(_write(rec, agent_type="general-purpose"), tmp_path)
    assert d.get("permissionDecision") == "deny"


def test_enforcement_and_state_writes_denied_for_everyone(tmp_path):
    for rel in (".cursor/hooks/orchestration_gate.py", "scripts/orchestrate.py",
                ".orchestration/state/ledger.jsonl", ".githooks/pre-push",
                ".claude/settings.json", ".claude/hooks/orchestration_gate_claude.py"):
        rc, d = _run(_write(str(tmp_path / rel), agent_type="fanops-worker"), tmp_path)
        assert d.get("permissionDecision") == "deny", rel
    rc, d = _run(_write(str(tmp_path / "src/fanops/models.py"), agent_type="fanops-worker"), tmp_path)
    assert rc == 0 and not d


def test_subagent_stop_ledgered(tmp_path):
    payload = {"hook_event_name": "SubagentStop", "agent_type": "fanops-worker",
               "agent_id": "a1", "session_id": "s1"}
    rc, d = _run(payload, tmp_path)
    assert rc == 0
    entries = [json.loads(ln) for ln in
               (Path(tmp_path) / ".orchestration/state/ledger.jsonl").read_text().splitlines()]
    assert any(e["event"] == "subagent_stop" and e["subagent_type"] == "fanops-worker" for e in entries)
