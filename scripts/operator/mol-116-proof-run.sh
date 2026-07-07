#!/usr/bin/env bash
# MOL-116 — THE PROOF RUN: one FRESH post per account through approve -> publish -> reconcile -> verify-live
#
# Accounts (5): markmakmouly, perca.late, cisumwolfhom (IG via Postiz), backlikeineverleft, hrmny-blog (TikTok via Zernio)
#
# Queue reality (per ticket, 2026-07-07):
#   - 20 queued TikTok posts exist (10 each for backlikeineverleft, hrmny-blog) — approve ONE per account for proof
#   - IG queue EMPTY — mint fresh crossposts OR use Posted-tab "Post again" per IG account
#
# LIVE PUBLISH — operator host only. Cloud agent VM (2026-07-07): BLOCKED — no operator ledger/creds.
set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PY="${PY:-./.venv/bin/python}"
FANOPS="${FANOPS:-./.venv/bin/fanops}"
EVIDENCE_DIR="${EVIDENCE_DIR:-MohFlow-FanOps/07_reports/mol-116-$(date -u +%Y%m%dT%H%M%SZ)}"

ACCOUNTS_IG=(markmakmouly perca.late cisumwolfhom)
ACCOUNTS_TT=(backlikeineverleft hrmny-blog)

echo "=== MOL-116 preflight ==="
$FANOPS doctor || true
$FANOPS status
if ! $PY -c "from fanops.config import Config; c=Config(); import sys; sys.exit(0 if c.is_live else 1)"; then
  echo "BLOCKED: FANOPS_LIVE is not set." >&2; exit 1
fi
if ! test -f MohFlow-FanOps/00_control/ledger.json; then
  echo "BLOCKED: no operator ledger." >&2; exit 1
fi
mkdir -p "$EVIDENCE_DIR"

echo "=== Ledger state snapshot (attach to Linear) ==="
$PY - <<'PY' | tee "$EVIDENCE_DIR/ledger-snapshot.txt"
from collections import Counter
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
led = Ledger.load(Config())
states = Counter(p.state.value for p in led.posts.values())
print("post_states:", dict(states))
for acct in ["markmakmouly","perca.late","cisumwolfhom","backlikeineverleft","hrmny-blog"]:
    rows = [p for p in led.posts.values() if p.account == acct]
    by = Counter(p.state.value for p in rows)
    print(f"{acct}: {dict(by)}")
PY

echo "=== Step 1: Select / mint proof posts (one per account) ==="
echo "TikTok — pick one queued post per account from Review (do NOT flush the other 9 queued each):"
$PY - <<'PY' | tee "$EVIDENCE_DIR/candidate-queued.txt"
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState, Platform
led = Ledger.load(Config())
for handle in ["backlikeineverleft", "hrmny-blog"]:
    queued = [p for p in led.posts.values()
              if p.account == handle and p.platform is Platform.tiktok and p.state is PostState.queued]
    queued.sort(key=lambda p: p.scheduled_time or "")
    print(f"{handle}: first_queued={queued[0].id if queued else 'NONE'} count={len(queued)}")
PY

echo "IG — no queued posts: mint fresh OR Posted-tab Post again (creates awaiting_approval repost):"
echo "  Studio Posted -> Post again on a prior markmakmouly/perca.late/cisumwolfhom clip"
echo "  OR ingest a new source: fanops ingest && fanops run  (full pipeline — stronger proof)"
echo "  Record chosen post_ids in $EVIDENCE_DIR/chosen-posts.txt (one line per account: handle post_id source)"

CHOSEN="${CHOSEN:-$EVIDENCE_DIR/chosen-posts.txt}"
if ! test -f "$CHOSEN"; then
  cat > "$CHOSEN" <<'EOF'
# Edit before running publish phase — one fresh post_id per account from THIS run:
# markmakmouly <post_id> [fresh-ingest|post-again]
# perca.late <post_id> [fresh-ingest|post-again]
# cisumwolfhom <post_id> [fresh-ingest|post-again]
# backlikeineverleft <post_id> [queued-from-review]
# hrmny-blog <post_id> [queued-from-review]
EOF
  echo "Created template $CHOSEN — fill in post_ids, then re-run from Step 2."
  exit 0
fi

mapfile -t PROOF_IDS < <(grep -v '^#' "$CHOSEN" | grep -v '^[[:space:]]*$' | awk '{print $2}')
if test "${#PROOF_IDS[@]}" -ne 5; then
  echo "Need exactly 5 post_ids in $CHOSEN (got ${#PROOF_IDS[@]})" >&2
  exit 1
fi

echo "=== Step 2: Approve (awaiting_approval -> queued) ==="
echo "Studio Review -> Approve selected (human gate). CLI equivalent:"
$PY - <<PY
from fanops.config import Config
from fanops.studio.actions_approve import approve_posts
ids = $(printf '%s\n' "${PROOF_IDS[@]}" | $PY -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
print(approve_posts(Config(), ids, confirmed=True))
PY

echo "=== Step 3: Publish (T10 preflight must pass) ==="
for pid in "${PROOF_IDS[@]}"; do
  echo "--- publish_now $pid ---"
  $PY -c "
from fanops.config import Config
from fanops.studio.actions import publish_now
print(publish_now(Config(), '${pid}', confirmed=True))
"
done

echo "=== Step 4: Reconcile ==="
$FANOPS reconcile | tee "$EVIDENCE_DIR/reconcile.log"

echo "=== Step 5: Verify live (Root-2 / MOL-113) ==="
$FANOPS verify-live | tee "$EVIDENCE_DIR/verify-live.txt"
LIVE_COUNT=$(grep -c $'\tLIVE\t' "$EVIDENCE_DIR/verify-live.txt" || true)
echo "confirmed_live_lines=$LIVE_COUNT (need 5 for gate)"

echo "=== Step 6: Per-post Graph/oEmbed evidence ==="
$PY - <<PY | tee "$EVIDENCE_DIR/per-post-evidence.json"
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.meta_graph import confirm_post_live
ids = $(printf '%s\n' "${PROOF_IDS[@]}" | $PY -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
led = Ledger.load(Config())
out = []
for pid in ids:
    p = led.posts.get(pid)
    if not p:
        out.append({"post_id": pid, "error": "missing"}); continue
    res = confirm_post_live(Config(), p, reported_username=p.account)
    out.append({
        "post_id": pid,
        "account": p.account,
        "platform": p.platform.value,
        "state": p.state.value,
        "public_url": p.public_url,
        "media_id": p.media_id,
        "confirm_post_live": res,
    })
print(json.dumps(out, indent=2))
PY

echo "=== Acceptance checklist ==="
echo "  [ ] 5 NEW posts (creation timestamp from THIS run)"
echo "  [ ] Each published/analyzed with real permalink"
echo "  [ ] verify-live: 5/5 LIVE with correct owner username"
echo "  [ ] Evidence in $EVIDENCE_DIR attached to Linear MOL-116"
echo "  [ ] Operator visual check on all 5 profiles"
$FANOPS status | tee "$EVIDENCE_DIR/final-status.txt"
