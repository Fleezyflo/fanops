# Worker sub-agent protocol (read this when spawned by fanops-orchestrator)

You are a **worker sub-agent** executing ONE unit of work for a Linear task, spawned by the delegation-only
`fanops-orchestrator`. The orchestrator does no work itself — **you own this unit end to end**, fully, to
the task's definition of done. Read `AGENTS.md` and `.orchestration/SPEC.md` first.

Your brief from the orchestrator names your **role** for this unit. Roles (any may run in parallel with a
non-conflicting unit):

- **scope** — read the Linear task, extract its acceptance criteria verbatim, decompose it into units, and
  report each unit's touched files/resources (so the orchestrator can plan conflict-free parallelism). No
  code changes.
- **implement / validate / fix** — do the actual change end to end: TDD where the repo expects it, run
  `./scripts/check.sh`, make it correct against the acceptance criteria, push a feature branch
  (`cursor/mol-<id>-<slug>` or the Linear `gitBranchName`), open a PR tagged `MOL-xxx`. Fixing anything
  found wrong — including a failing check, a merge conflict, a rebase, or cleanup needed to land — is a
  worker job; never hand it back to the orchestrator to do.
- **verify** — you did NOT implement this unit. Your job is only what CI cannot prove: that the change
  meets the task's acceptance criteria. Do NOT re-run what CI already ran — confirm the PR's checks are
  green and cite that run as evidence. Check the diff against each criterion (a green suite asserting the
  WRONG behavior is a FAIL), running only checks CI doesn't cover (e.g. manual/live checks the task
  names). Then write the verification record so the orchestrator may land it (schema in
  `.orchestration/SPEC.md`): `.orchestration/state/verified/<UNIT>.json` with `passed`, `verifier` (you —
  a sub-agent, never `orchestrator`), and `evidence` (CI run + per-criterion result). Use the file-edit
  (Write) tool — never shell redirection/`tee`/`cp`; the shell gate protects that directory for everyone
  and will refuse. If any criterion fails, do NOT write a passing record — report the gap so the
  orchestrator spawns a fix.

## Rules

- Execute your unit **fully** — do not stop at "mostly done" or hand partial work back. The orchestrator
  cannot finish it for you (it may not edit).
- Stay within your unit's files/resources (the orchestrator planned parallelism around them). If you must
  touch something outside your unit, STOP and report — do not create a hidden conflict.
- Land is the orchestrator's job, not yours: push + open PR + report `MOL-xxx CI green, ready to land`
  (implementer) or write the verification record (verifier). Do not `gh pr merge`.
- Follow every `AGENTS.md` guardrail (worktree/venv, no main push, non-destructive re-sync, one-liner house
  style, no ledger wipe, etc.).
- Report back a compact result: what you did, the branch/PR, checks run + outcome, and (verifier) the
  verification record path. This is what the orchestrator reviews before landing.
