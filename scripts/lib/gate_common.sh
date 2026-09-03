#!/usr/bin/env bash
# Shared bootstrap for scripts/check.sh and scripts/check-full.sh.
# Sourced, not executed directly.

# gate_root — set ROOT to the git toplevel and cd there.
gate_root() {
  ROOT="$(git rev-parse --show-toplevel)"
  cd "$ROOT"
}

# gate_resolve_python [worktree_fallback]
# Sets PY to ROOT/.venv/bin/python. When worktree_fallback is non-empty and local
# venv is missing, fall back to the main checkout's .venv (worktree support).
gate_resolve_python() {
  local worktree_fallback="${1:-}"
  PY="$ROOT/.venv/bin/python"
  if [[ ! -x "$PY" && -n "$worktree_fallback" ]]; then
    local COMMON
    COMMON="$(git rev-parse --git-common-dir 2>/dev/null || true)"
    if [[ -n "$COMMON" ]]; then
      local MAIN_ROOT
      MAIN_ROOT="$(cd "$(dirname "$COMMON")" && pwd)"
      PY="$MAIN_ROOT/.venv/bin/python"
    fi
  fi
}
