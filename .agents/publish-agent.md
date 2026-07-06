# Brief: PUBLISH-RESILIENCE AGENT

You are the **publish-resilience agent**. Read `AGENTS.md` (repo root) first — the shared
how-you-work contract (worktrees, TDD, no main-push, parallelism cap). This file is WHAT you do.

## Your tickets ONLY, in order

MOL-128, MOL-115, MOL-125, MOL-124, MOL-112, MOL-113, MOL-114, MOL-116, MOL-117

Touch NOTHING else. The picking agent owns MOL-142..179; the RF-D agent owns
MOL-166/167/168/164/169. Your files: post/run.py, post/postiz.py, post/zernio.py, reconcile.py,
studio/views_common.py, config.py, .gitignore. Do NOT edit moments.py, models.py, crosspost.py,
prompts.py, casting.py, clip.py, ledger.py.

## SKIP — do NOT execute (LIVE operator actions, not code)

MOL-126, MOL-127. Leave them for the human.

## Sequencing

MOL-128 FIRST (security: purge the live-key .env.bak + gitignore .env/.env.*/*.bak + commit the
v4/R2 work). Then the rest in the order above.

## Where to STOP

Stop when MOL-117 is merged green. Never touch a picking or RF-D ticket, never run MOL-126/127.

## DONE means

Each ticket merged on green CI to its Acceptance block. A transient publish failure retries then
parks needs_reconcile (not terminal failed); a 4xx stays terminal; no double-submit on retry; the
live-key backup is gone and gitignored; the Postiz banner distinguishes idle from broken. Per-ticket
TDD, ruff + scoped pytest green, PR merged.
