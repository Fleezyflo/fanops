# Running a wave of work (quickstart)

Hand a batch of Linear tasks to one agent and let it drive them all to landed — while it delegates every
bit of the actual work to sub-agents and only *lands* finished, verified results itself.

You do not need to understand hooks, verification records, or ledgers to use this. Three steps:

## 1. Start the orchestrator
In Cursor, start a **Cloud Agent** on this repo using the **`fanops-orchestrator`** agent, and tell it what
to do, e.g.:

> "Take the ready Linear tickets for team *Molham homsi* and drive them all to landed. Leave the repo pristine."

That's it — you don't manage it after this.

## 2. What happens automatically
The orchestrator runs `python scripts/orchestrate.py start` to engage enforcement, then loops:
**scope → plan (parallel where safe) → implement → verify → land → repeat**, spawning sub-agents for every
step. It keeps going until the finish gate is green.

## 3. Check progress / completion (optional)
Anytime, from a terminal:

```bash
python scripts/orchestrate.py status   # what's still outstanding across the whole repo
python scripts/orchestrate.py done     # the finish line — exits 0 only when everything is landed & pristine
```

`done` is also the orchestrator's own completion gate: it cannot claim it's finished until this exits 0.

---

## What you're guaranteed (while a wave is running)
- **Nothing lands unless a *different* sub-agent verified it** against the task's acceptance criteria.
- **Every sub-agent's work is recorded** (`.orchestration/state/ledger.jsonl`) — nothing is silently done
  by the orchestrator itself.
- **The orchestrator never edits code** — it only lands; anything it tried to change can't be landed.
- **No destructive git, no tampering** with the gate or the verification state.
- **Scope is the whole repo** — open PRs, merge conflicts, stale branches, leftover junk are all driven to
  resolution, not just the tickets you named.

## Notes
- Enforcement is **off by default** and only turns on for a wave (via `orchestrate.py start`, or by setting
  `FANOPS_ORCHESTRATED=1`), so it never interferes with normal Cursor work or other agents on this repo.
- If the orchestrator's GitHub token can't merge, the final merge click is yours — everything is still
  verified and recorded first.
- Deeper detail (the exact enforcement contract): [`.orchestration/SPEC.md`](.orchestration/SPEC.md).

## One lander at a time
Run **one** orchestrator landing session at a time. Before (re)landing anything, `git fetch` and
`gh pr view` the target PR — parallel orchestrators have caused double-merges.
