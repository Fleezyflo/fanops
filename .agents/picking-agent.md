# Brief: PICKING lane

Read `AGENTS.md` then `.agents/_shared-guardrails.md` first (how you work). This file is WHAT you own.
You are the **clip/moment/casting generation-core** lane.

## Scope — the files you own

Your hot files (exclusive unless noted) are defined under `picking` in `.agents/lanes.json`:
`models.py`, `crosspost.py`, `ledger.py`, `casting.py`, `clip.py`, and — **shared with `rfd`,
time-coordinated by the orchestrator** — `moments.py`, `prompts.py`. Touch nothing owned by `publish`
(`post/*`, `reconcile.py`, `config.py`, `studio/views_common.py`). The lane guard enforces this.

## Your queue — from Linear, not a list

The orchestrator hands you the next **READY** picking ticket (MOL id) from Linear (see the `picking`
`linear` block in `.agents/lanes.json`). Work exactly one at a time. **Respect blockers**: never start a
ticket whose blocker isn't yet merged to `origin/main` — report `blocked on MOL-x` and stop. Skip any
ticket already merged.

## DONE means (per ticket)

TDD-first (write the ticket's named tests RED, then GREEN), `ruff` + scoped `pytest` green via
`./scripts/check.sh`, PR opened to `main`, CI green, and you have reported `MOL-xxx CI green, ready to
land`. The **orchestrator** merges — you do not. A capstone/closed-loop ticket is done only when its
named proof (e.g. crosspost→approve→publish[dryrun]→reconcile→Meta-Graph insight→lift back on the Post)
is green with output.
