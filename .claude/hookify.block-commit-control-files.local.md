---
name: block-commit-control-files
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: git\s+(add|commit)\b[^|&;]*\b00_control/(accounts|personas)\.(json|lock)
---
BLOCKED: staging/committing a live control file. 00_control/accounts.json + personas.json are runtime state (live channel mapping / persona records) — CLAUDE.md says NEVER commit accounts.json; they're reconstructable from docs/INSTAGRAM_CONNECT.md. The real fix is `git rm --cached` + .gitignore so they can't be committed at all. (NB: this catches an explicit path; `git commit -am` of an already-tracked file is only fully closed by untracking.) Disable deliberately if you truly mean to commit it.
