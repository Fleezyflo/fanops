# Delegation-only orchestration protocol

> **ORCHESTRATION-GATE-STATUS: DORMANT** — no gate wiring is present in `.cursor/hooks.json` or
> `.claude/settings.json`. **This file is the status owner**; every other document links here.
>
> **STATUS: enforcement hooks are DISABLED (operator decision, 2026-07-15) — the gate cost more in
> blocked work than it protected.** The machinery below was wired and enforcing before that decision;
> it is kept on disk (gate, adapter, and their tests still pass in CI) and enforces nothing today.
> **Everything under "Enforcement" is therefore the RETAINED DESIGN, not a description of live
> behaviour** — read every guarantee there as *"would hold, once re-wired"*. The live safety rails
> are: GitHub branch protection with required checks, the lint-only `check.sh`, the Claude-Code-only
> `permissions.deny` list in `.claude/settings.json` (the mechanism that actually refuses `pytest` —
> Cursor has no equivalent), and the agent conventions in the orchestrator/worker files. Re-enable by
> restoring the hook wiring from git history (`git log -- .cursor/hooks.json .claude/settings.json`);
> the wiring is the ONLY thing missing.

> Quickstart: [`ORCHESTRATION.md`](../ORCHESTRATION.md). One command:
> `python scripts/orchestrate.py start | status | done | stop`.

The orchestrator coordinates; sub-agents execute every unit of work (scope, implement, validate,
verify, fix, cleanup, conflict-resolution). Its only hands-on action is the land (`gh pr merge`);
it never commits or pushes — workers push their own branches.

## Enforcement — RETAINED DESIGN (dormant; nothing below is wired today)

Written in the design's own present tense because it described live behaviour until 2026-07-15 and
would describe it again on re-wiring. **Today the wiring is absent, so none of it fires.**

One enforcement brain, two runtimes. All decision logic lives in
`.cursor/hooks/orchestration_gate.py`; Cursor wires it via `.cursor/hooks.json` (`failClosed: true`),
Claude Code via `.claude/settings.json` hooks through the thin adapter
`.claude/hooks/orchestration_gate_claude.py`. Cursor hook payloads carry NO caller identity, so its
guarantees sit at boundaries judged from the command string / event payload alone. Claude Code
payloads DO carry the caller's `agent_type`, which enables two Claude-only upgrades: verification
records are writable ONLY by a `fanops-worker` sub-agent, and Write/Edit of orchestration state or
enforcement machinery is denied for everyone during a wave (the un-hookable-Write residual below is
Cursor-only).

Even when wired, the gate is INERT unless a wave is engaged — `FANOPS_ORCHESTRATED=1` or the
`.orchestration/state/ACTIVE` marker (created by `orchestrate.py start`) — so committing the hooks
changes nothing for normal sessions. **That wave check is a second condition, not the reason it is
inert today: today it is unwired, so it does not run at all.** Once re-wired, while active:

