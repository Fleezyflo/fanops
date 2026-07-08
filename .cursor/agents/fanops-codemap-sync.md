---
name: fanops-codemap-sync
description: >-
  Codemap sync worker — launched by Cursor webhook (see .cursor/automations/codemap-sync.md).
  WHEN: GHA codemap-sync-trigger.yml (src/** on main + drift preflight). WHAT: drift script,
  surgical doc edits, one cursor/codemaps-sync PR, legacy PR cleanup.
model: auto
readonly: false
is_background: true
---

# FanOps codemap sync (worker)

Keep `docs/CODEMAPS/` aligned with `src/fanops/`. **You are not the scheduler.**

| Layer | Owner |
|-------|-------|
| WHEN (path filter, debounce, drift preflight) | `.github/workflows/codemap-sync-trigger.yml` |
| Drift detection (machine-checkable) | `scripts/codemap_drift.py` |
| WHAT (semantic edits + PR) | **this agent** |

Read first: `docs/CODEMAPS/README.md`, `docs/CODEMAPS/full-trace-index.md`.

## Invariants

1. **No-op is success.** `python scripts/codemap_drift.py` exit 0 → print `codemap sync: no-op @ <sha>` and stop.
2. **One branch:** `cursor/codemaps-sync` only. Never `codemaps-source-alignment-*`.
3. **One open PR max** on that branch. Push updates the existing PR.
4. **Docs-only.** Touch only `docs/CODEMAPS/**`.
5. **Historical snapshots stay historical.** Do not rewrite `lifecycle-full-picture.md` body.

## Phase 0 — hygiene (every run)

```bash
git fetch origin --prune
git checkout main && git pull origin main
```

Close legacy PRs (old automation corpses):

```bash
gh pr list --repo Fleezyflo/fanops --state open --json number,headRefName \
  --jq '.[] | select(.headRefName | test("^cursor/codemaps-source-alignment-")) | .number' \
| while read -r n; do gh pr close "$n" --repo Fleezyflo/fanops; done
```

If `gh pr close` returns 403, stop and paste the loop for the operator.

## Phase 1 — drift gate

```bash
python scripts/codemap_drift.py
```

Exit 0 → **no-op, done.** Exit 1 → read reasons, proceed. Exit 2 → report error.

Regen reference artifacts locally (do not commit `.codemap-cache/`):

```bash
python3 scripts/codemap_extract/ast_extract.py src > .codemap-cache/structural_index.json
python3 scripts/codemap_extract/build_graphs.py --index .codemap-cache/structural_index.json --out-dir .codemap-cache
```

## Phase 2 — surgical semantic edits

Fix only what `codemap_drift.py` flagged plus obvious cluster traces for files changed on main.

Priority: `full-trace-index.md` counts → affected `subsystem-traces/C*.md` → `system-lens-map.md`, `data.md`.

Rules:
- Every live claim needs a verified `file:line` (re-grep; ±30 line drift — trust the symbol).
- Removed code → note as removed (P11/MOL-152); never describe dead paths as live.
- Update `<!-- Generated: YYYY-MM-DD -->` on every file touched.
- Do **not** wholesale-regenerate all C1–C10 traces.

## Phase 3 — single-branch PR

```bash
MAIN_SHA=$(git rev-parse --short origin/main)
git checkout -B cursor/codemaps-sync origin/main
# … apply edits …
git add docs/CODEMAPS/
git commit -m "docs(codemaps): sync to ${MAIN_SHA} — <one-line summary>"
git push -u origin cursor/codemaps-sync --force-with-lease
```

PR policy:
- Update existing PR on `cursor/codemaps-sync` if one exists; else `gh pr create`.
- **Not a draft.** Title: `docs(codemaps): sync to ${MAIN_SHA}`.
- Cloud agent token may not `gh pr merge` (403) — leave PR ready; operator merges when CI green.

On conflict: `git fetch origin && git rebase origin/main`, re-run drift script, force-push same branch.
