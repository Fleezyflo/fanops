# Pack: H-INT — invariant authority

## Re-verified 2026-10-18

Subject is `origin/main` `430ece7377bf33cc5c5e72876c13d6b78b8e6ca9`. Assessment date 2026-10-18. `git diff origin/main -- src` is empty on this SHA. Entry symbols re-read: `tools/arch/policy/rules.py` ARCH-001 (“The subsystem partition is TOTAL”); `tools/arch/cli.py` `cmd_regen` / `cmd_ci`; `partition.json` `modules`. H-INT-F01 stays resolved. No retag. No X-keys in `partition.json`.

## Findings

| ID | Severity | Class | Status | Observed | Required | Evidence |
|----|----------|-------|--------|----------|----------|----------|
| H-INT-F01 | low | taxonomy | resolved | Two deliberate C6/C7 disagreements between the operator audit index and `partition.json`: operator edits of `reconcile.py` are **C6** (publish lane in `.agents/lanes.json`); `partition.json` keeps `src/fanops/reconcile.py` in **C7**. `post/metrics/*` is **C6** in `partition.json` and **C7** for operator audit (metrics/learning). X-modules (e.g. X-DEP) stay audit overlays only — no third partition key. ARCH-001 enforces subsystem totality in `kb/subsystems.json` / derived `modules.json`, not lane retag. | Dual map kept. Do not merge operator index with `partition.json`. Do not add X-keys to `partition.json`. Do not retag `lanes.json`. | `tools/arch/policy/rules.py` ARCH-001; `tools/arch/cli.py` `cmd_regen`, `cmd_ci`; `docs/CODEMAPS/partition.json` `modules`; `.github/workflows/architecture.yml` |

## Dual map (operator index vs partition.json)

| Path | `partition.json` | Operator audit index |
|------|------------------|----------------------|
| `src/fanops/reconcile.py` | C7 | C6 (publish owns edits; lane `publish`) |
| `src/fanops/post/metrics/*` (four modules) | C6 | C7 (metrics/learning audit) |

X-DEP and other X-modules overlap C1–C10 files in prose only; `partition.json` values remain C1–C10 exclusively (`test_live_partition_covers_every_module`).

## Evidence commands run

```
SUBJ=430ece7377bf33cc5c5e72876c13d6b78b8e6ca9

git show $SUBJ:tools/arch/policy/rules.py | rg -n 'ARCH-001'
# Rule("ARCH-001", "The subsystem partition is TOTAL"

git show $SUBJ:tools/arch/cli.py | rg -n 'def cmd_regen|def cmd_ci|"regen"|"ci"'
# cmd_regen, cmd_ci; subparsers regen + ci

git show $SUBJ:.reports/architecture/derived/modules.json | rg 'partition_is_total'
# "partition_is_total": true

git show $SUBJ:docs/CODEMAPS/partition.json | python3 -c "import json,sys; p=json.load(sys.stdin); m=p['modules']; print(len(m), sorted(set(m.values())), [m[k] for k in sorted(m) if 'reconcile' in k or 'post/metrics' in k])"
# 196 ['C1'…'C10']; reconcile C7; metrics paths C6
# src/fanops/reconcile.py C7; post/metrics/* C6 (four paths)

git show $SUBJ:.agents/lanes.json | rg reconcile
# "src/fanops/reconcile.py": "publish"

git grep -n 'src/fanops/reconcile.py' $SUBJ -- .agents/lanes.json docs/CODEMAPS/partition.json
git grep -n 'src/fanops/post/metrics/' $SUBJ -- docs/CODEMAPS/partition.json

rg -n 'python -m tools.arch regen|python -m tools.arch ci' .github/workflows/architecture.yml
# regen at docs/regen step; ci at composite gate step
```

Scanned-source edit path (documented, not run this turn): `python -m tools.arch regen`, then `python -m tools.arch ci` as wired in `.github/workflows/architecture.yml`. `./scripts/check.sh` was not run (pack-only remediate).
