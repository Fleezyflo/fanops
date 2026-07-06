#!/usr/bin/env bash
set -euo pipefail
# scripts/check-full.sh — the FULL local suite, CI parity. OPTIONAL, and NEVER hooked to git.
#
# Mirrors ci.yml's `unit` job exactly: `ruff check .` + `pytest -q -m "not integration"`. Run it when
# you want the whole gate locally before opening a PR (e.g. a broad refactor `check.sh` can't scope).
# For day-to-day work use ./scripts/check.sh (scoped, fast) — this one is minutes, by design.
#
# CI (ci.yml) remains the authoritative gate; this is a convenience, not a substitute. No git hook
# calls this, and none should — hooks enforce policy, scripts run tests, CI proves everything.

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "[check-full] .venv missing — run: python -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'" >&2
  exit 1
fi

echo "[check-full] ruff check . (whole tree)"
"$PY" -m ruff check .

echo "[check-full] pytest -q -m 'not integration' (full fast suite, CI parity)"
"$PY" -m pytest -q -m "not integration"

echo "[check-full] OK — full fast suite green (CI parity). The PR gate is still CI (unit + e2e)."
