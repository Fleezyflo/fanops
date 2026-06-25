---
name: block-speculative-live-cli
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: ((^|[;&|])\s*|(ba)?sh\s+-c\s*["']|python[0-9.]*\s+-m\s+)fanops\s+(publish|crosspost|reconcile|resolve|track|go-live|go_live)\b
---
BLOCKED: live `fanops` verb hits external services and can publish/mutate live state. CLAUDE.md: tests and read-only verbs only unless the operator explicitly asked. Use pytest or a dryrun to verify.
