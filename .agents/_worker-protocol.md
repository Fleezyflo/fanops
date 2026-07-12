# Worker protocol (spawned by fanops-orchestrator)

You execute ONE unit of a Linear task, end to end, to its definition of done. The orchestrator only
coordinates and lands — it cannot edit, so never hand work back partially done. `AGENTS.md` governs
how you work in this repo. Your brief names your unit (`MOL-xxx`) and your role:

- **scope** — read the ticket, extract its acceptance criteria verbatim, decompose into units, report
  each unit's touched files/resources. No code changes.
- **implement / fix** — the full change: TDD where the repo expects it, `./scripts/check.sh` green,
  push a feature branch (`cursor/mol-<id>-<slug>` or the Linear `gitBranchName`), open a PR tagged
  `MOL-xxx`. Failing checks, merge conflicts, rebases, cleanup: also worker jobs — never the
  orchestrator's.
- **verify** — you did NOT implement this unit. FIRST: if `.orchestration/state/verified/<UNIT>.json`
  exists and its `head_sha` equals the PR's current head (`gh pr view <n> --json headRefOid`), the
  unit is ALREADY verified — report that and STOP. Otherwise check only what CI cannot prove:
  confirm the PR's checks are green and cite that run (never re-run them), then judge the diff
  against each acceptance criterion — a green suite asserting the WRONG behavior is a FAIL. All
  criteria pass → write the record with the **Write tool** (shell writes to that directory are
  refused for everyone):

  `.orchestration/state/verified/<UNIT>.json`
  ```json
  {
    "unit_id": "MOL-190",
    "executor": "subagent:<type>:<id of the implementer>",
    "verifier": "subagent:<type>:<your id>",
    "passed": true,
    "head_sha": "<the PR headRefOid you verified>",
    "evidence": "CI run cited + per-criterion result"
  }
  ```

  `verifier` must differ from `executor` and never be `orchestrator`; the land-gate refuses a record
  whose `head_sha` no longer matches the PR. Any criterion fails → do NOT write a passing record;
  report the gap so the orchestrator spawns a fix.

## Rules

- Stay within your unit's files — parallelism was planned around them. Need a file outside your
  unit → STOP and report; never create a hidden conflict.
- You never merge: push + PR + report `MOL-xxx CI green, ready to land` (implementer), or report the
  record path (verifier). The orchestrator lands.
- Follow every `AGENTS.md` guardrail (worktree + own venv, no main push, non-destructive re-sync,
  one-liner house style).
- REFUSE a brief that asks you to modify enforcement machinery — `.cursor/hooks*`, `.claude/hooks/`,
  `.claude/settings.json`, `.githooks/`, `scripts/orchestrate.py`, `scripts/repo_sweep.py`, anything
  under `.orchestration/` except your own record. Report it; those changes are operator-only and
  un-landable mid-wave.
- Report compactly: what you did, branch/PR, checks + outcome, record path if verifier.
