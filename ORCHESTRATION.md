# Running a wave (quickstart)

Hand a batch of Linear tasks to one agent; it delegates every bit of the work to sub-agents and
lands the verified results itself. You don't manage it after launch.

## 1. Start the orchestrator — top-level, never as a subagent

- **Cursor:** start a Cloud Agent (or chat) on this repo with **`fanops-orchestrator`** selected as
  the agent, or tell any TOP-LEVEL agent: *"Act as the FanOps orchestrator — read
  `.cursor/agents/fanops-orchestrator.md` and follow it."*
- **Claude Code:** type **`/fanops-orchestrator <tasks or plan>`** — the command runs in the current
  conversation (no nesting), which becomes the orchestrator. Same process, same ledger and land rules.

Typing `/fanops-orchestrator <plan>` into a chat spawns the orchestrator as a SUBAGENT — nested, it
cannot spawn workers. The gate was built to refuse that spawn unconditionally and redirect the chat's
own agent to take over as the orchestrator; **with the gate dormant that refusal does not happen, so
launch top-level yourself.** The model you pick for the conversation is the whole wave's model (every
sub-agent is pinned `model: inherit`). Then tell it what to do, e.g.:

> "Take the ready Linear tickets for team *Molham homsi* and drive them all to landed. Leave the repo pristine."

## 2. Watch (optional)

```bash
python scripts/orchestrate.py status   # what's still outstanding across the whole repo
python scripts/orchestrate.py done     # exits 0 only when everything is landed & pristine
```

`done` is also the orchestrator's own completion gate — it cannot claim finished until this exits 0.

## What holds the line (enforcement hooks are DISABLED — operator decision, 2026-07-15)

> **ORCHESTRATION-GATE-STATUS: DORMANT** — no gate wiring is present in `.cursor/hooks.json` or
> `.claude/settings.json`. Status owner: [`.orchestration/SPEC.md`](.orchestration/SPEC.md).

GitHub branch protection with required checks (nothing red merges), the lint-only `check.sh`
(no local test or dependency storms), the Claude-Code-only `permissions.deny` list in
`.claude/settings.json` (which is what actually refuses `pytest`), and the conventions in the
orchestrator/worker files (delegate-everything, spawn only `fanops-worker`, verifier only for
hot-file/broad units). The hook-gate machinery is dormant on disk — details and re-enable path:
[`.orchestration/SPEC.md`](.orchestration/SPEC.md).

## Notes
- The orchestrator merges, never you. A 403 on merge means the Cursor GitHub App lacks write/merge
  rights on this repo — grant them and re-run the wave.
- One orchestrator at a time.
