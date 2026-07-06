# Brief: PICKING AGENT

You are the **picking-rebuild agent**. Read `AGENTS.md` (repo root) first — that is the
shared how-you-work contract (worktrees, TDD, no main-push, parallelism cap). This file is
WHAT you do, in what order, where to stop, and when you're done.

## Your tickets ONLY (Linear project "FanOps: Per-Persona Independent Picking")

Do NOT touch any ticket not on this list. Another agent owns publish-resilience
(MOL-112..128) and the RF-D refactors (MOL-164/166/167/168/169) — you WILL collide on
shared files (moments.py, models.py, crosspost.py, prompts.py, casting.py, clip.py, ledger.py).

### Order (each ticket's own "Blockers" section is authoritative; this is the through-line)

- ✅ MOL-179 (S0) · MOL-170 (A1) · MOL-142 (P1) · MOL-171/172/173 (A2+A3+A4 atomic) · MOL-176 (S1) · MOL-144 (P3) · MOL-177 (S2) · MOL-178 (S3) · MOL-157 (P4a) — MERGED
- ▶ MOL-145 (P4) → MOL-158 (P4b) → MOL-159 (P4c)
- MOL-146 (P5 — ADDS Moment.clip_profile/framing, the field P9 reads)
- MOL-147 (P6) → MOL-148 (P7)
- MOL-149 (P8) → MOL-150 (P9 — owns merged render_account_cut, carries the S3 supercut branch forward)
- MOL-151 (P10) → MOL-152 (P11) → MOL-154 (P12) → MOL-153 (P13) → MOL-155 (P14)
- MOL-174 (A5) · MOL-175 (A6)
- MOL-162 (P4f docs) · MOL-163 (P4g docs)
- **MOL-156 (P15) — LAST**

## Where to STOP

Stop when MOL-156 (P15) is merged green. Never start a ticket outside this list. Never work
ahead of an unmerged blocker — if blocked, report "blocked on MOL-x" and stop.

## DONE means

MOL-156 (P15) merged with all four proofs green WITH OUTPUT:
1. single-owner E2E · 2. archetype-differentiation · 3. ghost-sweep ·
4. **closed-loop metric round-trip** (crosspost→approve→publish[dryrun]→reconcile→Meta-Graph insight→lift_score back on the Post).
The closed-loop test green is the gate that declares the rebuild finished. Until then, NOT done.
