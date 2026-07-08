#!/usr/bin/env bash
# Operator automation for codemap sync — handles gh/git bulk work only.
# Cursor UI is manual (see .cursor/automations/codemap-sync.md).
#
# Usage (from repo root on main):
#   ./scripts/codemap-sync-operator-setup.sh          # cleanup + secrets + verify
#   ./scripts/codemap-sync-operator-setup.sh cleanup  # legacy PRs + branches only
#   ./scripts/codemap-sync-operator-setup.sh secrets  # GitHub secrets only
#   ./scripts/codemap-sync-operator-setup.sh verify   # inventory + drift check
#   ./scripts/codemap-sync-operator-setup.sh smoke    # dispatch workflow (bills agent)
set -euo pipefail

REPO="${FANOPS_REPO:-Fleezyflo/fanops}"

die() { echo "[codemap-setup] ERROR: $*" >&2; exit 1; }
info() { echo "[codemap-setup] $*"; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing $1"; }

step_cleanup_legacy() {
  info "Closing legacy cursor/codemaps-source-alignment-* PRs…"
  mapfile -t nums < <(gh pr list --repo "$REPO" --state open --json number,headRefName \
    --jq '.[] | select(.headRefName | test("^cursor/codemaps-source-alignment-")) | .number')
  if ((${#nums[@]} == 0)); then
    info "  none open"
  else
    for n in "${nums[@]}"; do
      info "  closing #$n"
      gh pr close "$n" --repo "$REPO" || die "gh pr close #$n failed — need write access"
    done
    info "  closed ${#nums[@]} PR(s)"
  fi

  info "Deleting legacy codemaps-source-alignment-* branches…"
  git fetch origin --prune 2>/dev/null || true
  mapfile -t branches < <(git branch -r 2>/dev/null \
    | grep 'origin/cursor/codemaps-source-alignment-' | sed 's|^[[:space:]]*origin/||' || true)
  if ((${#branches[@]} == 0)); then
    info "  none found"
  else
    for b in "${branches[@]}"; do
      info "  deleting origin/$b"
      git push origin --delete "$b" || die "git push --delete $b failed"
    done
    info "  deleted ${#branches[@]} branch(es)"
  fi
}

step_wire_secrets() {
  info "GitHub secrets (paste values from Cursor automation UI)…"
  for key in CURSOR_CODEMAP_SYNC_WEBHOOK_URL CURSOR_CODEMAP_SYNC_WEBHOOK_TOKEN; do
    if gh secret list --repo "$REPO" 2>/dev/null | grep -q "^${key}"; then
      read -r -p "  $key already set. Overwrite? [y/N] " ans
      [[ "${ans,,}" == "y" ]] || { info "  skipped $key"; continue; }
    fi
    if [[ "$key" == *TOKEN* ]]; then
      read -r -s -p "  Paste $key (hidden): " val; echo
    else
      read -r -p "  Paste $key: " val
    fi
    [[ -n "$val" ]] || die "empty value for $key"
    gh secret set "$key" --repo "$REPO" --body "$val"
    info "  set $key"
  done
}

step_verify() {
  info "Codemap PR inventory:"
  gh pr list --repo "$REPO" --state open --json number,headRefName,title,isDraft \
    --jq '.[] | select(.headRefName | test("^cursor/codemaps")) | "  #\(.number)  \(.headRefName)  draft=\(.isDraft)  \(.title)"' \
    || true
  local legacy sync
  legacy=$(gh pr list --repo "$REPO" --state open --json headRefName \
    --jq '[.[] | select(.headRefName | test("^cursor/codemaps-source-alignment-"))] | length')
  sync=$(gh pr list --repo "$REPO" --state open --json headRefName \
    --jq '[.[] | select(.headRefName == "cursor/codemaps-sync")] | length')
  [[ "$legacy" -eq 0 ]] || die "$legacy legacy alignment PR(s) still open — run: $0 cleanup"
  info "  legacy alignment PRs: 0 ✓"
  info "  cursor/codemaps-sync PRs: $sync (0 or 1 OK)"
  if [[ -f scripts/codemap_drift.py ]]; then
    info "Drift check:"
    python3 scripts/codemap_drift.py || info "  drift present — expected until first sync PR lands"
  fi
}

step_smoke() {
  read -r -p "Dispatch codemap-sync-trigger? Bills one agent run. [y/N] " ans
  [[ "${ans,,}" == "y" ]] || { info "skipped"; return 0; }
  gh workflow run codemap-sync-trigger.yml --repo "$REPO"
  info "dispatched — https://github.com/$REPO/actions/workflows/codemap-sync-trigger.yml"
}

usage() {
  cat <<EOF
Usage: $0 [all|cleanup|secrets|verify|smoke]

YOU do manually (UI, ~2 min):
  • Cursor automation be112a2b: webhook-only trigger, paste prompt, copy URL+token
  • Merge cursor/codemaps-sync PR when agent opens it

SCRIPT does (this tool):
  • Close/delete legacy codemaps-source-alignment-* PRs + branches
  • Wire GitHub secrets (you paste token/URL when prompted)
  • Verify inventory + drift
  • Optional smoke-test workflow dispatch

Env: FANOPS_REPO (default Fleezyflo/fanops)
EOF
}

main() {
  local cmd="${1:-all}"
  need gh; need git; need python3
  case "$cmd" in
    all)     step_cleanup_legacy; step_wire_secrets; step_verify; step_smoke ;;
    cleanup) step_cleanup_legacy; step_verify ;;
    secrets) step_wire_secrets ;;
    verify)  step_verify ;;
    smoke)   step_smoke ;;
    -h|--help|help) usage ;;
    *) die "unknown: $cmd (try: $0 help)" ;;
  esac
  info "done"
}

main "$@"
