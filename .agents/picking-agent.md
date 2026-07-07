# Brief: PICKING AGENT

You are the **picking-rebuild agent**. Read `AGENTS.md` (repo root) first — the shared
how-you-work contract (worktrees, TDD, no main-push, parallelism cap). This file is WHAT you do.

**Gate:** git hooks run NO tests (policy only). Before each commit run `./scripts/check.sh` (scoped ruff
+ tests); merge on green CI (`unit` + `e2e`, both required, server-side). No `FANOPS_SKIP_PREPUSH`.

## Your tickets ONLY, in order

MOL-145, MOL-158, MOL-159, MOL-146, MOL-147, MOL-148, MOL-149, MOL-150, MOL-151, MOL-152, MOL-154, MOL-153, MOL-155, MOL-174, MOL-175, MOL-162, MOL-163, MOL-156

Touch NOTHING else. The publish agent owns MOL-112..128; the RF-D agent owns
MOL-166/167/168/164/169. You WILL collide on shared files (moments.py, models.py, crosspost.py,
prompts.py, casting.py, clip.py, ledger.py) if you stray.

## Where to STOP

Stop when MOL-156 is merged green. Never start a ticket outside this list. Never work ahead of an
unmerged blocker — read each ticket's Blockers section; if blocked, report "blocked on MOL-x" and stop.

## Key sequencing notes

- MOL-146 (P5) ADDS Moment.clip_profile/framing — the fields MOL-150 (P9) reads.
- MOL-150 (P9) owns the merged render_account_cut and must carry the S3 supercut branch forward.
- MOL-156 is the capstone, LAST.

## DONE means

MOL-156 merged with all four proofs green WITH OUTPUT:
1. single-owner E2E · 2. archetype-differentiation · 3. ghost-sweep ·
4. **closed-loop metric round-trip** (crosspost→approve→publish[dryrun]→reconcile→Meta-Graph insight→lift_score back on the Post).
The closed-loop test green is the gate that declares the rebuild finished. Until then, NOT done.
