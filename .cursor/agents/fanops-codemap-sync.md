---
name: fanops-codemap-sync
description: >-
  Codemap sync worker — invoked by the Cursor webhook automation (see .cursor/automations/codemap-sync.md).
  WHEN to run is gated by .github/workflows/codemap-sync-trigger.yml (src/** on main + concurrency).
  WHAT: drift-detect, no-op if current, one cursor/codemaps-sync PR max, self-heal legacy alignment PRs.
model: auto
readonly: false
is_background: true
---

# FanOps codemap sync (worker)

You keep `docs/CODEMAPS/` aligned with `src/fanops/` **without PR landfill**.

**You are not the scheduler.** When this runs is decided upstream:
- `.github/workflows/codemap-sync-trigger.yml` — path filter (`src/**`), concurrency debounce
- `.cursor/automations/codemap-sync.md` — webhook automation wiring (Cursor UI)

The old automation used **PR merged** + new random branch per run → 26 drafts in one wave. That trigger
is retired; do not recreate it.

Read first: `docs/CODEMAPS/README.md`, `docs/CODEMAPS/full-trace-index.md` → "How to regenerate".

## Non-negotiable invariants

1. **No-op is success.** If codemaps already match `origin/main`, exit 0 with **no branch, no PR, no commit**.
2. **One inflight artifact.** Fixed branch `cursor/codemaps-sync` only. Never mint `codemaps-source-alignment-*`.
3. **At most one open codemap PR** at any time. Update it in place; never open a second.
4. **Docs-only scope.** Touch only `docs/CODEMAPS/**`. Never edit `src/`, `tests/`, or control data.
5. **Land or skip — never linger.** Merge when CI green; if nothing to land, leave zero open codemap PRs.
6. **Historical snapshots stay historical.** Do not rewrite `lifecycle-full-picture.md` body (dated audit);
   preserve its superseded-by banner if present.

## Phase 0 — self-heal repo hygiene (always)

```bash
git fetch origin --prune
git checkout main && git pull origin main
```

**Legacy cleanup (mandatory on every run):** close every open PR whose head matches
`cursor/codemaps-source-alignment-*`. These are corpses from the old automation — do not merge them.

```bash
gh pr list --repo Fleezyflo/fanops --state open \
  --json number,headRefName \
  --jq '.[] | select(.headRefName | test("^cursor/codemaps-source-alignment-")) | .number' \
| while read -r n; do gh pr close "$n" --repo Fleezyflo/fanops; done
```

If `gh pr close` returns 403, **stop and report** the exact loop for the operator — do not claim success.

Also close any duplicate open PR on `cursor/codemaps-sync` except the newest (keep one).

## Phase 1 — restore extractors + drift detection (idempotent gate)

Extractors live in `.reports/` (gitignored). Restore if missing:

```bash
git show 20c96d4:.reports/ast_extract.py > .reports/ast_extract.py
git show 20c96d4:.reports/build_graphs.py > .reports/build_graphs.py
chmod +x .reports/ast_extract.py .reports/build_graphs.py
```

Run deterministic layer (stdlib-only, local verification — do not commit `.reports/` output):

```bash
python3 .reports/ast_extract.py src > .reports/structural_index.json
python3 .reports/build_graphs.py
```

**Drift signals** (any → proceed to Phase 2; none → Phase 4 no-op):

| Check | How |
|-------|-----|
| Deterministic counts stale | `full-trace-index.md` callable/module counts disagree with `.reports/structural_index.json` / `call_graph.json` |
| Known semantic rot | `rg` live claims for removed machinery: `moment_casting` as active gate, `AccountSelection`, `FANOPS_ACCOUNT_CASTING` as LLM casting stage, `crosspost.py:269` birth site (live is `:228-232` post-P11) |
| SCHEMA drift | `data.md` / C1 ledger version ≠ current `SCHEMA_VERSION` in `src/fanops/models.py` |
| Recent main delta | `git log origin/main --oneline -20 -- src/fanops/` touched files whose cluster trace likely stale |

**If all checks pass:** print `codemap sync: no drift @ <main-sha> — no-op` and **exit 0**. Do not open a PR.

## Phase 2 — edit semantic layer (surgical, not wholesale)

Update only files that actually drifted. Priority order:

1. `full-trace-index.md` — counts, module coverage, safety-verdict table if invariant changed
2. Affected `subsystem-traces/C*.md` — only clusters whose `src/` files changed on main
3. `system-lens-map.md`, `architecture.md`, `data.md`, `dependencies.md`, `anomalies.md` — grep-driven fixes
4. `fresh-ingestion-trace.md` — only when pipeline semantics changed (owner-moment, gates, approval flow)

Rules:

- Every live claim needs a **verified `file:line`** (re-grep; anchors drift ±30 lines — trust the symbol).
- Removed code → document as removed with ticket/P11 note; never describe dead paths as live.
- Do not regenerate all 10 C1–C10 traces unless a cluster's **intent** changed materially.
- Update the `<!-- Generated: YYYY-MM-DD -->` header date on every file you touch.

## Phase 3 — single-branch PR (update-in-place)

```bash
MAIN_SHA=$(git rev-parse --short origin/main)
git checkout -B cursor/codemaps-sync origin/main   # reset branch to main tip
# … apply doc edits …
git add docs/CODEMAPS/
git commit -m "docs(codemaps): sync to ${MAIN_SHA} — <one-line drift summary>"
git push -u origin cursor/codemaps-sync --force-with-lease
```

**PR policy:**

- If an open PR exists on `cursor/codemaps-sync`: the push **updates** it (same PR number).
- If none exists: `gh pr create --repo Fleezyflo/fanops --base main --head cursor/codemaps-sync \
  --title "docs(codemaps): sync to ${MAIN_SHA}" --body "<drift summary + test plan>"`
- **Not a draft.** Docs-only; CI `unit` job is the gate.
- Enable auto-merge when checks pass: `gh pr merge --auto --squash` (or operator merges manually).

On conflict (branch behind after wave): `git fetch origin && git rebase origin/main`, re-run Phase 1
extractors, re-apply edits, force-push **same branch** — never fork a new branch.

## Phase 4 — finish

| Outcome | Action |
|---------|--------|
| No drift | Exit 0, zero open codemap PRs |
| PR merged | `git push origin --delete cursor/codemaps-sync` (optional hygiene), exit 0 |
| PR open, CI pending | Report PR URL + "waiting on CI"; orchestrator may re-invoke after green |
| `gh` 403 on close/merge | Stop; give operator exact commands |

Verify finish line for orchestration waves:

```bash
python scripts/orchestrate.py status   # codemap PRs must not pollute open-PR count after land
```

## Failure modes → self-heal

| Failure | Fix |
|---------|-----|
| 26 stale `codemaps-source-alignment-*` PRs | Phase 0 close loop (never merge) |
| Extractors missing | Restore from `20c96d4` |
| PR conflict | Rebase `cursor/codemaps-sync` onto `origin/main`, regen, force-push same branch |
| Behind main | Update same PR; do not open another |
| Docs-only CI red | Fix ruff/markdown issues in touched files; push to same branch |
| False positive drift | Tighten grep checks; no-op must stay trustworthy |

## Out of scope

- Editing `src/` or `tests/` (file a MOL ticket instead)
- Running live `fanops` publish/metrics verbs
- Rewriting `lifecycle-full-picture.md` as a living doc
- MOL-223 live recovery / operator Mac workflows
