# Brief: CI / infra lane

Read `AGENTS.md` then `.agents/_shared-guardrails.md` first (how you work). This file is WHAT you own.
You are the **CI / infra / tooling** lane.

The orchestrator spawns this lane — like every lane — as a **`fanops-worker`** background agent pointed
at this brief. Everything in `.agents/_shared-guardrails.md` still applies (worktree, TDD where code is
involved, push-after-green, orchestrator lands the merge).

## Scope — the files you own

CI, packaging, and dev-tooling: `.github/**`, `scripts/**`, `pyproject.toml`, `tests/conftest.py`,
`requirements-ci.txt`. You own **no `src/fanops` hot files**, so you are safe to run in parallel with any
source lane. If a task genuinely needs a source hot file, STOP and tell the orchestrator (it belongs to
another lane). Branch prefix **`ci/`** — required so the lane guard engages.

## Your queue — from Linear

The live wave is the Linear **project "FanOps: CI Hardening (2026 Audit)"** (see the `ci` `linear` block
in `.agents/lanes.json`). Take the next READY ticket the orchestrator assigns, one at a time, respecting
blockers and skipping already-merged tickets.

## DONE means (per ticket)

Change matches the ticket's Acceptance block; `ruff` + `pytest` (via `./scripts/check.sh`, or the full
suite when you touch CI wiring) green; PR opened to `main`; CI green; reported `MOL-xxx CI green, ready
to land`. The **orchestrator** merges.
