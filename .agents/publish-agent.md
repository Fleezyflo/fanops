# Brief: PUBLISH lane

Read `AGENTS.md` then `.agents/_shared-guardrails.md` first (how you work). This file is WHAT you own.
You are the **publish-resilience** lane (publish/schedule/reconcile subsystem — boundary mirrors
`src/fanops/post/CLAUDE.md`).

## Scope — the files you own

Your hot files, defined under `publish` in `.agents/lanes.json`: `post/run.py`, `post/postiz.py`,
`post/zernio.py`, `post/__init__.py`, `reconcile.py`, `config.py`, `studio/views_common.py` (plus
`.gitignore`). Do **not** edit the generation-core hot files (`models.py`, `crosspost.py`, `ledger.py`,
`casting.py`, `clip.py`, `moments.py`, `prompts.py`) — those belong to `picking`/`rfd`. The lane guard
enforces this.

## Your queue — from Linear, not a list

The orchestrator hands you the next **READY** publish ticket (MOL id) from Linear (see the `publish`
`linear` block in `.agents/lanes.json`). Work one at a time; respect blockers (report `blocked on MOL-x`
and stop); skip already-merged tickets. **Some publish items are LIVE operator actions, not code** — if
the orchestrator flags a ticket as operator-only, leave it for the human and move on.

## DONE means (per ticket)

TDD-first, `ruff` + scoped `pytest` green via `./scripts/check.sh`, PR opened to `main`, CI green, and
you have reported `MOL-xxx CI green, ready to land`. The **orchestrator** merges — you do not. Honor each
ticket's Acceptance block (e.g. a transient publish failure retries then parks `needs_reconcile`, not
terminal `failed`; a 4xx stays terminal; no double-submit on retry).
