---
name: block-reset-state-files
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: (^|/)ledger\.json$|00_control/(cutover|hashtags|hashtag_budget|learn_doctor|tuning|personas|accounts)\.json$
  - field: content
    operator: regex_match
    pattern: \A\s*(\{\s*\}|\[\s*\]|\{\s*"[a-zA-Z_]+"\s*:\s*(\[\s*\]|\{\s*\})\s*\})\s*\Z
---
BLOCKED: resetting live state to empty. Writing `{}` / `[]` / `{"posts": []}` over ledger.json or a 00_control state file ERASES accumulated runtime state (no-wipe HARD RULE). If you are legitimately initializing a NEW file, disable this rule for that one write; otherwise patch the existing structure transactionally instead of overwriting it.
