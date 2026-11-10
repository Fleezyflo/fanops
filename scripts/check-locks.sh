#!/usr/bin/env bash
set -euo pipefail
# check-locks.sh — CI drift guard (MOL-195 / CI-15). Fails a PR that changes pyproject.toml dependencies
# without regenerating the hashed locks (requirements/ci-*.txt). Cheap: a git-diff check, no dep resolution.
# Only what a lock is built from counts: the base dependencies plus the extras each lock's own pip-compile
# header names. A change to an extra no lock is built from ([asr] is nightly-only, MOL-197) has no lock to
# regenerate, so it passes. A lock with no readable header, or built with --all-extras, makes every extra count.
#
# Usage: check-locks.sh <base-ref>   (e.g. origin/main, or the PR base sha)
base="${1:-origin/main}"
locks=(requirements/ci-unit.txt requirements/ci-e2e.txt)
changed() { git diff --name-only "${base}...HEAD" -- "$@"; }

# Did the PR touch the dependency-bearing parts of pyproject.toml?
#
# PARSE, don't grep. Diff-regexing this is wrong in a way that reads as working: adding a package to
# an EXISTING inline extra (`dev = ["pytest>=8.0", ..., "jsonschema>=4.0"]`) produces an added line
# beginning `dev = [`, which matches neither "contains the word dependencies" nor "is a bare quoted
# requirement" — so the guard reports unchanged while the deps changed, and a new package ships
# against stale hashed locks. Comparing the PARSED tables is exact.
# tomllib is stdlib on the 3.12 runner; setup-python runs before this step.
pyproj_dep_change="$(python3 - "$base" "${locks[@]}" <<'PY' || true
import re, subprocess, sys, tomllib

def norm(name: str) -> str:            # PEP 685: extra names compare normalized
    return re.sub(r"[-_.]+", "-", name).lower()

def lock_extras(path: str):
    """The extras named on the lock's own pip-compile header; None when every extra counts."""
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return None                    # no readable lock -> every extra counts
    with fh:
        for line in fh:
            if not line.startswith("#"):
                break                  # past the header
            cmd = line[1:].split()
            if cmd[:1] == ["pip-compile"]:
                if "--all-extras" in cmd:
                    return None
                return {norm(t.split("=", 1)[1]) for t in cmd if t.startswith("--extra=")}
    return None                        # no pip-compile header -> every extra counts

def dep_view(text: str, locked) -> dict:
    proj = tomllib.loads(text).get("project", {})
    return {"deps": sorted(proj.get("dependencies") or []),
            "extras": {k: sorted(v or []) for k, v in (proj.get("optional-dependencies") or {}).items()
                       if locked is None or norm(k) in locked}}

base, lock_paths = sys.argv[1], sys.argv[2:]
per_lock = [lock_extras(p) for p in lock_paths]
locked = None if None in per_lock else set().union(*per_lock)
old = subprocess.run(["git", "show", f"{base}:pyproject.toml"], capture_output=True, text=True)
if old.returncode != 0:            # base tree unreadable -> FAIL CLOSED and demand the locks
    print("pyproject.toml at base unreadable — cannot prove dependencies unchanged")
    raise SystemExit(0)
with open("pyproject.toml", encoding="utf-8") as fh:
    new_text = fh.read()
if dep_view(old.stdout, locked) != dep_view(new_text, locked):
    print("pyproject.toml [project] dependencies / optional-dependencies differ from base")
PY
)"

if [[ -z "$pyproj_dep_change" ]]; then
  echo "[check-locks] pyproject.toml locked dependencies unchanged — locks not required to move. OK."
  exit 0
fi

locks_changed="$(changed "${locks[@]}")"
if [[ -n "$locks_changed" ]]; then
  echo "[check-locks] pyproject deps changed AND locks regenerated — OK."
  exit 0
fi

echo "[check-locks] REFUSED: pyproject.toml dependencies changed but requirements/ci-*.txt were NOT regenerated." >&2
echo "[check-locks] Run ./scripts/lock-deps.sh (linux/py3.12) and commit the updated locks." >&2
exit 1
