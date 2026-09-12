# Pack: H-INT — invariant authority

## Findings

| ID | Severity | Class | Observed | Required | Evidence |
|----|----------|-------|----------|----------|----------|
| H-INT-F01 | low | taxonomy | Two partitions: S-subsystem (ARCH-001) vs C-cluster (operator) | Document dual authority; no merge | `tools/arch/policy/rules.py` ARCH-001; `partition.json` |

## Evidence commands run

```
python -m tools.arch ci  # S totality (operator does not run locally per AGENTS.md)
rg 'partition_is_total' .reports/architecture/derived/modules.json
```
