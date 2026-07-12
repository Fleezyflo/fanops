# Running a wave (quickstart)

Hand a batch of Linear tasks to one agent; it delegates every bit of the work to sub-agents and
lands the verified results itself. You don't manage it after launch.

## 1. Start the orchestrator — top-level, never as a subagent

- **Cursor:** start a Cloud Agent (or chat) on this repo with **`fanops-orchestrator`** selected as
  the agent, or tell any TOP-LEVEL agent: *"Act as the FanOps orchestrator — read
  `.cursor/agents/fanops-orchestrator.md` and follow it."*
- **Claude Code:** type **`/fanops-orchestrator <tasks or plan>`** — the command runs in the current
  conversation (no nesting), which becomes the orchestrator. Same process, same gate
  (`.claude/settings.json` hooks), same ledger and land rules.

Typing `/fanops-orchestrator <plan>` into a chat spawns the orchestrator as a SUBAGENT — nested, it
cannot spawn workers. The gate now refuses that spawn unconditionally and redirects the chat's own
agent to take over as the orchestrator, so the launch self-corrects instead of dead-ending. The model
you pick for the conversation is the whole wave's model (every sub-agent is pinned `model: inherit`).
Then tell it what to do, e.g.:

> "Take the ready Linear tickets for team *Molham homsi* and drive them all to landed. Leave the repo pristine."

## 2. Watch (optional)

```bash
python scripts/orchestrate.py status   # what's still outstanding across the whole repo
python scripts/orchestrate.py done     # exits 0 only when everything is landed & pristine
```

`done` is also the orchestrator's own completion gate — it cannot claim finished until this exits 0.

## Guarantees while a wave runs

Nothing lands unless the PR's CI is green (gate-enforced); risky changes — lane hot files or broad
diffs — additionally require a *different* sub-agent's verification record pinned to the PR's current
head (small non-hot changes land on green CI alone, no verifier spend); every spawn and land is
ledgered (`.orchestration/state/ledger.jsonl`); spawn types and models are locked; no destructive
git; the gate and its state are tamper-protected. Scope is the whole repo — open PRs, conflicts,
stale branches included. Details: [`.orchestration/SPEC.md`](.orchestration/SPEC.md).

## Notes

- Enforcement is OFF outside a wave: `start` engages it, `done` (exit 0) disengages it. If a crashed
  wave left it on, run `python scripts/orchestrate.py stop` from your own terminal — operator-only,
  refused from inside a run.
- The orchestrator merges, never you. A 403 on merge means the Cursor GitHub App lacks write/merge
  rights on this repo — grant them and re-run the wave.
- One orchestrator at a time.
