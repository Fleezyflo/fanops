# AGENTS.md — execution protocol for coding agents

This is the **execution-protocol** layer: how to work (worktrees, TDD, push, parallelism).
For **domain invariants** (what must never break, where things live) read `CLAUDE.md` (root)
and the nested `src/fanops/CLAUDE.md`, `src/fanops/post/CLAUDE.md`, `src/fanops/studio/CLAUDE.md`,
`tests/CLAUDE.md` — they are authoritative and this file does not repeat them.

One ticket at a time, in its own git worktree, TDD-first, pushed small.
Correctness and safety beat speed. When unsure, do the safe serial thing.

---

## Non-negotiable guardrails (violating any → stop and ask)

1. NEVER touch `main` directly. NEVER force-push. NEVER rebase/force-push over a branch
   you didn't create. NEVER `git reset --hard` (it has wiped live `accounts.json`). Commit
   ONLY files you explicitly staged — `git commit` sweeps the whole index, so check `git status` first.
2. NEVER run live `fanops` publish/metrics verbs — this system is LIVE (hits Postiz / Meta Graph).
   Tests and read-only verbs only.
3. NEVER mass-reformat: no `black`, no `ruff format`. The compact one-liner house style is
   deliberate (E701/E702/E401/E501 ignored — see `pyproject.toml`). Match surrounding style.
4. NEVER raise the 60s pytest timeout to make a hang pass — a hanging test IS the bug (ledger flock deadlock).
5. NEVER wipe/reset the ledger. Schema changes are additive-with-default OR a drop-migration hop
   + `SCHEMA_VERSION` bump — never a wipe. Old ledgers must still load.
6. Do NOT invent skip-state / wait-cycle / bounded-skip machinery
   (`moments_wait_cycles`, `moments_skipped_handles`, degraded-flag partial mints). Condemned twice.
   Pending gates defer via the native `if dec is None: return led` idiom only.

## Per-ticket workflow (strict TDD, one worktree per ticket)

**A. Setup — isolated worktree off fresh main**
```bash
git fetch origin
git worktree add ../fanops-<mol-id> -b <ticket-branch> origin/main
cd ../fanops-<mol-id>
python -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'   # each worktree needs its OWN venv
git config --local core.hooksPath .githooks                          # wire the repo policy hooks
```
The hooks enforce POLICY only: `pre-commit` = secret scan + staged ruff; `pre-push` = block main/force-push.
Neither runs tests. Keep `.githooks/pre-commit` (it carries the secret scanner; `core.hooksPath` disables
the global one). Tests run via `./scripts/check.sh` (below) and CI — never at push time.

**B. Read first** — the full ticket body (exact `file:line` anchors + its "Tests" list);
every blocker it names (if a blocker is NOT merged to `origin/main`, STOP: "blocked on MOL-x");
the files it names. Anchors may have drifted ±30 lines — **trust the symbol, re-find the line.**

**C. RED** — write the ticket's named tests FIRST, run them, confirm they FAIL for the right
reason, paste the failure.

**D. GREEN** — smallest change that passes. No speculative scope. Honor every
"KEEP"/"do NOT delete"/"byte-identical" clause verbatim.

**E. REFACTOR** — tidy without behavior change; keep the one-liner style.

