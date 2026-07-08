# Codemap sync — operator setup

Cursor automations are **UI-only**. This checklist wires the replacement for the broken
"Codemap sync on merge" automation (`be112a2b-7a13-11f1-ba66-0e7d0216e441`).

## Architecture

```
push main + src/** changed
        ↓
.github/workflows/codemap-sync-trigger.yml
   ├─ preflight: scripts/codemap_drift.py (free — no agent if OK)
   └─ POST webhook only when drift
        ↓
Cursor automation (Webhook trigger only)
        ↓
fanops-codemap-sync subagent
   ├─ re-run drift script → no-op or edit docs/CODEMAPS/**
   └─ one PR on cursor/codemaps-sync
```

**Retire** PR-merged / push-to-main Cursor triggers on the old automation.

## 1. Cursor UI

Edit [Codemap sync on merge](https://cursor.com/automations/be112a2b-7a13-11f1-ba66-0e7d0216e441)
(or create new + delete old).

| Setting | Value |
|---------|-------|
| **Trigger** | **Webhook only** — remove PR merged / push-to-main |
| **Repository** | `Fleezyflo/fanops`, branch `main` |
| **Prompt** | See below |
| **Tools** | PR creation ON |

**Prompt:**

```
Run the fanops-codemap-sync subagent (.cursor/agents/fanops-codemap-sync.md).
Follow it exactly. No-op when scripts/codemap_drift.py exits 0.
```

Save → copy webhook URL + `crsr_…` token.

## 2. GitHub secrets

| Secret | Value |
|--------|-------|
| `CURSOR_CODEMAP_SYNC_WEBHOOK_URL` | Webhook URL from automation |
| `CURSOR_CODEMAP_SYNC_WEBHOOK_TOKEN` | Token (`crsr_…`, no Bearer prefix) |

Until wired, workflow skips POST (exit 0, logs message).

## 3. One-time legacy cleanup (operator Mac — needs `gh` write)

```bash
# Close all old alignment drafts (do NOT merge)
gh pr list --repo Fleezyflo/fanops --state open --json number,headRefName \
  --jq '.[] | select(.headRefName | test("^cursor/codemaps-source-alignment-")) | .number' \
| xargs -I{} gh pr close {} --repo Fleezyflo/fanops

# Delete stale remote branches
git fetch origin --prune
git branch -r | grep 'codemaps-source-alignment-' \
| sed 's|origin/||' | xargs -I{} git push origin --delete {}
```

Skip merging old alignment PRs (#446 etc.) — superseded by this design.

## 4. Verify

```bash
# Drift check locally
python scripts/codemap_drift.py

# Manual webhook fire (smoke test — bills one agent run)
gh workflow run codemap-sync-trigger.yml --repo Fleezyflo/fanops
```

Expect: ≤1 open PR on `cursor/codemaps-sync`; zero on `codemaps-source-alignment-*`.

## When it runs

| Runs | Doesn't run |
|------|-------------|
| `main` push touching `src/**` **and** drift detected | Docs-only merges |
| `workflow_dispatch` (manual smoke) | Preflight OK (no webhook, no agent) |
| After concurrency coalesces merge wave | Per-PR merge (old broken behavior) |

## orchestrate.py done

Codemap PRs **count** toward open PRs — land or close them before claiming wave done.
The self-heal phase closes legacy `codemaps-source-alignment-*` drafts automatically.
