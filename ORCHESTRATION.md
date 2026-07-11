# Running a wave (quickstart)

Hand a batch of Linear tasks to one agent; it delegates every bit of the work to sub-agents and
lands the verified results itself. You don't manage it after launch.

## 1. Start the orchestrator — top-level, never as a subagent

- **Preferred:** start a Cursor Cloud Agent (or chat) on this repo with **`fanops-orchestrator`**
  selected as the agent.
- **Hand-off:** tell any TOP-LEVEL agent: *"Act as the FanOps orchestrator — read
  `.cursor/agents/fanops-orchestrator.md` and follow it."*

Never have an agent SPAWN the orchestrator as a subagent — nested, it cannot spawn workers, and the
gate refuses a second orchestrator mid-wave. The model you pick is the whole wave's model (every
sub-agent is pinned `model: inherit`). Then tell it what to do, e.g.:

> "Take the ready Linear tickets for team *Molham homsi* and drive them all to landed. Leave the repo pristine."

## 2. Watch (optional)

```bash
python scripts/orchestrate.py status   # what's still outstanding across the whole repo
python scripts/orchestrate.py done     # exits 0 only when everything is landed & pristine
```

`done` is also the orchestrator's own completion gate — it cannot claim finished until this exits 0.

## Guarantees while a wave runs

Nothing lands without a *different* sub-agent's verification record pinned to the PR's current head;
every spawn and land is ledgered (`.orchestration/state/ledger.jsonl`); spawn types and models are
locked; no destructive git; the gate and its state are tamper-protected. Scope is the whole repo —
open PRs, conflicts, stale branches included. Details: [`.orchestration/SPEC.md`](.orchestration/SPEC.md).

## Notes

- Enforcement is OFF outside a wave: `start` engages it, `done` (exit 0) disengages it. If a crashed
  wave left it on, run `python scripts/orchestrate.py stop` from your own terminal — operator-only,
  refused from inside a run.
- The orchestrator merges, never you. A 403 on merge means the Cursor GitHub App lacks write/merge
  rights on this repo — grant them and re-run the wave.
- One orchestrator at a time.