**F. Verify locally — run `./scripts/check.sh` before EVERY commit**
```bash
./scripts/check.sh          # scoped ruff + pytest on changed modules vs origin/main merge-base — seconds
```
Green or you're not done. This is the local gate; it is NOT enforced by a hook, so actually run it.
(Broad refactor that scoping can't cover? `./scripts/check-full.sh` for full CI parity — minutes.)

**G. Commit + push — push freely; CI is the gate.** The `pre-commit` hook runs the secret scan +
staged ruff; `pre-push` only blocks main/force-push. **No test runs at push time and there is no
`FANOPS_SKIP_PREPUSH` to set** — you already proved the change in step F, and CI proves it fully on the
PR. Do NOT rely on any push-time test gate; it doesn't exist. Conventional commit `fix(scope): …
(MOL-xxx)`, one logical change per commit.

**H. PR** — open to `main`, summarize change + test plan, wait for CI (the definitive unit + e2e gate)
to go GREEN, merge only on green. Never merge over red or conflicts — re-sync onto fresh `origin/main`
per **Re-syncing a drifted branch** below (merge, never reset/re-cut), re-run F, re-push.

**I. Cleanup** — after merge: `git worktree remove ../fanops-<mol-id>`.

## Re-syncing a drifted branch — NON-DESTRUCTIVE, MANDATORY

If `origin/main` advanced under your branch (siblings merged), you MUST re-sync **WITHOUT ever
discarding your own work**. The ONLY permitted sequence:

1. **COMMIT or STASH your work first** — NEVER re-sync a dirty tree.
   ```bash
   git add -A && git commit -m "wip(MOL-xxx): checkpoint before resync"
   # (or: git stash push -u -m "MOL-xxx wip")
   ```
2. **Fetch and MERGE main in** (never reset, never re-cut):
   ```bash
   git fetch origin && git merge origin/main
   ```
3. Resolve conflicts by hand, keeping **BOTH** your work and the incoming work.
4. If you stashed: `git stash pop`, resolve, continue.
5. `./scripts/check.sh` → push.

**ABSOLUTELY FORBIDDEN as drift recovery** (these caused work-loss incidents):
- `git reset --hard <anything>` — discards uncommitted work (has wiped live `accounts.json`)
- `git checkout -B <branch> origin/main` — re-cut throws away unpushed commits
- Deleting/abandoning the worktree and starting a fresh one for the **same ticket**
- `git push --force` / `--force-with-lease` over your own branch to "clean" it

A drift warning is **NORMAL and SAFE**. It means "siblings merged; merge them in." It is NEVER a
reason to reset. If you cannot reconcile a conflict, STOP and report
`blocked: conflict on <file> between MOL-xxx and merged main` — do NOT reset to escape it.

**PUSH EARLY, PUSH OFTEN:** commit and push every green step. Unpushed work is the only work that
can be lost. If it's on `origin`, no drift or reset can destroy it.

**Recovery before redoing:** if work was lost, try `git reflog`, `git stash list`, and
`git fsck --lost-found` in the abandoned worktree **before** rewriting. Dangling commits from a
bad reset often survive ~90 days in the reflog.

## Parallelism — allowed ONLY when 100% safe; default is SERIAL

Stacked parallel branches on **shared hot files** cause constant drift → agents panic-reset →
work-loss. Cap concurrency so drift is rare; when it happens, use the re-sync protocol above.

- **HARD CAP: at most 2 agent branches active at once**, and only if BOTH hold:
  1. **No blocker edge** between the two tickets (neither blocks the other, transitively), and
  2. **Disjoint file sets** — no common file. If both touch a shared hot file
     (`models.py`, `moments.py`, `crosspost.py`, `ledger.py`, `prompts.py`, `config.py`,
     `casting.py`, `clip.py`), they are NOT parallel-safe → run serially.
- Every branch is cut fresh off `git fetch origin` + `origin/main` at setup (step A).
- Do NOT start a ticket whose blocker is unmerged (e.g. RF-D MOL-164/MOL-169 need MOL-146 on
  `origin/main`).
- **Land branches SERIALLY in dependency order**; after each merge, the next open branch runs the
  re-sync protocol BEFORE continuing.
- Each parallel branch = own clone/worktree, own venv, own PR. NEVER two agents in one tree.
- Cloud agents get isolated VMs — the RAM/worktree crash story is local. **Git file collisions still
  apply on the shared repo**; the cap and disjoint-file rule are about merge safety, not machine RAM.
- Do NOT parallelize to hit a deadline. If only one thing is safe, do one thing and say so:
  "running serially — no 2 ready tickets are file-disjoint + blocker-free."

## What is HARD-enforced vs. advisory

- **Hard-enforced (git `.githooks/pre-push`, cannot be ignored by an agent):** direct push to
  `main` is REFUSED; force-push (non-fast-forward) to `main` is REFUSED. Override is a deliberate
  human env var (`FANOPS_ALLOW_MAIN_PUSH=1`), never something an agent sets. The pre-push hook runs
  NO tests — it is a policy guard only. Correctness is proven by `./scripts/check.sh` (local, step F)
  and by CI (authoritative, every PR), not at push time.
- **Advisory (this file — no git hook exists to enforce it):** `git reset --hard`, force-push to a
  FEATURE branch, and "commit only staged files". Git has no `pre-reset` hook, so these rely on the
  agent obeying the guardrails above. Treat them as absolute anyway; they are the exact operations
  that caused past data loss.

## After each merge

Post one line: `MOL-xxx merged, CI green, worktree removed`.
Stop and ask if: a blocker isn't merged, a ticket's anchors no longer match the code,
CI is red for a reason you can't fix quickly, or any guardrail would be violated.
