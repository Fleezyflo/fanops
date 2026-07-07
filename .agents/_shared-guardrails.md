# Shared lane guardrails (read after `AGENTS.md`, before your lane brief)

Every FanOps lane agent (`picking`, `publish`, `rfd`, `ci`) obeys THIS file. It is the DRY home for the
how-you-work rules that used to be copy-pasted into each brief. Your lane brief (`.agents/<lane>-agent.md`)
carries only what is UNIQUE to your lane. Precedence: system/user instructions → `AGENTS.md` → this file →
your lane brief.

## Where your work comes from — Linear, at runtime (never a frozen list)

You do **not** work from a hard-coded ticket list — it goes stale the moment a ticket merges. The
`fanops-orchestrator` pulls the next **READY** ticket for your lane from **Linear** (team *Molham homsi*)
and hands you its **MOL id**. "Ready" = status `Todo`/`Backlog`, not `Done`/`Canceled`/`Duplicate`/`In
Review`, no unmet blocker. Your lane→Linear mapping (label/project) lives in `.agents/lanes.json`.

## One ticket, one worktree, TDD, small pushes

```bash
git fetch origin
# Branch MUST start with your lane prefix (publish/ picking/ rfd/ ci/) so the lane guard engages.
git worktree add ../fanops-<mol-id> -b <lane>/<mol-id>-<slug> origin/main
cd ../fanops-<mol-id>
python3 -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'   # each worktree gets its OWN venv
git config --local core.hooksPath .githooks                            # wire the policy hooks
```

RED → GREEN → REFACTOR. Run `./scripts/check.sh` before EVERY commit. **Push after every green check** —
unpushed work is the only work that can be lost. Conventional commits `fix(scope): … (MOL-xxx)`, one
logical change each; commit only files you staged.

## You do NOT merge — the orchestrator lands, serially

Land authority is centralized to avoid two lanes merging at once (a drift race). Your finish line is:
`./scripts/check.sh` green → push → open the PR to `main` → wait for CI green → **report to the
orchestrator: `MOL-xxx CI green, ready to land`**. The orchestrator merges PRs one at a time in
dependency order and, after each merge, tells the remaining lanes to re-sync. Do **not** run
`gh pr merge` yourself.

## Stay in your lane — mechanically enforced

Edit only your lane's files. The **hot files** in `.agents/lanes.json` are owned per-lane; a branch that
edits a hot file owned by another lane is **refused** by `scripts/lane_guard.py` at pre-push and by the
`lane-guard` CI job. If you genuinely need a file another lane owns, STOP and tell the orchestrator — do
not edit `lanes.json` to grab it unilaterally.

## Drift is normal and SAFE — re-sync, never reset

If `origin/main` advanced under you: `git add -A && git commit` (or stash) → `git fetch origin && git
merge origin/main` → resolve keeping BOTH sides → `./scripts/check.sh` → push. **Never** `git reset
--hard`, **never** `git checkout -B … origin/main`, **never** abandon the worktree for the same ticket,
**never** force-push. A conflict you can't reconcile → STOP and report `blocked: conflict on <file>`.

## Stop conditions

Stop and report (do not guess) if: your lane's Linear queue has no ready ticket; a blocker isn't on
`origin/main`; a ticket's `file:line` anchors no longer match the code; CI is red for a reason you can't
fix quickly; or any `AGENTS.md` guardrail would be violated.
