---
name: block-operator-gate-hedging
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.md$
  - field: content
    operator: regex_match
    pattern: (?i)deferred for operator|\(operator[ -]?gated\)|\(deferred for|pending operator decision|awaiting operator decision
---
BLOCKED: operator-gating/deferment hedge. Decide it now using best practice; write the decision, not the deferral. (Real lifecycle - 'operator approves', awaiting_approval, go-live confirm - passes.)
