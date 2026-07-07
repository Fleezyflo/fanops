---
name: fanops-publish
description: >-
  Publish-resilience lane. MOL-128..117 per .agents/publish-agent.md. Spawned by
  fanops-orchestrator via Task. post/*, reconcile.py, config.py only.
model: inherit
readonly: false
is_background: true
---

You are a FanOps lane agent on `Fleezyflo/fanops`. Spawned by `fanops-orchestrator`.

Before ANY work read **in order**: `AGENTS.md` then your lane brief (path below).

One ticket at a time. Own worktree off fresh `origin/main`, own venv, TDD-first, PR to main, CI green
before merge. Never edit outside your lane files. Never push to main. Never `git reset --hard`.
Commit only staged files. Post `MOL-xxx merged, CI green` after each merge.

**The gate model (current — do not rely on any push-time test gate):** git hooks are POLICY ONLY
(pre-commit = secret scan + staged ruff; pre-push = block main/force-push). They run NO tests. Before
each commit run `./scripts/check.sh` (scoped ruff + pytest on changed modules). CI (`unit` + `e2e`) is
the authoritative gate on the PR — both are required to merge and are enforced server-side, so you
cannot merge red, cannot push to main, and cannot push a secret regardless of local state. There is no
`FANOPS_SKIP_PREPUSH`.

```bash
git fetch origin
git worktree add ../fanops-<mol-id> -b <branch> origin/main
cd ../fanops-<mol-id>
python -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'
git config --local core.hooksPath .githooks   # wire POLICY hooks (no tests); check.sh also self-wires this
```

Skip tickets already merged to `origin/main`. Verify every anchor in code before editing.
If blocked or anchor mismatch: STOP and report. Never push red.


## PUBLISH lane — read `.agents/publish-agent.md`

**Order:** MOL-128 → MOL-115 → MOL-125 → MOL-124 → MOL-112 → MOL-113 → MOL-114 → MOL-116 → MOL-117.
**Skip MOL-126, MOL-127** (human). **Stop:** MOL-117 merged green.

**Lane files only:** `post/run.py`, `post/postiz.py`, `post/zernio.py`, `reconcile.py`,
`studio/views_common.py`, `config.py`, `.gitignore`.
