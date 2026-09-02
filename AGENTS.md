# FanOps — agent context

An intelligent clip + cross-post engine for fan accounts: ingest long-form video → pick moments → cut per-account clips → caption → cross-post (Instagram via Postiz, TikTok via Zernio), driven from a local web cockpit.

Operator docs live in [README.md](README.md) — install, quickstart, the `fanops` command table, and the docs map. This file is the **execution protocol**: what an agent must obey while changing this repo. It does not restate the README.

## Guardrails

**Guardrail #1 — never push to `main`, never force-push to `main`.** Enforced by `.githooks/pre-push`. Work lands through a PR.

**Disjoint hot files.** Lanes own files; a change that edits a hot file owned by another lane is refused. Ownership is declared in [.agents/lanes.json](.agents/lanes.json) and enforced by `scripts/lane_guard.py` (pre-push + the `lane-guard` CI job) and `scripts/pr_collision_guard.py` (refuses a PR whose hot file is open in another PR). Need a file another lane owns → stop and report; do not edit `lanes.json` to take it.

**One ticket, one worktree, its own venv.** `git worktree add`, then a fresh `.venv` per worktree, then `./scripts/setup-hooks.sh` (wires `core.hooksPath=.githooks` and, for MOL-833, `merge.ours.driver=true` so architecture `derived/` `merge=ours` resolves). Worktrees do not share a venv or an index.

**Drift is normal — re-sync, never reset.** Commit or stash → `git fetch origin` → `git merge origin/main` → resolve keeping both sides → re-check → push. With hooks wired, a clean merge refreshes `.reports/architecture/derived/` via `post-merge` (MOL-833) — commit that working-tree regen; do not hand-merge the hashes. Never `git reset --hard`, never `git checkout -B … origin/main`, never abandon a worktree mid-ticket, never force-push. Unreconcilable conflict → stop and report.

**Never wipe or reset the ledger.** Ledger state is production data; destructive ledger operations are not a repair path.

**Cite the symbol, not the line.** A `file:line` is a hint that rots on the next edit — trust the symbol and re-find the line (`STD-DOC-01` in [docs/ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md)). A number copied into prose is a defect.

## Verification

| Situation | Command |
|-----------|---------|
| Before every commit | `./scripts/check.sh` — scoped lint + test-mapping, fast |
| Broad refactor `check.sh` cannot scope | `./scripts/check-full.sh` — full local parity, minutes |
| Architecture gates | `python -m tools.arch [selftest\|ci\|impact\|regen]` |
| CI registry gates | `python -m tools.ci` |
| After push | `gh pr checks --watch` on the PR |

**Do not dispatch workflows to verify a ticket.** Merge gate is PR checks only. E2E runs on the 04:00 UTC schedule via `ci-e2e.yml`.

**Tests are CI-only.** Do not run `pytest` locally — parallel wave suites take the machine down. Claude Code refuses it via `permissions.deny` in `.claude/settings.json`; nothing refuses it in Cursor, so the rule is yours to keep. Write the tests with the change; GitHub CI on the PR executes them.

Any line shift in scanned source requires an architecture regen before the drift gate passes.

## Repository map

| Path | Contents |
|------|----------|
| `src/` | The `fanops` package — all product code |
| `tests/` | Test suite (executed in CI) |
| `tools/` | `arch/` and `ci/` gate engines |
| `scripts/` | Local gates, operator utilities, hooks setup |
| `docs/` | Operator and reference docs; `ENFORCEMENT.md` is the enforcement index |
| `.agents/` | Lane/hot-file ownership (`lanes.json`) |
| `.claude/`, `.cursor/` | Per-platform harness: skills, hooks, settings |
| `.githooks/` | Policy hooks (wired by `core.hooksPath`) |
| `.github/` | CI workflows |
| `.reports/` | Generated architecture knowledge base and contract |
| `requirements/` | Pinned dependency locks |

## Entry points

| Task | Start at |
|------|----------|
| CLI surface | `fanops.cli:main` (`src/fanops/cli.py`) |
| One pipeline pass | `fanops run` |
| Web cockpit | `fanops studio` → `src/fanops/studio/` |
| Publishing path | `src/fanops/post/` |
| Ledger / state | `src/fanops/ledger.py` |
| What enforces a rule | [docs/ENFORCEMENT.md](docs/ENFORCEMENT.md) |
| Env vars and defaults | [docs/CONFIG.md](docs/CONFIG.md) |

## Skills and scoped rulebooks

| Context | When to load |
|---------|--------------|
| [.claude/skills/fanops-hook-hashtag/SKILL.md](.claude/skills/fanops-hook-hashtag/SKILL.md) | Writing or reviewing on-screen hooks and hashtags |
| `src/fanops/post/CLAUDE.md` | Publish path; vendor wire (`posted_text_for` vs `Post.caption`, Zernio `content` / `mediaItems`) |
| [.agents/lanes.json](.agents/lanes.json) | Lane → hot-file ownership, lane → Linear mapping |
| `src/fanops/CLAUDE.md`, `studio/`, `tests/` | Edit-time rulebooks, scoped to that directory |

## Governance

A rule ships as a mechanism or it does not ship. The governance-prose layer was deleted in the 2026-07 theatre census because its stated gates were backed by nothing; `tests/test_governance_tombstone.py` keeps those namespaces dead — do not recreate them. Decision rationale lives in the PR that lands the change: main history is the decision record.

## When to update this file

| Trigger | Action |
|---------|--------|
| A guardrail's enforcing mechanism changes | Update the guardrail and name the new mechanism |
| A new top-level directory appears | Add a repository-map row |
| A skill is added or removed | Update the skills table |
| An agent repeatedly gets something wrong despite this file | Move the detail into a scoped skill and link it |
| This file grows past a quick read | Split detail out; root context stays small |
