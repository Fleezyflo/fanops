#!/usr/bin/env bash
set -euo pipefail
# setup-hooks.sh — wire the repo policy hooks (MOL-198). Run ONCE per fresh clone/worktree.
#
# Points git at .githooks so pre-commit (secret scan + staged ruff + scoped check.sh), pre-push
# (block main/force-push), and post-merge (MOL-833: regen architecture derived/ after a clean merge
# that used merge=ours on those generated paths) all fire. Also installs the built-in-style
# `merge.ours.driver` so `.gitattributes` `merge=ours` actually resolves (attribute alone is not
# enough — without a driver definition git still conflicts). Idempotent. This is the explicit
# replacement for check.sh's old silent auto-wire (a test gate must not mutate git config).
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
if [[ "$(git config --local core.hooksPath || true)" == ".githooks" ]]; then
  echo "[setup-hooks] already wired (core.hooksPath=.githooks)"
else
  git config --local core.hooksPath .githooks
  echo "[setup-hooks] wired core.hooksPath -> .githooks (pre-commit + pre-push + post-merge policy hooks now armed)"
fi

# MOL-833: `merge=ours` in .gitattributes is inert until a driver named `ours` exists.
# `true` exits 0 and leaves %A (ours) untouched — git's documented pattern for keep-ours.
if [[ "$(git config --local merge.ours.driver || true)" == "true" ]]; then
  echo "[setup-hooks] already wired (merge.ours.driver=true)"
else
  git config --local merge.ours.driver true
  echo "[setup-hooks] wired merge.ours.driver=true (architecture derived/ merge=ours now armed)"
fi
