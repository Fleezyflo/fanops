#!/usr/bin/env bash
set -euo pipefail
# scripts/check.sh — the EXPLICIT local gate. Run this BEFORE every commit.
#
# Scoped, fast: lints only the .py you changed and runs only the tests that cover them, diffed against
# the origin/main merge-base. Seconds on a small change, not minutes. This is NOT a git hook and is NOT
# authoritative — CI (ci.yml) runs the full suite on every PR and is the sole gate. This just catches
# the obvious break before you push, so CI rarely comes back red.
#
# Exit non-zero on any ruff or pytest failure. Full-suite parity: ./scripts/check-full.sh
#
# Usage:  ./scripts/check.sh                    # scope = changes vs origin/main merge-base
#         BASE=origin/main ./scripts/check.sh   # override the diff base

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "[check] .venv missing — run: python -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'" >&2
  exit 1
fi

# Self-heal the ONE piece of infra that is otherwise on trust: the policy hooks are inert until
# core.hooksPath points at .githooks, and a fresh clone/worktree does NOT set it. Rather than document
# "remember to wire the hooks" (a markdown request an agent skips), wire it here — check.sh runs before
# every commit, so the main-push guard is armed by the time you can push. Idempotent.
if [[ "$(git config --local core.hooksPath || true)" != ".githooks" ]]; then
  git config --local core.hooksPath .githooks
  echo "[check] wired core.hooksPath -> .githooks (policy hooks were inert; now armed)"
fi

# Diff base: the merge-base with origin/main (fall back to HEAD~1, then empty tree, so a fresh repo works).
BASE="${BASE:-}"
if [[ -z "$BASE" ]]; then
  if git rev-parse --verify --quiet origin/main >/dev/null; then
    BASE="$(git merge-base origin/main HEAD 2>/dev/null || echo HEAD)"
  elif git rev-parse --verify --quiet HEAD~1 >/dev/null; then
    BASE="HEAD~1"
  else
    BASE="$(git hash-object -t tree /dev/null)"   # empty tree: everything counts as changed
  fi
fi

# Changed files = committed-since-base + staged + unstaged, restricted to .py.
changed_py() {
  { git diff --name-only --diff-filter=ACMR "$BASE" -- '*.py'
    git diff --cached --name-only --diff-filter=ACMR -- '*.py'
    git diff --name-only --diff-filter=ACMR -- '*.py'
  } | sort -u
}

mapfile -t CHANGED < <(changed_py)
if [[ ${#CHANGED[@]} -eq 0 ]]; then
  echo "[check] no changed .py vs $BASE — nothing to lint or test. (CI still runs the full suite on PR.)"
  exit 0
fi

echo "[check] base=$BASE  changed .py files: ${#CHANGED[@]}"
printf '        %s\n' "${CHANGED[@]}"

# 1) Scoped ruff — only the changed files.
echo "[check] ruff (scoped)"
"$PY" -m ruff check "${CHANGED[@]}"

# 2) Scoped pytest — changed test files + convention/override map (scripts/check_scope.py handles
# studio/, post/, and alternate test names like test_studio_actions.py).
mapfile -t TESTS < <("$PY" "$ROOT/scripts/check_scope.py" "${CHANGED[@]}")

if [[ ${#TESTS[@]} -eq 0 ]]; then
  echo "[check] no matching test files for the changed modules — ruff passed; skipping pytest."
  echo "[check] (no scoped test mapping — only proven by the full suite in CI; see scripts/check_scope.py)"
  exit 0
fi
echo "[check] pytest (scoped): ${#TESTS[@]} file(s)"
printf '        %s\n' "${TESTS[@]}"
"$PY" -m pytest -q -m "not integration and not slow" "${TESTS[@]}"

echo "[check] OK — scoped ruff + tests green. Push freely; CI is the authoritative full gate."
