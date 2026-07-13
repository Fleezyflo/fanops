<!-- Edit-time rulebook for tests/. Anchors verified 2026-07-03. Commands = root CLAUDE.md. -->
# tests — traps when writing or fixing a test

## How the suites run — never on the operator's Mac; CI and Linux sandboxes execute them

**The suite never runs on the operator's Mac** (machine rule, not a repo rule): a wave runs many
workers on one machine and parallel suites crash it. The Darwin test gate
(`.claude/hooks/darwin_test_gate.py`) denies `pytest`/`check-full.sh` there, the orchestration gate
additionally refuses them during waves, and `./scripts/check.sh` is scoped lint + test-mapping
only. `FANOPS_LOCAL_TESTS=1` is the operator-only override from a human terminal. On Linux the
suite RUNS: GitHub CI executes it on every PR (its run is your evidence), and a Claude cloud
session runs `./scripts/check-full.sh` before pushing. What CI runs:

- CI `unit` job: `python -m pytest -q -m "not integration and not slow"` (hermetic, no ffmpeg/whisper/network).
- CI `e2e` job: `python -m pytest -q -m integration -rs` (real ffmpeg/whisper/TTS; `FANOPS_REQUIRE_E2E=1`
  turns a skip into a FAILURE) plus the `@pytest.mark.slow` cross-face UNIT proofs (`-m slow`):
  `test_account_first_e2e.py`, `test_hashtag_lifecycle_e2e.py`, `test_review_lanes_e2e.py`,
  `test_per_persona_e2e.py`.

## Hard rules

- **The 60s global timeout (`pyproject.toml:77`, pytest-timeout) is a DEADLOCK GUARDRAIL** — it exists so a
  concurrency regression that self-deadlocks on the ledger SQLite busy_timeout fails fast instead of hanging. A hanging test
  IS the bug. NEVER raise the timeout to make a test pass.

## The os.environ leak gotcha (bites new-flag/new-credential tests)

`conftest.py` strips a `_LEAKY_ENV` allowlist (`:35`) before every test via the autouse `_hermetic_publish_env`
fixture (`:62`), because a test that calls `load_dotenv` pulls the OPERATOR's live repo `.env`
(`FANOPS_POSTER=postiz`, a real `POSTIZ_API_KEY`, default-ON flags like `FANOPS_CREATIVE_VARIATION=1`) into
`os.environ` — and `load_dotenv` does NOT override an already-set var, so a leaked value would silently make the
test assert against the operator's config instead of the CODE default.

- A test that WANTS a live backend or a non-default flag sets it explicitly via `monkeypatch` (clean teardown).
- When you add a new default-ON flag or credential env var a repo `.env` might carry, ADD it to `_LEAKY_ENV`.
- `monkeypatch.delenv(..., raising=False)` is the safe form for a possibly-absent key (this gotcha bit the
  Go-Live tests).

Defect-fix tasks: the exact `file:line` + class for each MOL-* issue is in `.reports/issue-register-2026-07-03.md`
— write the failing regression test against that line, not a re-derived guess.
