---
name: block-unasked-frugality
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.(prd|plan)\.md$|/prds/|/plans/
  - field: content
    operator: regex_match
    pattern: (?i)to save (you |us )?(money|cost|tokens|api ?calls|spend|time)|cheaper (option|route|approach|version|path)|to (keep|reduce|cut) (the |our )?(cost|spend|token|api)|less (costly|expensive) (option|approach)|for cost reasons
---
BLOCKED: imposing frugality the operator did not ask for. "I didn't ask you to save me money" — build the full-ambition version; cost guardrails are the operator's PRODUCT call, not a default you smuggle into the plan. Domain budget terms (FANOPS_CAST_PICK_BUDGET etc.) describe a real product lever and do NOT match this rule — only cost-SAVING justification does.
