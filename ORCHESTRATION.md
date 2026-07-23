# Running a wave (quickstart)

> **⚠️ THIS IS NOT THE NORMAL WORKFLOW, and you do not need it for ordinary work.** One ticket is one
> worktree, one branch, one PR, merged on green — [`AGENTS.md`](AGENTS.md) "Per-ticket workflow" is
> the whole path. Reach for a wave only when an operator hands you a *batch* and asks for one.
>
> **The enforcement gate this document describes is DORMANT and stays dormant** — no wiring exists in
> `.cursor/hooks.json` or `.claude/settings.json`, so none of the "refused", "denied" or "ledgered"
> guarantees below actually fire. The decision to re-enable, replace or retire it was Phase 6 of the
> Agent Change System program, which is **cancelled**, so dormancy is the permanent disposition
> rather than a pending question. Status owner: [`.orchestration/SPEC.md`](.orchestration/SPEC.md).

Hand a batch of Linear tasks to one agent; it delegates every bit of the work to sub-agents and
lands the verified results itself. You don't manage it after launch.

## 1. Start the orchestrator — top-level, never as a subagent

- **Cursor:** start a Cloud Agent (or chat) on this repo with **`fanops-orchestrator`** selected as
  the agent, or tell any TOP-LEVEL agent: *"Act as the FanOps orchestrator — read
  `.cursor/agents/fanops-orchestrator.md` and follow it."*
- **Claude Code:** type **`/fanops-orchestrator <tasks or plan>`** — the command runs in the current
  conversation (no nesting), which becomes the orchestrator. Same process, same ledger and land rules.

**In Cursor**, asking a chat to spawn `fanops-orchestrator` as an agent nests it as a SUBAGENT — and a
nested orchestrator cannot spawn workers. (In Claude Code the slash command above does *not* nest; it
takes over the current conversation.) The gate was built to refuse that nested spawn and redirect the
calling agent to take over top-level; **with the gate dormant that refusal does not happen, so launch
top-level yourself.** The model you pick for the conversation is the whole wave's model (every
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

**Unconditional:** GitHub branch protection with required checks — nothing red merges, on any machine.

**Conditional on local setup:** the `.githooks` `pre-commit`/`pre-push` pair (secret scan, staged ruff,
refuses direct/force push to `main`) fires **only where `core.hooksPath` points at `.githooks`** — run
`./scripts/setup-hooks.sh` once per clone. A fresh clone, a new worktree on another machine, or a cloud
VM has **no** local hook until that runs. The `permissions.deny` list in `.claude/settings.json` (what
actually refuses `pytest`) is **Claude Code only** — Cursor has no equivalent. `check.sh` is lint-only.

**Convention, enforced by nothing:** delegate-everything, spawn only `fanops-worker`, verifier for
hot-file/broad units. The hook-gate machinery is dormant on disk — details and re-enable path:
[`.orchestration/SPEC.md`](.orchestration/SPEC.md).

## Notes
- The orchestrator merges, never you. A 403 on merge means the Cursor GitHub App lacks write/merge
  rights on this repo — grant them and re-run the wave.
- One orchestrator at a time.
