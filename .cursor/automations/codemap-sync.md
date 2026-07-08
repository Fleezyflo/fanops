# Codemap sync automation — operator setup

Cursor automations are configured in the **UI only** (no config-as-code). This file is the
checklist. The **when** gate lives in `.github/workflows/codemap-sync-trigger.yml`; the **what**
lives in `.cursor/agents/fanops-codemap-sync.md`.

## Architecture

```
push main + src/** changed
        ↓
.github/workflows/codemap-sync-trigger.yml   ← WHEN (path filter + concurrency debounce)
        ↓ POST webhook
Cursor automation (Webhook trigger)          ← launch cloud agent
        ↓
fanops-codemap-sync subagent                 ← WHAT (drift detect, single PR, no-op)
```

**Do not use "Pull request merged" or "Push to branch main" as the Cursor trigger.** Those fire
per merge with no path filter — that is what produced 26 `codemaps-source-alignment-*` drafts.

## 1. Cursor UI — edit automation

Open [Codemap sync on merge](https://cursor.com/automations/be112a2b-7a13-11f1-ba66-0e7d0216e441)
(or create a replacement and delete the old one).

| Setting | Value |
|---------|-------|
| **Trigger** | **Webhook only** — remove PR merged / push-to-main if present |
| **Repository** | `Fleezyflo/fanops`, branch `main` |
| **Active** | On |
| **Prompt** | See below |
| **Tools** | Pull request creation ON; others as needed |

**Prompt** (short — do not put schedule/debounce logic here; GHA owns that):

```
Run the fanops-codemap-sync subagent (.cursor/agents/fanops-codemap-sync.md).
Follow it exactly. No-op when docs already match main.
```

Save → **Generate auth header** → copy webhook URL + `crsr_…` token.

## 2. GitHub secrets

Repo → Settings → Secrets and variables → Actions:

| Secret | Value |
|--------|-------|
| `CURSOR_CODEMAP_SYNC_WEBHOOK_URL` | Full webhook URL from automation settings |
| `CURSOR_CODEMAP_SYNC_WEBHOOK_TOKEN` | Token only (`crsr_…`, no `Bearer` prefix) |

Until both are set, the workflow skips the POST (exit 0, logs a message).

## 3. Verify

```bash
# Manual fire (bypasses path filter — for smoke test only)
gh workflow run codemap-sync-trigger.yml --repo Fleezyflo/fanops

# Or curl the webhook directly with the auth header from the UI
```

Expect: one cloud agent run → either no-op or a single `cursor/codemaps-sync` PR.

## When it runs / doesn't run

| Runs | Doesn't run |
|------|-------------|
| `main` push that touches `src/**` | Docs-only / tooling-only merges to `main` |
| After concurrency coalesces a merge wave | Every individual PR merge (old broken behavior) |
| `workflow_dispatch` (manual) | While secrets are unset (workflow skips) |

Agent-level no-op (no PR opened) is a **second** gate inside `fanops-codemap-sync` when
codemaps already match — the webhook still fires and bills one run.

## One-time legacy cleanup

Close (do not merge) open `cursor/codemaps-source-alignment-*` PRs. Optionally land current
doc drift via one manual `workflow_dispatch` or `fanops-codemap-sync` invoke after setup.
