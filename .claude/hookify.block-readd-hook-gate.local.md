---
name: block-readd-hook-gate
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.py$
  - field: content
    operator: regex_match
    pattern: has_artist_reference|_ARTIST_PRONOUN
---
BLOCKED: re-adding the rejected hook gate. RF5 deletes has_artist_reference and starves the generator at its five priming sources. Fix the SOURCE; a read-only meter (narration_signature) that never strips is fine.
