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
# Branch name: your platform's per-ticket name is fine (cursor/mol-<id>-…, fix/mol-<id>-…) — CI resolves
# your lane from the MOL id via Linear. A `<lane>/…` prefix (publish/ picking/ rfd/ ci/) additionally
# engages the OFFLINE pre-push guard. Either way the MOL id MUST be in the branch or PR title.
git worktree add ../fanops-<mol-id> -b <lane>/<mol-id>-<slug> origin/main
cd ../fanops-<mol-id>
python3 -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'   # each worktree gets its OWN venv
git config --local core.hooksPath .githooks                            # wire the policy hooks
```

Write the ticket's tests WITH the change, but NEVER execute them locally — tests run ONLY in GitHub
CI on your PR (operator rule: parallel wave suites crash the machine; the gate refuses `pytest`).
Run `./scripts/check.sh` (scoped lint + test-mapping check) before EVERY commit. **Push after every
green check** — unpushed work is the only work that can be lost. Conventional commits
`fix(scope): … (MOL-xxx)`, one logical change each; commit only files you staged.

## You do NOT merge — the orchestrator lands, serially

Land authority is centralized to avoid two lanes merging at once (a drift race). Your finish line is:
`./scripts/check.sh` green → push → open the PR to `main` → wait for CI green → **report to the
orchestrator: `MOL-xxx CI green, ready to land`**. The orchestrator merges PRs one at a time in
dependency order; when `origin/main` moves under you after a land, re-sync per **Drift** below. Do
**not** run `gh pr merge` yourself — during a wave the orchestration land-gate refuses any merge of an
unverified unit, and the serial landing order only exists if exactly one actor (the orchestrator) merges.

## Stay in your lane — mechanically enforced

Edit only your lane's files. The **hot files** in `.agents/lanes.json` are owned per-lane; enforcement is
two-layer:
- **`scripts/lane_guard.py`** (pre-push + `lane-guard` CI job) refuses a change that edits a hot file
  owned by ANOTHER lane. Your lane is read from a `<lane>/` prefix or from your branch's MOL id via Linear.
- **`scripts/pr_collision_guard.py`** (CI) refuses your PR if a hot file it touches is ALSO open in
  another PR to `main`. So even two same-lane tickets can't silently race the same hot file — land one,
  re-sync the other.

If you genuinely need a file another lane owns, STOP and tell the orchestrator — do not edit `lanes.json`
to grab it unilaterally.

## Drift is normal and SAFE — re-sync, never reset

If `origin/main` advanced under you: `git add -A && git commit` (or stash) → `git fetch origin && git
merge origin/main` → resolve keeping BOTH sides → `./scripts/check.sh` → push. **Never** `git reset
--hard`, **never** `git checkout -B … origin/main`, **never** abandon the worktree for the same ticket,
**never** force-push. A conflict you can't reconcile → STOP and report `blocked: conflict on <file>`.

## Stop conditions

Stop and report (do not guess) if: your lane's Linear queue has no ready ticket; a blocker isn't on
`origin/main`; a ticket's `file:line` anchors no longer match the code; CI is red for a reason you can't
fix quickly; or any `AGENTS.md` guardrail would be violated.
