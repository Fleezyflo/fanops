# Pack: H-INT — invariant authority

Subject `d2cded31e29e027e4ed55bfdb99b9a93ac4f024e`. H-INT-F01 stays accepted. No retag.

## Findings

| ID | Severity | Class | Status | Observed | Required | Evidence |
|----|----------|-------|--------|----------|----------|----------|
| H-INT-F01 | low | taxonomy | accepted | ARCH-001 remains the S-subsystem totality rule (`Rule` title "The subsystem partition is TOTAL"; `kb/subsystems.json` vs `derived/modules.json`, `partition_is_total`). `partition.json` is the operator C-map and must stay C1–C10. reconcile.py is C7 in partition.json and C6 for operator remediation (publish lane owns the file). post/metrics/* is C6 in partition.json and C7 for operator audit. | Document the dual map. Do not merge the partitions. Do not add X-keys. | `tools/arch/policy/rules.py` ARCH-001; `docs/CODEMAPS/partition.json`; `tests/test_production_audit_gates.py` `test_live_partition_covers_every_module`; `docs/audit/AUDIT_MODULE_INDEX.md`; `.agents/lanes.json` |

## Split

reconcile.py is C7 in partition.json and C6 for operator remediation (publish lane owns the file); post/metrics/* is C6 in partition.json and C7 for operator audit. No retag.

## Evidence commands run

```
git rev-parse HEAD
# d2cded31e29e027e4ed55bfdb99b9a93ac4f024e

git diff --stat origin/main...HEAD
# 29 files, +904 / −26

git show HEAD:tools/arch/policy/rules.py | rg -n 'ARCH-001'
# Rule("ARCH-001", "The subsystem partition is TOTAL"

python3 -c 'json.load subsystems.json; print sorted subsystem ids'
# S01_foundation … S19_maintenance, all S-prefixed

rg -n 'partition_is_total' .reports/architecture/derived/modules.json
# "partition_is_total": true

python3 -c 'print partition.json keys for reconcile.py and src/fanops/post/metrics/'
# live_py_count 196
# value_set C1 C2 C3 C4 C5 C6 C7 C8 C9 C10
# outside C1–C10: none
# src/fanops/reconcile.py=C7
# src/fanops/post/metrics/__init__.py=C6
# src/fanops/post/metrics/common.py=C6
# src/fanops/post/metrics/postiz_read.py=C6
# src/fanops/post/metrics/zernio_read.py=C6

git grep -n 'src/fanops/reconcile.py' HEAD -- .agents/lanes.json docs/CODEMAPS/partition.json
# lanes.json owner publish; partition.json value C7

git grep -n 'src/fanops/post/metrics/' HEAD -- docs/CODEMAPS/partition.json
# all four files C6
```

`test_live_partition_covers_every_module` asserts every `partition.json` value is in C1–C10. Operator index keeps reconcile.py on C6 (publish wire) and metrics/learning, including `post/metrics`, on C7. Scanned-source edit path, not run this turn: `python -m tools.arch regen`, then `python -m tools.arch ci` in `.github/workflows/architecture.yml`.
