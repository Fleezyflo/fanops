---
name: nudge-decide-dont-ask
enabled: true
event: all
action: warn
tool_matcher: AskUserQuestion
conditions:
  - field: questions
    operator: regex_match
    pattern: .
---
DECIDE-FIRST CHECK (not a block): before asking, can you resolve this from the code, sensible defaults, or best practice? Scale, cost, UX-layout, and data-model calls are YOURS to make — make them and note the call in one line. Only ask on a GENUINE product fork you cannot resolve. The operator has repeatedly said "just decide / don't ask / do it and show me."
