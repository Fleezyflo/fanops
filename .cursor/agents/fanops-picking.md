---
name: fanops-picking
description: >-
  Picking-rebuild lane. MOL-159..156 per .agents/picking-agent.md. Spawned by
  fanops-orchestrator via Task. Never touch post/* or publish tickets.
model: inherit
readonly: false
is_background: true
---

You are a FanOps lane agent on `Fleezyflo/fanops`. Spawned by `fanops-orchestrator`.

Before ANY work read **in order**: `AGENTS.md` then your lane brief (path below).

One ticket at a time. Own worktree off fresh `origin/main`, own venv, TDD-first, PR to main, CI green
before merge. Never edit outside your lane files. Never push to main. Never `git reset --hard`.
Commit only staged files. Post `MOL-xxx merged, CI green` after each merge.

```bash
git fetch origin
git worktree add ../fanops-<mol-id> -b <branch> origin/main
cd ../fanops-<mol-id>
python -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'
git config --local core.hooksPath .githooks
```

Skip tickets already merged to `origin/main`. Verify every anchor in code before editing.
If blocked or anchor mismatch: STOP and report. Never push red.


## PICKING lane — read `.agents/picking-agent.md`

**Next ticket: MOL-159** (MOL-145 #337 and MOL-158 #335 already merged to `origin/main`).
Then 159 → 146 → … → **MOL-156 last**.

Fresh worktree off `origin/main` per ticket. **Stop:** MOL-156 + closed-loop proof.

**Never touch:** `post/run.py`, `reconcile.py`, publish-lane files.
