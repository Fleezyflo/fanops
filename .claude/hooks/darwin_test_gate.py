#!/usr/bin/env python3
"""darwin_test_gate.py — MACHINE-scoped local-test guard (PreToolUse Bash).

Stacked/parallel suite runs take the operator's Mac down — that is a property of the HOST, not the
repo, so the guard keys on the platform: Darwin + pytest/check-full.sh -> deny (FANOPS_LOCAL_TESTS=1
is the operator override); Linux (GitHub CI, Claude cloud sandboxes) -> inert, the suite runs there.
Replaces the repo-wide permissions.deny pytest entries; the orchestration gate still refuses suite
runs during waves on top. Deny = stdout JSON (same contract as orchestration_gate_claude.py), exit 0.
Fail OPEN on unexpected errors — a habit rail for the host, not a security boundary.
"""
import json, os, platform, re, sys

# Command-position pytest / python -m pytest / check-full.sh (env-var and bash/path prefixes
# included) — mirrors the orchestration gate's _LOCAL_TESTS so both rails catch the same commands.
_LOCAL_TESTS = re.compile(
    r"(?:^|[|&;])\s*(?:[A-Za-z_]\w*=\S+\s+)*(?:bash\s+|sh\s+)?(?:\S*/)?"
    r"(?:pytest\b|python3?(?:\.\d+)?\s+-m\s+pytest\b|check-full\.sh\b)")

_DENY_MSG = (
    "REFUSED (darwin test gate): the test suite may not run on the operator's Mac — stacked runs "
    "crash this host. Push the branch and let GitHub CI run it, or use a Claude cloud session "
    "(Linux), where this gate is inert. FANOPS_LOCAL_TESTS=1 is the operator-only override.")


def should_block(command: str, system: str, env: dict):
    """Deny-reason when `command` is a suite run forbidden on this machine, else None. Pure."""
    if system != "Darwin":
        return None
    cmd = command or ""
    if str(env.get("FANOPS_LOCAL_TESTS", "")).strip() == "1" or "FANOPS_LOCAL_TESTS=1" in cmd:
        return None
    if not _LOCAL_TESTS.search(cmd):
        return None
    return _DENY_MSG


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    if (data.get("tool_name") or "") != "Bash":
        return 0
    command = (data.get("tool_input") or {}).get("command") or ""
    try:
        reason = should_block(command, platform.system(), os.environ)
    except Exception:
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
