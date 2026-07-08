#!/usr/bin/env bash
set -euo pipefail
# scan-secrets.sh — shared secret scanner for the pre-commit hook AND CI (MOL-193 / CI-13).
#
# Modes:
#   scan-secrets.sh staged            # scan ADDED lines in the staged diff (used by .githooks/pre-commit)
#   scan-secrets.sh diff-base <ref>   # scan ADDED lines in <ref>...HEAD (used by CI on the PR diff)
#
# Exit 1 on any finding, 0 otherwise. Scans only ADDED lines (a '+' in the unified diff), never the whole
# tree/history. Binary/lock files are skipped, as is this scanner + the hook (they legitimately contain the
# patterns). There is deliberately NO skip/bypass env var — CI must never honor a local `ECC_SKIP_*`.

mode="${1:-}"
case "$mode" in
  staged)
    mapfile -t files < <(git diff --cached --name-only --diff-filter=ACMR || true)
    diff_added() { git diff --cached -U0 -- "$1" | awk '/^\+\+\+ /{next} /^\+/{print substr($0,2)}'; }
    ;;
  diff-base)
    base="${2:-}"
    [[ -n "$base" ]] || { echo "usage: scan-secrets.sh diff-base <ref>" >&2; exit 2; }
    mapfile -t files < <(git diff --name-only --diff-filter=ACMR "${base}...HEAD" || true)
    diff_added() { git diff -U0 "${base}...HEAD" -- "$1" | awk '/^\+\+\+ /{next} /^\+/{print substr($0,2)}'; }
    ;;
  *)
    echo "usage: scan-secrets.sh (staged | diff-base <ref>)" >&2; exit 2 ;;
esac

has_findings=0

scan_one() {   # <added-lines-text> <file> <name> <regex>
  local added="$1" file="$2" name="$3" regex="$4" hits
  [[ -z "$added" ]] && return 0
  if hits="$(printf '%s\n' "$added" | rg -n --pcre2 "$regex" 2>/dev/null)"; then
    printf '\n[scan-secrets] Potential secret (%s) in %s\n' "$name" "$file" >&2
    printf '%s\n' "$hits" | head -n 3 >&2
    has_findings=1
  fi
}

for file in "${files[@]}"; do
  [[ -z "$file" ]] && continue
  case "$file" in
    *.png|*.jpg|*.jpeg|*.gif|*.svg|*.pdf|*.zip|*.gz|*.lock|pnpm-lock.yaml|package-lock.json|yarn.lock|bun.lockb) continue ;;
    scripts/scan-secrets.sh|.githooks/pre-commit) continue ;;   # they carry the patterns by design
  esac
  added="$(diff_added "$file")"
  scan_one "$added" "$file" "OpenAI key"                 'sk-[A-Za-z0-9]{20,}'
  scan_one "$added" "$file" "GitHub classic token"       'ghp_[A-Za-z0-9]{36}'
  scan_one "$added" "$file" "GitHub fine-grained token"  'github_pat_[A-Za-z0-9_]{20,}'
  scan_one "$added" "$file" "AWS access key"             'AKIA[0-9A-Z]{16}'
  scan_one "$added" "$file" "private key block"          '-----BEGIN (RSA|EC|OPENSSH|DSA|PRIVATE) KEY-----'
  scan_one "$added" "$file" "generic credential assignment" "(?i)\\b(api[_-]?key|secret|password|token)\\b\\s*[:=]\\s*['\\\"][^'\\\"]{12,}['\\\"]"
done

if [[ "$has_findings" -eq 1 ]]; then
  echo >&2
  echo "[scan-secrets] BLOCKED: potential secret in added lines. Remove it (use env vars / a secret manager), then retry." >&2
  exit 1
fi
exit 0
