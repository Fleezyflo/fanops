#!/usr/bin/env bash
set -euo pipefail
# scripts/check-full.sh — the FULL local suite, CI parity. OPTIONAL, and NEVER hooked to git.
#
# Default: fast local parity — `ruff check .` + `FANOPS_REQUIRE_STUDIO=1 pytest -q -m "not integration and not slow"`
# (skips the slow cross-face UNIT proofs). Set CHECK_FULL_SLOW=1 for full CI unit parity (includes slow).
# Run it when you want the whole gate locally before opening a PR (e.g. a broad refactor `check.sh` can't scope).
# you want the whole gate locally before opening a PR (e.g. a broad refactor `check.sh` can't scope).
# For day-to-day work use ./scripts/check.sh (scoped, fast) — this one is minutes, by design.
#
# CI (ci.yml) remains the authoritative gate; this is a convenience, not a substitute. No git hook
# calls this, and none should — hooks enforce policy, scripts run tests, CI proves everything.

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/gate_common.sh
source "$_SCRIPT_DIR/lib/gate_common.sh"

gate_root
gate_resolve_python

if [[ ! -x "$PY" ]]; then
  echo "[check-full] .venv missing — run: python -m venv .venv && ./.venv/bin/pip install -e '.[dev,studio]'" >&2
  exit 1
fi

echo "[check-full] ruff check . (whole tree)"
"$PY" -m ruff check .

MARKER='not integration and not slow'
if [[ "${CHECK_FULL_SLOW:-}" == "1" ]]; then
  MARKER='not integration'
  echo "[check-full] pytest -q -m '$MARKER' (full unit suite, CI parity — CHECK_FULL_SLOW=1)"
else
  echo "[check-full] pytest -q -m '$MARKER' (fast local — set CHECK_FULL_SLOW=1 for slow cross-face proofs)"
fi
FANOPS_REQUIRE_STUDIO=1 "$PY" -m pytest -q -m "$MARKER"

echo "[check-full] OK — suite green. The PR gate is still CI (unit + e2e)."
