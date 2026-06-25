---
name: block-wipe-ledger
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: (\brm\b|\btruncate\b|>)[^|&;\n]*\b(ledger\.json|00_control/(cutover|hashtags|hashtag_budget|learn_doctor|tuning|personas|accounts)\.json)
---
BLOCKED: wiping live persisted state. ledger.json is the no-wipe content lifecycle (day-bucketed, accumulated across runs); 00_control/*.json are live runtime state. HARD RULE: never reset/wipe the ledger — past manual resets caused real data loss. Read/patch it transactionally; never rm/truncate/`>`-overwrite it. Disable deliberately only if you have a stash + a reason.
