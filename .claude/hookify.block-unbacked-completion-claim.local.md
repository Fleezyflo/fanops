---
name: block-unbacked-completion-claim
enabled: false
event: stop
action: block
conditions:
  - field: transcript
    operator: regex_match
    pattern: (?i)(✅|\ball tests? (pass|passed|passing)\b|\bit works\b|\bfully (working|fixed|implemented)\b|\bverified working\b|\bproduction[- ]ready\b|\b100% (complete|done)\b|\bsuccessfully (implemented|completed|fixed)\b)
---
PARKED (enabled:false). Tested 2026-06-26: the LOGIC is correct - on isolated text it blocks a completion claim and allows honest/neutral text. But field:transcript matches the ENTIRE growing conversation, so (1) the regex scan of a real multi-MB+ session transcript TIMED OUT (>2 min) on every turn-end, and (2) once any completion word appears in history it fires forever; as a stop-block with no stop_hook_active guard that wedges the session. Real but not viable. Re-enable only if hookify gains last-message-only scoping. The guard that holds: evidence-on-the-table + the operator rejecting claims without it.
