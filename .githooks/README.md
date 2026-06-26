# `.githooks/` — repo-owned git hooks (opt-in)

## Why this exists

The machine-global ECC pre-push hook (`~/.codex/git-hooks/pre-push`, wired via a global
`core.hooksPath`) runs the **entire** `pytest -q` suite — codebase-wide, including integration
markers — under whatever `python`/`pytest` is on `PATH`. On FanOps that PATH python is a system
interpreter **without** flask/werkzeug, so the studio tests fail to even collect, and running the
full suite on every push hammered a 16 GB host. This directory holds the **correct** fast gate.

## What `pre-push` does

Runs only `ruff check .` + the **fast unit suite** (`pytest -q -m "not integration"`) under the
project's `.venv` — the same gate as CI's `unit` job. Seconds, not minutes; correct interpreter.

## Two ways to use it

### A. Opt in to the repo hook (re-points `core.hooksPath` for THIS repo)

```bash
git config --local core.hooksPath .githooks
```

⚠️ **Security caveat:** re-pointing `core.hooksPath` means git looks **only** in `.githooks/` for
this repo, so the machine-global **`pre-commit` secret scanner** (which blocks committing OpenAI /
GitHub / AWS keys and private-key blocks) **stops firing**. If you opt in, you MUST also port it:

```bash
cp ~/.codex/git-hooks/pre-commit .githooks/pre-commit   # keep secret-blocking on commit
```

### B. Leave the global hooks in place, bypass only the heavy pre-push (recommended for automation)

The host-crash lives **only** in the global pre-push hook, which honors a pre-push-scoped env var.
Run the fast suite by hand, then push with the bypass — the global **pre-commit secret scanner stays
fully live**:

```bash
.venv/bin/python -m ruff check . && .venv/bin/python -m pytest -q -m "not integration"
ECC_SKIP_PREPUSH=1 git push -u origin <branch>
```

This is what the autonomous remediation loop uses: it never disables the secret scanner; it only
skips the one hook that runs the codebase-wide suite.

## Bypass the repo hook (option A) once

```bash
FANOPS_SKIP_PREPUSH=1 git push
```
