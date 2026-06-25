---
name: block-disable-via-edit
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.claude/hookify\.[^/]*\.local\.md$
  - field: content
    operator: regex_match
    pattern: (?m)^\s*enabled:\s*false\b
---
BLOCKED: disabling a guardrail by editing it to `enabled: false`. Flipping a rule off via the editor is the same quiet self-silencing the Bash guard blocks. If a rule is genuinely wrong, FIX its pattern or have the OPERATOR toggle it with /hookify-configure — the agent does not disable its own guardrails mid-task.
