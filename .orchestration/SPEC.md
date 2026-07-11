# Delegation-only orchestration protocol

> Quickstart: [`ORCHESTRATION.md`](../ORCHESTRATION.md). One command:
> `python scripts/orchestrate.py start | status | done | stop`. This file is the enforcement reference.

The orchestrator coordinates; sub-agents execute every unit of work (scope, implement, validate,
verify, fix, cleanup, conflict-resolution). Its only hands-on action is the land (`gh pr merge`);
it never commits or pushes — workers push their own branches.

## Enforcement

Cursor hook payloads carry NO caller identity, and `readonly` (the only per-agent write block) would
also block the orchestrator's land. So the hard guarantees sit at boundaries the gate can judge from
the command string / event payload alone: `.cursor/hooks.json` → `.cursor/hooks/orchestration_gate.py`,
`failClosed: true`.

The gate is INERT unless a wave is engaged — `FANOPS_ORCHESTRATED=1` or the
`.orchestration/state/ACTIVE` marker (created by `orchestrate.py start`) — so committing the hooks
changes nothing for normal sessions. While active:

| Guarantee | Mechanism |
|---|---|
| Nothing unverified lands | `gh pr merge` and raw `gh api …/merge` denied unless every `MOL-xxx` on the PR (branch + title + body) has a passing verification record. |
| A record covers exactly the commits it saw | record `head_sha` must equal the PR's current `headRefOid`; stale → land refused (the ONLY re-verify trigger). |
| Only named wave agents spawn; models stay pinned | `subagentStart` denies any type outside {`fanops-worker`, `fanops-lander`}: ad-hoc types (`general-purpose`, `shell`) are where spawn-time models take effect, and a second `fanops-orchestrator` mid-wave is the double-merge incident. Allowed agents' frontmatter pins `model: inherit`. |
| Every spawn and land attributed | `subagentStart`/`subagentStop` and lands append `state/ledger.jsonl` (type, model, task, status); denied spawns are ledgered as `subagent_denied`. |
| Enforcement machinery cannot change mid-wave | lands are denied while `.cursor/hooks*`, `.githooks/`, `scripts/orchestrate.py`, `scripts/repo_sweep.py` are dirty in the working tree, AND when the PR's own changed files touch those paths (operator-only; merged outside waves). Fails CLOSED when git/gh cannot answer. |
| No destructive git | `reset --hard`, force-push / direct push to `main`, `checkout -B … origin/main` denied. |
| Gate + state tamper-proof from the shell | any mutating command naming `.orchestration/state/`, `.cursor/hooks*`, `.githooks/` is denied — including interpreters/heredocs (`python3 <<PY`, `python -c`). |
| No self-verification | a record is rejected when `verifier` equals `executor` or is the orchestrator. |
| Done is measured, not declared | `orchestrate.py done` exits 0 only when `repo_sweep --require-pristine` is green (unmeasurable → exit 3, never a false done); exit 0 auto-disengages the wave. `stop` is operator-only — denied from inside a run. |

**NOT enforced (residuals):**
- Cursor's Write tool cannot be hooked: protected files can still be WRITTEN by any agent. The
  land-time checks make such writes un-landable, not impossible. Keystroke-level prevention requires
  the readonly option below.
- `executor`/`verifier` in records are self-reported strings — verifier ≠ implementer is auditable
  (ledger + record), not identity-bound. A lying record passes the gate.
- The `subagentStart` deny and spawning the named `fanops-worker` from an orchestrator context follow
  Cursor's documented contracts but have not yet run in a live wave on this machine. The first wave is
  the validation; failure is loud (`subagent_denied` ledger entries; the orchestrator's step-0 abort).

### Readonly option (maximum enforcement)
`readonly: true` on `fanops-orchestrator` blocks all its edits AND its git — it then hands each land
command to `fanops-lander` (same gate applies). Default ships non-readonly: the orchestrator lands.

## Records and lifecycle

Scope → implement (parallel where non-conflicting) → verify (a DIFFERENT sub-agent) → land
(orchestrator). The verifier writes `.orchestration/state/verified/<UNIT>.json`; the schema and
writing rules live in `.agents/_worker-protocol.md` (verify role). The gate lands a PR only when
every unit on it has a record with `passed: true`, a sub-agent `verifier` differing from `executor`,
and a `head_sha` matching the PR's current head.

`state/` is git-ignored runtime data; `ledger.jsonl` is the attribution record — proof nothing was
silently done by the orchestrator itself.

## Scope = the whole repo

`scripts/repo_sweep.py` (read-only) enumerates open PRs, merge conflicts, unresolved merges, stale
branches, and leftover artifacts. `--require-pristine` exits 0 only when every task is landed and the
repo is pristine (draft PRs reported, non-blocking); otherwise exit 3 plus the outstanding list.
