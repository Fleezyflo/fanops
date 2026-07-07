#!/usr/bin/env bash
# MOL-126 — Requeue the 2 IG funnel-era failed posts (markmakmouly/instagram)
#   post_43de48824815, post_d32e79446f80
#
# Root cause (fixed): Meta couldn't download media from dead Tailscale funnel (503).
# Media now serves from Cloudflare R2 (FANOPS_MEDIA_PUBLIC_BASE).
#
# LIVE PUBLISH — run only on the operator host with FANOPS_LIVE=1, creds, and ledger.
# Cloud agent VM (2026-07-07): BLOCKED — no .env, no accounts.json, no ledger.json, dryrun only.
set -euo pipefail

POST_IDS=(post_43de48824815 post_d32e79446f80)
HANDLE=markmakmouly
IG_USER_ID=17841414501372977
REASON="mol-126 funnel-era requeue (R2 media)"

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PY="${PY:-./.venv/bin/python}"
FANOPS="${FANOPS:-./.venv/bin/fanops}"

echo "=== MOL-126 preflight ==="
$FANOPS doctor || true
$FANOPS status
if ! $PY -c "from fanops.config import Config; c=Config(); import sys; sys.exit(0 if c.is_live else 1)"; then
  echo "BLOCKED: FANOPS_LIVE is not set — flip Go Live in Studio first." >&2
  exit 1
fi
if ! test -f MohFlow-FanOps/00_control/ledger.json; then
  echo "BLOCKED: no operator ledger at MohFlow-FanOps/00_control/ledger.json" >&2
  exit 1
fi

echo "=== Inspect failed posts ==="
$PY - <<'PY'
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
ids = ["post_43de48824815", "post_d32e79446f80"]
led = Ledger.load(Config())
for pid in ids:
    p = led.posts.get(pid)
    if not p:
        print(f"MISSING {pid}")
        continue
    print(f"{pid}\t{p.state.value}\t{p.account}/{p.platform.value}\t{p.error_reason or ''}")
PY

echo "=== Requeue (failed -> queued) ==="
echo "Option A — Studio Posted tab: filter Failed, select both posts, click Retry."
echo "Option B — Python recover_posts (retryable bucket: funnel 503 -> unknown):"
$PY - <<PY
from fanops.config import Config
from fanops.studio.actions import recover_posts
cfg = Config()
ids = ["post_43de48824815", "post_d32e79446f80"]
res = recover_posts(cfg, ids, action="retry", reason="${REASON}")
print(res)
if not res.ok:
    raise SystemExit(1)
PY

echo "=== Approve if posts landed in awaiting_approval (should be queued after retry) ==="
$PY - <<'PY'
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState
from fanops.studio.actions_approve import approve_posts
ids = ["post_43de48824815", "post_d32e79446f80"]
cfg = Config()
led = Ledger.load(cfg)
need = [i for i in ids if (p := led.posts.get(i)) and p.state is PostState.awaiting_approval]
if need:
    print("approving:", need)
    print(approve_posts(cfg, need, confirmed=True))
else:
    print("no awaiting_approval — skip approve")
PY

echo "=== Publish + reconcile ==="
echo "Ensure daemon is running: fanops daemon status  (or fanops daemon install && fanops daemon start)"
for pid in "${POST_IDS[@]}"; do
  echo "--- publish_now $pid (Studio Schedule tab Publish now is equivalent) ---"
  $PY -c "
from fanops.config import Config
from fanops.studio.actions import publish_now
print(publish_now(Config(), '${pid}', confirmed=True))
"
done
$FANOPS reconcile

echo "=== Verify live (Graph truth, not ledger URL) ==="
$FANOPS verify-live | grep -E 'post_43de48824815|post_d32e79446f80' || true

echo "=== Optional: direct Graph enumeration for ${HANDLE} (ig_user_id=${IG_USER_ID}) ==="
echo "Requires META_GRAPH_TOKEN in .env. Compare newest media permalinks to reconciled posts."
$PY - <<'PY' || true
import os, json, urllib.request
from fanops.config import Config
cfg = Config()
token = os.getenv("META_GRAPH_TOKEN") or os.getenv("META_GRAPH_TOKEN__markmakmouly")
if not token:
    print("(skip Graph curl — no META_GRAPH_TOKEN)")
    raise SystemExit(0)
uid = "17841414501372977"
url = f"https://graph.facebook.com/v21.0/{uid}/media?fields=id,permalink,media_type,timestamp,caption&limit=5&access_token={token}"
with urllib.request.urlopen(url, timeout=30) as r:
    print(json.dumps(json.load(r), indent=2))
PY

echo "=== Post-run ledger snapshot ==="
$FANOPS status
echo "Attach verify-live output + Graph JSON to Linear MOL-126 and mark Done."
