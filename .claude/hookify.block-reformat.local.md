---
name: block-reformat
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: \b(black|ruff\s+format|autopep8|yapf)\b
---
BLOCKED: mass-reformatting. CLAUDE.md hard constraint - the compact one-liner house style is deliberate. Use `ruff check .` (lint only), never a formatter.
