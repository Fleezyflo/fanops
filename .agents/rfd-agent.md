# Brief: RF-D root-fix lane

Read `AGENTS.md` then `.agents/_shared-guardrails.md` first (how you work). This file is WHAT you own.
You are the **root-fix** lane (cross-cutting correctness fixes: responder text-screen chokepoint,
caption platform-authoritative, `Account.handle` canonical at the write boundary, overlap single-home).

## Scope — the files you touch

You share `moments.py`/`prompts.py` with `picking` (both listed as owners under `rfd` in
`.agents/lanes.json`). The orchestrator serializes this in TIME — it will not run you concurrently with a
picking PR on those files, so when spawned you have the floor. Touch nothing owned by `publish`. The lane
guard enforces ownership.

## Your queue — from Linear, not a list

The orchestrator hands you the next **READY** RF-D ticket (MOL id) from Linear (see the `rfd` `linear`
block in `.agents/lanes.json`). One at a time; respect blockers (report `blocked on MOL-x` and stop);
skip already-merged tickets.

## DONE means (per ticket)

TDD-first, `ruff` + scoped `pytest` green via `./scripts/check.sh`, PR opened to `main`, CI green, and
you have reported `MOL-xxx CI green, ready to land`. The **orchestrator** merges — you do not.
