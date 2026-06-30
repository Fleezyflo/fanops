#!/usr/bin/env python3
"""UserPromptSubmit / beforeSubmitPrompt — short execute-first contract (JSON for Cursor).

Prose nags are ignored when long; this stays ≤12 lines and names FanOps failure modes
from live sessions: operator handoffs, backlog shipping, hedging. Fails open."""
from __future__ import annotations
import json
import re
import sys

_BASE = (
    "EXECUTE-FIRST (FanOps): Do the fix in code/CLI yourself — commit, test, reconcile "
    "ledger only when the task requires it. Do NOT tell the operator what to click, "
    "approve, or run unless you are blocked on a secret/credential only they hold. "
    "Do NOT bulk-publish, ship queues, or 'prove it works' on live posts unless explicitly asked. "
    "Lead with verdict + evidence; one-line fork only when genuinely blocked."
)

_FRUSTRATED = re.compile(
    r"(do your job|not asking me|stop (telling|listing)|operator|my job|you skipped|"
    r"don't understand|wasteful|handoff|broken system|fix the code)",
    re.I,
)


def _contract(user_prompt: str) -> str:
    lines = ["<execute-first>", _BASE]
    if _FRUSTRATED.search(user_prompt or ""):
        lines.append("⚠ Operator rejected handoffs this turn — no runbooks, no optional later, ship the code path now.")
    lines.append("</execute-first>")
    return "\n".join(lines)


def main() -> None:
    user_prompt = ""
    event = "UserPromptSubmit"
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
            user_prompt = str(data.get("prompt") or data.get("message") or data.get("content") or "")
            event = str(data.get("hook_event_name") or event)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"anti-divert-contract: stdin: {exc}", file=sys.stderr)
    out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": _contract(user_prompt)}}
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
