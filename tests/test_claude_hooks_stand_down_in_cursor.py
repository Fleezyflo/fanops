"""The Claude Code hooks `.claude/settings.json` wires stand down in Cursor sessions.

Cursor runs a workspace's Claude Code hooks too (its `thirdPartyExtensibilityEnabled`, on by default), with its own
payload: lowercase event names and a `cursor_version` field Claude Code never sends. hookify denies only on
`PreToolUse`, `PostToolUse` or `Stop`, so in Cursor its block rules printed BLOCKED and let the call run: at least
eight `git reset --hard` ran from Cursor agents in this repo on Sep 3 and 8, one of them a subagent's. ~/.cursor/contract guards Cursor sessions (it refuses a hard reset in
every session), so on a Cursor payload each hook exits 0 and prints nothing.

Each case is a payload the hook acts on in Claude Code (the control); the same payload with Cursor's fields added
must leave no output.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_HOOKS = _ROOT / ".claude" / "hooks"
_CURSOR = {"cursor_version": "3.20.10", "workspace_roots": [str(_ROOT)]}
# The repo's hookify rules are machine-local and gitignored, so the control brings its own block rule.
_RULE = """---
name: stand-down-probe
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: probe\\.py$
---
BLOCKED: probe rule.
"""


def _payloads(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "hookify.stand-down-probe.local.md").write_text(_RULE, encoding="utf-8")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Would you like me to push the branch?"}})
        + "\n",
        encoding="utf-8",
    )
    return {
        "hookify-run.py": {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "src/probe.py", "content": "x = 1\n"},
        },
        "anti-divert-contract.py": {"hook_event_name": "UserPromptSubmit", "prompt": "do your job"},
        "block-hedge-on-stop.py": {"hook_event_name": "Stop", "transcript_path": str(transcript)},
        "decide_dont_ask.py": {
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "Should I build the simpler version first?"}]},
        },
    }


def _run(script, payload, cwd):
    return subprocess.run(
        [sys.executable, str(_HOOKS / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        cwd=cwd,
    )


@pytest.mark.parametrize("script", ["hookify-run.py", "anti-divert-contract.py", "block-hedge-on-stop.py", "decide_dont_ask.py"])
def test_a_cursor_payload_leaves_no_output(tmp_path, script):
    payload = _payloads(tmp_path)[script]
    claude = _run(script, payload, tmp_path)
    assert claude.returncode == 0, claude.stderr
    assert claude.stdout.strip(), f"{script}: the control printed nothing, so the Cursor case proves nothing: {claude.stderr[-300:]!r}"
    cursor = _run(script, {**payload, **_CURSOR}, tmp_path)
    assert cursor.returncode == 0, cursor.stderr
    assert not cursor.stdout.strip(), f"{script} acted on a Cursor payload: {cursor.stdout[:200]!r}"
