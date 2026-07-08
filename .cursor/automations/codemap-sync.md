# Codemap sync — operator setup

## Division of labor

| **You (UI, quick)** | **Script (`scripts/codemap-sync-operator-setup.sh`)** |
|---------------------|--------------------------------------------------------|
| Cursor automation: remove PR-merged/push triggers, webhook-only, paste prompt, copy URL+token | Close all `codemaps-source-alignment-*` PRs |
| Merge `cursor/codemaps-sync` when agent opens it | Delete matching stale remote branches |
| Paste URL/token when script prompts | `gh secret set` for both secrets |
| | Verify PR inventory + drift check |
| | Optional smoke-test workflow dispatch |

Cursor UI cannot be scripted (platform has no config-as-code). Everything with `gh`/`git` bulk clicks is scripted.

## 1. You — Cursor UI (once, ~2 min)

Open [Codemap sync on merge](https://cursor.com/automations/be112a2b-7a13-11f1-ba66-0e7d0216e441):

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

Save → keep webhook URL + `crsr_…` token handy for the script.

## 2. Script — paste into terminal

```bash
cd /path/to/fanops && git pull origin main && ./scripts/codemap-sync-operator-setup.sh
```

Runs: legacy cleanup → secrets (prompts for URL/token) → verify → optional smoke test.

Subcommands if you need to re-run pieces: `cleanup` | `secrets` | `verify` | `smoke`

## 3. You — merge the sync PR

When the agent opens `cursor/codemaps-sync`, merge it when CI is green. Cloud agents cannot `gh pr merge`.

## Architecture

```
push main + src/** → GHA preflight (free) → webhook if drift → fanops-codemap-sync → ≤1 PR
```

## When it runs

| Runs | Doesn't |
|------|---------|
| `main` + `src/**` + drift | Docs-only merges |
| `workflow_dispatch` | Preflight OK (no agent) |

Codemap PRs count toward `orchestrate.py done` — land or close before claiming wave done.
