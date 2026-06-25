#!/usr/bin/env python3
"""Self-contained hookify runner — evaluates .claude/hookify.*.local.md against a tool call.

Vendored so the rules fire deterministically from committed settings.json, with NO dependency
on the hookify plugin being installed/enabled (that was the one unverifiable link). Wired at
PreToolUse; mirrors the plugin's event mapping (Bash->bash, Edit/Write/MultiEdit->file, else
None=all). Fails OPEN on any error so a runner bug never blocks a tool call.
"""
import sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'hookify_vendor'))

try:
    from config_loader import load_rules
    from rule_engine import RuleEngine
except Exception as e:  # noqa: BLE001 — fail open, never block on a load error
    print(json.dumps({"systemMessage": f"hookify-run load error: {e}"}))
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get('tool_name', '')
    if tool == 'Bash':
        event = 'bash'
    elif tool in ('Edit', 'Write', 'MultiEdit'):
        event = 'file'
    else:
        event = None  # load all; tool_matcher rules (e.g. AskUserQuestion) still apply
    try:
        rules = load_rules(event=event)
        result = RuleEngine().evaluate_rules(rules, data)
    except Exception as e:  # noqa: BLE001 — fail open
        print(json.dumps({"systemMessage": f"hookify-run error: {e}"}))
        sys.exit(0)
    if result:
        print(json.dumps(result))
    sys.exit(0)


if __name__ == '__main__':
    main()
