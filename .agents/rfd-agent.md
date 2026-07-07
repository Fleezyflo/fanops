# Brief: RF-D ROOT-FIX AGENT

You are the **RF-D root-fix agent**. Read `AGENTS.md` (repo root) first — the shared
how-you-work contract (worktrees, TDD, no main-push, parallelism cap). This file is WHAT you do.

## Your tickets ONLY, in order

MOL-166, MOL-167, MOL-168, MOL-164, MOL-169

Touch NOTHING else. The picking agent owns the P/A/S tickets (MOL-142..179); the publish agent
owns MOL-112..128. You WILL collide on shared files if you stray.

## Sequencing

- MOL-164 and MOL-169 blocker-depend on MOL-146 (P5). Do NOT start either until MOL-146 is
  merged to origin/main. Until then, work only MOL-166, MOL-167, MOL-168.
- MOL-166 and MOL-167 touch moments.py / prompts.py. If the picking agent has an open PR on
  those files, WAIT — do not race it.

## Where to STOP

Stop when MOL-169 is merged green. Never touch a picking or publish ticket.

## DONE means

Each ticket merged on green CI to its Acceptance block. One text-screen chokepoint at the
responder (MOL-166); no request_id/source_id echo (MOL-167); caption platform request-authoritative
(MOL-168); Account.handle canonical at the write boundary (MOL-164); overlap owns one home (MOL-169).
Per-ticket TDD; run `./scripts/check.sh` (scoped ruff + tests) locally before each commit; PR merged
on green CI (the authoritative gate — `unit` + `e2e`). Git hooks run NO tests; `check.sh` + CI ARE the gate.
