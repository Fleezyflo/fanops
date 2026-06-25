---
name: block-silent-fail-open
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.py$
  - field: content
    operator: regex_match
    pattern: except[^:\n]*:\s*pass\b
---
BLOCKED: silent swallow (R2 root). `except ...: pass` discards the error with no trace. Propagate it OR model the degraded outcome as a first-class visible state (degraded_reason/held) surfaced in Review. (Typed `except X: return <fallback>` is intentional fail-open in this codebase — that's a code-review call, not a hard block, so it's no longer matched here.)
