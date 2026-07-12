#!/usr/bin/env python3
"""Claude Code adapter for the FanOps orchestration gate — same brain, second runtime.

All decision logic lives in `.cursor/hooks/orchestration_gate.py` (the single enforcement home).
This shim translates Claude Code hook payloads/decisions to/from that gate:

  PreToolUse(Bash)                     -> gate handle_before_shell   (land-gate, protected paths,
                                          destructive git, operator-only stop)
  PreToolUse(Task|Agent)               -> gate handle_subagent_start (spawn allowlist + ledger;
                                          fanops-orchestrator-as-subagent denied UNCONDITIONALLY)
  PreToolUse(Write|Edit|MultiEdit|NotebookEdit)
                                       -> state/enforcement write protection. Claude hooks carry the
                                          caller's agent_type, so verification records are writable
                                          ONLY by a fanops-worker — closing the un-hookable-Write
                                          residual the Cursor runtime documents.
  SubagentStop                         -> gate handle_subagent_stop  (attribution ledger)

Deny = stdout JSON {"hookSpecificOutput": {"permissionDecision": "deny", ...}}, exit 0.
Allow = no output, exit 0. Unexpected errors fail CLOSED for Bash (the land surface), open otherwise.
The gate itself is inert unless a wave is engaged (ACTIVE marker / FANOPS_ORCHESTRATED) except the
nested-orchestrator spawn deny, which is unconditional by design.
"""
import contextlib, io, json, os, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / ".cursor" / "hooks"))
import orchestration_gate as gate  # noqa: E402

_FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_SPAWN_TOOLS = {"Task", "Agent"}


def _emit_deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    return 0


def _gate_decision(handler, payload: dict, root) -> dict:
    """Run a Cursor-gate handler, capturing its printed decision object."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        handler(payload, root)
    try:
        return json.loads(buf.getvalue().strip() or "{}")
    except Exception:
        return {}


def _relay(handler, payload: dict, root) -> int:
    d = _gate_decision(handler, payload, root)
    if d.get("permission") == "deny":
        return _emit_deny(d.get("agent_message") or d.get("user_message") or "refused by orchestration gate")
    return 0


def _rel(root: Path, file_path: str) -> str:
    try:
        return str(Path(file_path).resolve().relative_to(Path(root).resolve()))
    except Exception:
        return file_path or ""


def handle_file_write(data: dict, root) -> int:
    if not gate.is_active(root):
        return 0
    ti = data.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ""
    rel = _rel(root, fp)
    if not rel:
        return 0
    caller = str(data.get("agent_type") or "")  # absent = main-session agent (the orchestrator)
    if rel.startswith(".orchestration/state/verified/") and rel.endswith(".json"):
        if caller == "fanops-worker":
            return 0
        return _emit_deny(
            "REFUSED (orchestration gate): only a fanops-worker verifier sub-agent may write a "
            f"verification record (caller: {caller or 'main agent'}). Spawn a verifier per "
            ".agents/_worker-protocol.md — records written by anyone else would be forgery.")
    protected = rel.startswith(".orchestration/") or any(
        rel == p.rstrip("/") or rel.startswith(p if p.endswith("/") else p + "/")
        for p in gate._PROTECTED_PATHS) or bool(gate.enforcement_hits([rel]))
    if protected:
        return _emit_deny(
            f"REFUSED (orchestration gate): {rel} is orchestration state / enforcement machinery — "
            "not writable during a wave by any agent or tool. Enforcement changes are OPERATOR-ONLY "
            "and merge outside waves.")
    return 0


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    event = data.get("hook_event_name") or ""
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    try:
        if event == "PreToolUse" and tool == "Bash":
            return _relay(gate.handle_before_shell, {"command": ti.get("command") or ""}, root)
        if event == "PreToolUse" and tool in _SPAWN_TOOLS:
            return _relay(gate.handle_subagent_start, {
                "subagent_type": ti.get("subagent_type"),
                "subagent_model": ti.get("model"),
                "task": ti.get("description") or (ti.get("prompt") or "")[:200],
                "subagent_id": None,
                "parent_conversation_id": data.get("session_id"),
                "is_parallel_worker": bool(ti.get("run_in_background")),
                "git_branch": None}, root)
        if event == "PreToolUse" and tool in _FILE_TOOLS:
            return handle_file_write(data, root)
        if event == "SubagentStop":
            return _relay(gate.handle_subagent_stop, {
                "subagent_type": data.get("agent_type"),
                "subagent_id": data.get("agent_id"),
                "task": None, "status": "completed",
                "parent_conversation_id": data.get("session_id")}, root)
        return 0
    except Exception as exc:
        if event == "PreToolUse" and tool == "Bash":
            return _emit_deny(f"orchestration gate adapter error ({type(exc).__name__}) — failing closed on shell")
        return 0


if __name__ == "__main__":
    sys.exit(main())
