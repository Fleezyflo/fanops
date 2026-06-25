---
name: block-stub-theater
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.py$
  - field: content
    operator: regex_match
    pattern: \bTODO\b|\bFIXME\b|NotImplementedError|#\s*for now\b|\bassert True\b
---
BLOCKED: stub/placeholder = simulated completion. Implement it for real now, or do not mark the step/test/task complete. `assert True` is a green light wired to nothing.
