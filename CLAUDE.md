# FanOps — root instruction router

MOH FLOW FAN OPS: intelligent clip + cross-post engine. Pure-Python `src/` layout (`src/fanops/`), console script `fanops`, Python 3.12–3.13. **This file routes; it does not restate** — domain detail lives in the destinations below and rots when copied here. Precedence when they disagree: **executable source & tests > live GitHub config > the destination doc > this file.**

## Non-negotiable (violating any → stop and ask)

- **Tests are CI-ONLY — never run `pytest` / `check-full.sh` locally.** Parallel wave workers each running the suite crash this machine. In Claude Code the refusal is mechanical (`.claude/settings.json` `permissions.deny`); **in Cursor nothing blocks it, so the rule is yours to keep.** `./scripts/check.sh` is the local gate and runs no tests. `FANOPS_LOCAL_TESTS=1` is the operator-only override from a human terminal.
- **Never mass-reformat** — no `black`, no `ruff format`. The compact one-liner house style (E701/E702/E401/E501 ignored) is deliberate; rationale in `pyproject.toml`. Match the surrounding style.
- **Never raise the 60s pytest timeout** to make a hang pass — it is a deadlock guardrail (ledger SQLite busy_timeout) and a hanging test IS the bug.
- **Never run live `fanops` verbs speculatively** — publish/metrics hit Postiz and the Meta Graph for real. Read-only verbs and tests only, unless the operator asks.
- **Nothing auto-publishes.** Every `Post` is born `awaiting_approval`; only `Ledger.approve_post` promotes to `queued`, and only `queued` ever publishes. Do not add a mint site, and never state-set to `queued` anywhere else (`src/fanops/CLAUDE.md`).
- **Never wipe or reset the ledger.** Schema changes are additive-with-default, or a drop-migration hop plus a `SCHEMA_VERSION` bump. Old ledgers must still load.
- `.claude/workflows/*.js` are tracked, load-bearing build workflows — never delete. One orchestrator landing session at a time: `git fetch` + `gh pr view` the target before any merge (parallel orchestrators caused double-merges).

## Commands

- Install `pip install -e '.[dev,framing]'`. `[framing]` (OpenCV) is REQUIRED, not optional — `smart_framing` defaults ON and the render REFUSES without cv2; `FANOPS_SMART_FRAMING=0` centre-crops without it. Lazy extras: `[studio]` (Flask cockpit), `[transcribe]` (whisper CLI), `[compose]` (MoviePy).
- Lint `ruff check .` · pre-commit gate `./scripts/check.sh` (scoped ruff + changed-src-has-a-test; seconds, runs no tests) · Studio `fanops studio` (localhost:8787, needs `[studio]`).
- **What CI proves:** the `unit` job is the ONLY required status check. The real-tooling `e2e` job runs on `workflow_dispatch` and the nightly `schedule` ONLY — never on a push or a pull request — so a green PR has NOT run the integration suite. Authority: the job conditions in `.github/workflows/ci.yml` and branch protection; index in `docs/ENFORCEMENT.md`.

## Where to look (nested `CLAUDE.md` files load automatically when you edit under their directory)

| You are… | Read |
|---|---|
| editing anything under `src/fanops/` | `src/fanops/CLAUDE.md` — invariants, sibling-parity traps, the "zero callers is a lead" deletion rule |
| touching publish / schedule / reconcile | `src/fanops/post/CLAUDE.md` + `docs/CODEMAPS/subsystem-traces/C6_crosspost_publish_post.md` |
| touching a Studio route / action / view / tab | `src/fanops/studio/CLAUDE.md` — it owns tab and approval-lifecycle semantics — plus the C9/C10 traces |
| writing or fixing a test | `tests/CLAUDE.md` — the `_LEAKY_ENV` gotcha and the deadlock-guardrail timeout |
| changing hashtags | `docs/CODEMAPS/hashtag-lifecycle.md` |
| changing the learning / insights loop | `docs/CODEMAPS/insights-culmination.md` |
| changing clip framing / reframe | `docs/CODEMAPS/subsystem-traces/C3_clip_production_framing.md` + `docs/design/reframe/` |
| changing personas, levers or casting | `docs/LEVERS.md` + `docs/CODEMAPS/subsystem-traces/C4_moments_casting_personas.md` |
| asking what an env var or flag does | `docs/CONFIG.md` · `docs/FLAGS.md` |
| asking what actually enforces a rule, or how to work here | `docs/ENFORCEMENT.md` (`docs/ENGINEERING_STANDARDS.md` is the craft layer — guidance, not a gate) · `AGENTS.md` (worktree, PR, parallelism) |
| looking for a module's owner or the whole map | `docs/CODEMAPS/README.md` → `docs/CODEMAPS/full-trace-index.md`; `python -m tools.arch impact --base <sha>` |
