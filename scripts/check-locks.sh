#!/usr/bin/env bash
set -euo pipefail
# check-locks.sh — CI drift guard (MOL-195 / CI-15). Fails a PR that changes pyproject.toml dependencies
# without regenerating the hashed locks (requirements/ci-*.txt). Cheap: a git-diff check, no dep resolution.
#
# Usage: check-locks.sh <base-ref>   (e.g. origin/main, or the PR base sha)
base="${1:-origin/main}"
changed() { git diff --name-only "${base}...HEAD" -- "$@"; }

# Did the PR touch the dependency-bearing parts of pyproject.toml?
pyproj_dep_change="$(git diff "${base}...HEAD" -- pyproject.toml \
  | rg -n '^\+' \
  | rg -i 'dependencies|optional-dependencies|^\+\s*"[a-zA-Z0-9_.\-]+[<>=~!]' || true)"

if [[ -z "$pyproj_dep_change" ]]; then
  echo "[check-locks] pyproject.toml dependencies unchanged — locks not required to move. OK."
  exit 0
fi

locks_changed="$(changed requirements/ci-unit.txt requirements/ci-e2e.txt)"
if [[ -n "$locks_changed" ]]; then
  echo "[check-locks] pyproject deps changed AND locks regenerated — OK."
  exit 0
fi

echo "[check-locks] REFUSED: pyproject.toml dependencies changed but requirements/ci-*.txt were NOT regenerated." >&2
echo "[check-locks] Run ./scripts/lock-deps.sh (linux/py3.12) and commit the updated locks." >&2
exit 1
