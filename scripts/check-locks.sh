#!/usr/bin/env bash
set -euo pipefail
# check-locks.sh — CI drift guard (MOL-195 / CI-15). Fails a PR that changes pyproject.toml dependencies
# without regenerating the hashed locks (requirements/ci-*.txt). Cheap: a git-diff check, no dep resolution.
#
# Usage: check-locks.sh <base-ref>   (e.g. origin/main, or the PR base sha)
base="${1:-origin/main}"
changed() { git diff --name-only "${base}...HEAD" -- "$@"; }

# Did the PR touch the dependency-bearing parts of pyproject.toml?
#
# PARSE, don't grep. Diff-regexing this is wrong in a way that reads as working: adding a package to
# an EXISTING inline extra (`dev = ["pytest>=8.0", ..., "jsonschema>=4.0"]`) produces an added line
# beginning `dev = [`, which matches neither "contains the word dependencies" nor "is a bare quoted
# requirement" — so the guard reports unchanged while the deps changed, and a new package ships
# against stale hashed locks. Comparing the PARSED tables is exact.
# tomllib is stdlib on the 3.12 runner; setup-python runs before this step.
pyproj_dep_change="$(python3 - "$base" <<'PY' || true
import subprocess, sys, tomllib

def dep_view(text: str) -> dict:
    proj = tomllib.loads(text).get("project", {})
    return {"deps": sorted(proj.get("dependencies") or []),
            "extras": {k: sorted(v or []) for k, v in (proj.get("optional-dependencies") or {}).items()}}

base = sys.argv[1]
old = subprocess.run(["git", "show", f"{base}:pyproject.toml"], capture_output=True, text=True)
if old.returncode != 0:            # base tree unreadable -> FAIL CLOSED and demand the locks
    print("pyproject.toml at base unreadable — cannot prove dependencies unchanged")
    raise SystemExit(0)
with open("pyproject.toml", encoding="utf-8") as fh:
    new_text = fh.read()
if dep_view(old.stdout) != dep_view(new_text):
    print("pyproject.toml [project] dependencies / optional-dependencies differ from base")
PY
)"

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
