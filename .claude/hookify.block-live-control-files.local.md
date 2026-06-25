---
name: block-live-control-files
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: 00_control/(accounts\.(json|lock)|personas\.(json|lock))
---
BLOCKED: writing a live control file. accounts.json is the live channel mapping (NEVER commit it); personas/.lock are runtime state. If this is an intended runtime edit, disable this rule deliberately for the write.
