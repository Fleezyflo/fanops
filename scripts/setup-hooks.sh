#!/usr/bin/env bash
set -euo pipefail
# setup-hooks.sh — wire the repo policy hooks (MOL-198). Run ONCE per fresh clone/worktree.
#
# Points git at .githooks so pre-commit (secret scan + staged ruff + scoped check.sh) and pre-push
# (block main/force-push) fire. Idempotent and side-effect-free beyond this one `git config`. This is the
# explicit replacement for check.sh's old silent auto-wire (a test gate must not mutate git config).
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
if [[ "$(git config --local core.hooksPath || true)" == ".githooks" ]]; then
  echo "[setup-hooks] already wired (core.hooksPath=.githooks)"
else
  git config --local core.hooksPath .githooks
  echo "[setup-hooks] wired core.hooksPath -> .githooks (pre-commit + pre-push policy hooks now armed)"
fi
