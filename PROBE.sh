#!/usr/bin/env bash
# T01 probe: discover cursor-agent JSON envelope + error markers for llm.py constants.
# Run on a host with cursor-agent installed + authenticated. When absent, documents
# conservative defaults from https://cursor.com/docs/cli/reference/output-format
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
TIMEOUT="${PROBE_TIMEOUT:-30}"
SCHEMA='{"type":"object","properties":{"x":{"type":"integer"}},"required":["x"]}'
PROMPT="Respond with ONLY a single JSON object conforming to this schema — no prose, no markdown:
${SCHEMA}

Return {\"x\": 42}."

probe() { perl -e 'alarm shift; exec @ARGV' -- "$@"; }

echo "=== T01 cursor-agent probe (timeout=${TIMEOUT}s) ==="

if ! command -v cursor-agent >/dev/null 2>&1; then
  echo "PROBE: cursor-agent NOT on PATH — using doc defaults (see llm.py constants)."
  echo "  Install: https://cursor.com/docs/cli (not probed on this host)"
  echo "  Envelope fields: result (text/JSON), model (from system init when stream-json; absent in json success — fallback to pin)"
  echo "  Turn-count analogue: none in json envelope → frames_unread=False"
  echo "  _CURSOR_SUPPORTS_VISION=False (vision falls back to claude)"
  echo "  _CURSOR_MODEL_ALIASES={}"
  exit 0
fi

echo "--- happy path (json envelope) ---"
HAPPY=$(probe "$TIMEOUT" cursor-agent -p --output-format json <<< "$PROMPT" 2>/tmp/probe_cursor_stderr || true)
echo "$HAPPY" | head -c 2000
echo
echo "--- stderr (happy) ---"
head -c 500 /tmp/probe_cursor_stderr 2>/dev/null || true
echo

echo "--- toolchain probe (bogus flag) ---"
probe 10 cursor-agent -p --totally-bogus-flag 2>/tmp/probe_toolchain_stderr || true
echo "--- toolchain stderr ---"
cat /tmp/probe_toolchain_stderr 2>/dev/null | head -c 500 || true
echo

echo "--- auth hint (if logged out, stderr shows auth shape) ---"
echo "Done. Fill llm.py _CURSOR_* constants from stdout/stderr above."
