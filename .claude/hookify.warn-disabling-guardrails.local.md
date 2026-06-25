---
name: warn-disabling-guardrails
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: FANOPS_STOP_GATE\s*=\s*0|(\brm\b|\bmv\b|\bchmod\b|\btruncate\b|>\s*)[^|&;\n]*(hookify\.[^|&;\n]*\.local\.md|stop-completion-gate|settings(\.local)?\.json)
---
BLOCKED: disabling a guardrail. You are turning OFF a completion guarantee — the Stop-gate kill-switch, a rule file, the gate script, or the settings that wire it. This is NOT a quiet escape from a gate that just blocked you. If a gate is genuinely wrong, FIX the gate or have the OPERATOR disable it in settings; the agent does not silence its own guardrails mid-task.
