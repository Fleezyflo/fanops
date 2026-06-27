---
name: decide-dont-ask-block
enabled: true
event: all
action: block
tool_matcher: AskUserQuestion
conditions:
  - field: questions
    operator: regex_match
    pattern: .
---
BLOCKED: asking instead of deciding. This is NOT "just decide" — it is decide GROUNDED in the fixed hierarchy: (1) the operator's brief, (2) the stated requirement, (3) best practice, (4) compliance with the existing base/codebase, (5) reliability / robustness / resilience, (6) scalability. Resolve the answer from those and PROCEED. If a genuine fork survives that hierarchy, state the fork + your grounded choice in ONE prose line and act on it; the operator redirects if wrong. Asking what the brief/requirements already settle IS the diversion. Only the OPERATOR re-enables questions, via /hookify-configure; the agent does not ask its way out.
