---
name: warn-disabling-guardrails
enabled: true
event: bash
action: warn
conditions:
  - field: command
    operator: regex_match
    pattern: FANOPS_STOP_GATE\s*=\s*0|rm\s+[^|&;]*hookify\.[^|&;]*\.local\.md|rm\s+[^|&;]*stop-completion-gate|rm\s+[^|&;]*settings\.local\.json
---
GUARDRAIL DISABLE (not a block): you are turning OFF a completion guarantee — the Stop gate kill-switch or a rule/hook file. That is allowed for real maintenance, but it must be a DELIBERATE, operator-visible choice, never a quiet escape from a gate that just blocked you. If a gate is wrong, FIX the gate; don't silence it and move on.
