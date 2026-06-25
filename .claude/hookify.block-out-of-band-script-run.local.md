---
name: block-out-of-band-script-run
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: \b(python[0-9.]*|sh|bash|zsh|node|ruby|perl|deno|bun)\s+\S*(/tmp/|/private/tmp/|/var/folders/|/private/var/folders/|scratchpad/)\S+\.(py|sh|js|mjs|cjs|rb|pl|ts)\b
---
BLOCKED: running a script from outside the repo (tmp / scratchpad). Executing a file the in-repo hooks never inspected is a run-around — it smuggles logic (incl. anything that touches protected state or the guardrails) past every PreToolUse rule. Put the code in the repo where the rules can see it, or run it inline. The scratchpad is for OUTPUT/data, not for executing logic against the project.
