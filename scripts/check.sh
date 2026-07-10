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

# MOL-198: check.sh no longer MUTATES git config (a test gate silently changing `core.hooksPath` was a
# footgun). It only WARNS when the policy hooks aren't wired; wiring is a one-time explicit step via
# `./scripts/setup-hooks.sh` (see AGENTS.md worktree setup).
if [[ "$(git config --local core.hooksPath || true)" != ".githooks" ]]; then
  echo "[check] WARNING: policy hooks not wired — run: ./scripts/setup-hooks.sh  (one-time, idempotent)" >&2
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

# 1a) MOL-292: config env structural invariants (grep gates — fail closed; skip in minimal sandboxes).
if [[ -f src/fanops/config.py ]]; then
  echo "[check] config structural gates (MOL-292)"
  ! rg 'os\.getenv' src/fanops/config.py
  ! rg 'return Settings\(\)' src/fanops/
  rg 'Settings\.runtime_load|Settings\.strict_validate' src/fanops/ -q
fi

# 1b) Fail closed on changed src modules with no scoped test mapping (false-confidence hole).
mapfile -t ORPHANS < <("$PY" "$ROOT/scripts/check_scope.py" --orphans "${CHANGED[@]}")
if [[ ${#ORPHANS[@]} -gt 0 ]]; then
  if [[ "${FANOPS_CHECK_ALLOW_NO_TESTS:-}" == "1" ]]; then
    echo "[check] WARNING: changed src with no scoped test (FANOPS_CHECK_ALLOW_NO_TESTS=1):"
    printf '        %s\n' "${ORPHANS[@]}"
  else
    echo "[check] FAIL: changed src modules have no scoped test — add tests or set FANOPS_CHECK_ALLOW_NO_TESTS=1:" >&2
    printf '        %s\n' "${ORPHANS[@]}" >&2
    exit 1
  fi
fi

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
