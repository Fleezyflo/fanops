---
name: block-overclaim-without-proof
enabled: true
event: file
action: block
conditions:
  - field: content
    operator: regex_match
    pattern: structurally\s+(0|zero)|unrepresentable|impossible by construction|cannot be constructed|guaranteed (impossible|safe|correct|never)
---
BLOCKED: overclaim. Cite the TEST that proves the bad path can't be constructed, or downgrade to what is true (never SILENT, explicitly labeled, denies on absence). A guarantee with no refuting test is theater. (To discuss the word itself, use /hookify-configure to toggle.)
