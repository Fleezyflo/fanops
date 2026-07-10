# Delegation-only orchestration protocol

> **Just want to use it?** See the one-screen quickstart: [`ORCHESTRATION.md`](../ORCHESTRATION.md). The
> single command is `python scripts/orchestrate.py start | status | done`. This file is the deep reference.

This directory is the machine-checkable contract behind the `fanops-orchestrator` agent. The orchestrator
**coordinates**; it never does the work. Every unit of work — investigation/scoping, implementation,
validation, verification against acceptance criteria, fixing, and any cleanup/conflict-resolution/rebase
needed to land — is executed **fully by a sub-agent**. The orchestrator's only hands-on action is running
the git commands that land finished work (commit / push / merge).

## Enforcement (what is mechanical vs. what is contract)

Cursor's hook payloads do **not** identify the calling agent (`preToolUse` has no subagent/parent field —
`docs/hooks.md`), and `readonly` (the only per-agent "cannot write" lever) also blocks the orchestrator's
git. So the hard guarantees are placed at boundaries the hook CAN judge deterministically from the command
string / event payload, wired in `.cursor/hooks.json` (cloud-executed) and implemented in
`.cursor/hooks/orchestration_gate.py`.

**Activation (no collateral):** the gate is **inert unless the orchestration environment is engaged** — so
committing `.cursor/hooks.json` to the repo does NOT change behavior for normal or other-agent Cursor
sessions. It enforces only when `FANOPS_ORCHESTRATED` ∈ {1,true,yes,on} (operator env — robust) **or** the
marker `.orchestration/state/ACTIVE` exists. While active, both are tamper-protected (the marker lives under
the guarded state dir; an env var can't be unset by the agent). Enforced guarantees while active:

| Guardrail | Mechanism | Hardness |
|---|---|---|
| **Cannot land unverified work** | `beforeShellExecution` denies `gh pr merge` **and the raw `gh api … pulls/<n>/merge`** unless every Linear unit the PR carries has a passing sub-agent **verification record** (below). `failClosed:true`. | **HARD** (blocks the land command) |
| **Records which sub-agent did each unit** | `subagentStart`/`subagentStop` append to `state/ledger.jsonl` (type, task, status, modified files). | **HARD** (platform fires it) |
| **No destructive git** | `beforeShellExecution` denies `reset --hard`, force-push / direct-push to `main`, `checkout -B … origin/main`. | **HARD** |
| **Gate + state are tamper-proof from the shell** | `beforeShellExecution` denies any command that MUTATES a protected path — `.orchestration/state/`, `.cursor/hooks.json`, `.cursor/hooks/`, `.githooks/` — closing the "forge a verification record / `rm` the hook to disable enforcement" bypass (incl. `echo … > verified/X.json`). | **HARD** |
| **No self-verification** | a verification record whose `verifier` equals its `executor` (or is the orchestrator) is rejected. | **HARD** |
| **Orchestrator edits nothing** | (a) it delegates by contract (agent brief); (b) any file it did edit is **un-landable** — only a verified worker PR can merge; (c) OPTIONAL keystroke-level block: run the orchestrator `readonly:true` and land via the `fanops-lander` sub-agent (see below). | **OUTCOME-HARD** by default; keystroke-HARD with the readonly option |

Why guardrail (a) is not a `preToolUse` `Write` deny: that would block **workers** too (the hook can't tell
orchestrator from worker). The land-gate makes an orchestrator edit pointless (it can never reach `main`),
which is the outcome that matters. For a keystroke-level block, use the readonly option below.

**Residual (be honest):** the protected-path defenses above cover the SHELL. Cursor's `Write` *tool* has no
caller identity, so a determined orchestrator could still write a protected file via the tool (not the
shell). That write cannot be committed/pushed to `main` through the shell gate, and the tamper is visible
in the diff — but if you want a hard keystroke-level guarantee, run the orchestrator `readonly: true`
(below), which blocks the Write tool outright.

### Maximum-enforcement option (readonly orchestrator + lander)
Set `readonly: true` on `fanops-orchestrator` → Cursor blocks all its file edits AND state-changing shell
(hard). Since that also blocks its git-land, spawn `fanops-lander` (a minimal sub-agent whose only job is to
run the land commands; still subject to this same gate). Trade-off: the orchestrator triggers the land via
the lander instead of typing git itself. Default ships non-readonly so the orchestrator lands personally.

## Unit lifecycle

1. **Scope** (sub-agent): read the Linear task, extract its acceptance criteria, decompose into units, list
   the files/resources each unit touches (for conflict analysis).
2. **Implement / validate / fix** (sub-agents, parallel where non-conflicting): execute each unit fully to
   the task's definition of done; push a feature branch; open a PR tagged with the unit id (`MOL-xxx`).
3. **Verify** (a DIFFERENT sub-agent): check the work against the acceptance criteria and write the
   verification record (below). The verifier must not be the implementer and must never be the orchestrator.
4. **Land** (orchestrator): once a verification record exists, `gh pr merge` — the gate allows it and logs
   the land. Any conflict/failing-check/rebase needed to land is itself delegated to a sub-agent first.

## Verification record (the land key)

Path: `.orchestration/state/verified/<UNIT>.json` (e.g. `MOL-190.json`). Written by the **verifier
sub-agent**, never the orchestrator. Schema:

```json
{
  "unit_id": "MOL-190",
  "executor": "subagent:<type>:<subagent_id>",
  "verifier": "subagent:<type>:<subagent_id>",
  "passed": true,
  "acceptance_criteria_checked": true,
  "evidence": "what was run + result (tests, CI, manual checks)",
  "verified_at": "2026-07-08T00:00:00Z"
}
```

The gate lands a PR only if, for **every** `MOL-xxx` on the PR (head branch + title + body), such a record
exists with `passed:true` and a `verifier` that is a sub-agent (not `orchestrator`).

## Attribution ledger

`.orchestration/state/ledger.jsonl` — one JSON object per line, appended by the hooks: every `subagent_start`
/ `subagent_stop` (who, what task, status, files modified) and every `land`. This is the record that nothing
was silently done by the orchestrator itself.

## Full-repo scope

Scope is the whole repo, not just the listed Linear tasks. `scripts/repo_sweep.py` (read-only) enumerates the
mess — open PRs, merge conflicts, unresolved merges, stale branches, leftover artifacts — so the orchestrator
can drive each to resolution **via sub-agents**.

**DONE-gate:** `python scripts/repo_sweep.py --require-pristine` exits `0` only when every task is landed
(no ready-for-review open PRs — **draft PRs are reported but do not block**) AND the repo is pristine;
otherwise it exits `3` and lists what's outstanding (fail-safe: if it cannot even measure the repo, it exits
`3`, never a false "done"). The orchestrator MUST run it as its final action and may not claim completion
until it is green — this removes the orchestrator's ability to *declare* done prematurely. (What it cannot
do: force the agent loop to keep looping — Cursor Cloud has no top-level `stop` hook — so "keep driving"
remains the orchestrator's brief contract, made checkable by this gate.)

Runtime files under `state/` are git-ignored (per-run); only this SPEC and the empty `state/` dir are tracked.