| Guarantee | Mechanism |
|---|---|
| Nothing red lands | every land (`gh pr merge` and raw `gh api …/merge`) runs `gh pr checks --required` — the gate mirrors branch protection, never stricter, so advisory checks (e.g. CodeRabbit) can never block a land; non-green or unverifiable → refused. |
| Verification is priced to risk | an independent verification record is demanded ONLY where wrongness is expensive: the PR touches a `lanes.json` hot file, is broad (>5 files), or its file list is unverifiable (`records_required`, fail closed). Small non-hot changes land on green CI alone — no verifier is spawned for them. Rationale: CI cannot catch an implementer grading their own homework (tests asserting the misread behavior), so an independent diff-vs-acceptance read is bought only where that failure costs the most. |
| A record covers exactly the commits it saw | where a record is demanded, its `head_sha` must equal the PR's current `headRefOid`; stale → land refused (the ONLY re-verify trigger). |
| Only named wave agents spawn; models stay pinned | `subagentStart` denies any type outside {`fanops-worker`, `fanops-lander`}: ad-hoc types (`general-purpose`, `shell`) are where spawn-time models take effect, and a second `fanops-orchestrator` mid-wave is the double-merge incident. Allowed agents' frontmatter pins `model: inherit`. |
| The orchestrator never runs nested — even outside a wave | spawning `fanops-orchestrator` as a subagent is denied UNCONDITIONALLY (a nested orchestrator cannot spawn workers); the deny message redirects the calling agent to take over as the orchestrator top-level, so `/fanops-orchestrator` self-corrects. |
| Every spawn and land attributed | `subagentStart`/`subagentStop` and lands append `state/ledger.jsonl` (type, model, task, status); denied spawns are ledgered as `subagent_denied`. |
| Enforcement machinery cannot change mid-wave | lands are denied while `.cursor/hooks*`, `.githooks/`, `scripts/orchestrate.py`, `scripts/repo_sweep.py` are dirty in the working tree, AND when the PR's own changed files touch those paths (operator-only; merged outside waves). Fails CLOSED when git/gh cannot answer. |
| No destructive git | `reset --hard`, force-push / direct push to `main`, `checkout -B … origin/main` denied. |
| Gate + state tamper-proof from the shell | any mutating command naming `.orchestration/state/`, `.cursor/hooks*`, `.githooks/` is denied — including interpreters/heredocs (`python3 <<PY`, `python -c`). |
| No self-verification | a record is rejected when `verifier` equals `executor` or is the orchestrator. |
| Done is measured, not declared | `orchestrate.py done` exits 0 only when `repo_sweep --require-pristine` is green (unmeasurable → exit 3, never a false done); exit 0 auto-disengages the wave. `stop` is operator-only — denied from inside a run. |

**NOT enforced (residuals — these are residuals of the DESIGN; with the gate dormant, NONE of the
guarantees above is enforced either):**
- CURSOR RUNTIME ONLY: Cursor's Write tool cannot be hooked, so protected files can still be WRITTEN
  by any agent there; the land-time checks make such writes un-landable, not impossible.
  Keystroke-level prevention on Cursor requires the readonly option below. (On Claude Code this gap
  *was* closed while wired: Write/Edit hooks denied those writes, and records were writable only by
  `fanops-worker`. **Dormant today — that closure does not apply.**)
- `executor`/`verifier` in records are self-reported strings — verifier ≠ implementer is auditable
  (ledger + record), not identity-bound. A lying record passes the gate.
- The `subagentStart` deny and spawning the named `fanops-worker` from an orchestrator context follow
  Cursor's documented contracts but have not yet run in a live wave on this machine. The first wave is
  the validation; failure is loud (`subagent_denied` ledger entries; the orchestrator's step-0 abort).

### Readonly option (maximum enforcement)
`readonly: true` on `fanops-orchestrator` blocks all its edits AND its git — it then hands each land
command to `fanops-lander` (same gate applies). Default ships non-readonly: the orchestrator lands.

## Records and lifecycle

Scope → implement (parallel where non-conflicting) → verify (a DIFFERENT sub-agent, ONLY for
hot-file/broad units) → land (orchestrator, on green CI). The verifier writes
`.orchestration/state/verified/<UNIT>.json`; the schema and writing rules live in
`.agents/_worker-protocol.md` (verify role). Where the tier demands records, the gate lands a PR
only when every unit on it has one with `passed: true`, a sub-agent `verifier` differing from
`executor`, and a `head_sha` matching the PR's current head.

`state/` is git-ignored runtime data; `ledger.jsonl` is the attribution record — proof nothing was
silently done by the orchestrator itself.

## Scope = the whole repo

`scripts/repo_sweep.py` (read-only) enumerates open PRs, merge conflicts, unresolved merges, stale
branches, and leftover artifacts. `--require-pristine` exits 0 only when every task is landed and the
repo is pristine (draft PRs reported, non-blocking); otherwise exit 3 plus the outstanding list.
