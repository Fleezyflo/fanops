---
name: block-git-reset-hard
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f
---
BLOCKED: destructive git op. `git reset --hard` already wiped the live accounts.json mapping once. Stash 00_control/accounts.json + personas.json first; prefer `git pull --ff-only`. Disable this rule deliberately if you truly mean it.
