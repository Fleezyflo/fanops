<!-- Edit-time rulebook for tests/. Line anchors are a starting point, not a promise — trust the symbol, re-find the line. Commands = root CLAUDE.md. -->
# tests — traps when writing or fixing a test

## How the suites run — CI-ONLY, never locally

**Local test execution is FORBIDDEN** (operator rule): a wave runs many workers on one machine and
parallel suites crash it. Write tests with your change, push, open the PR — GitHub CI executes them
and its run is your evidence. In Claude Code the refusal is mechanical — `.claude/settings.json`
`permissions.deny` blocks `pytest`/`check-full.sh`; **in Cursor nothing blocks it, so the rule is yours
to keep**. `./scripts/check.sh` is scoped lint + test-mapping only. `FANOPS_LOCAL_TESTS=1` is the
operator-only override from a human terminal. What CI runs (reference, not for running):

- CI `unit` job — **the only required status check, and the only job a PR runs**:
  `python -m pytest -q -m "not integration and not slow"` (hermetic, no ffmpeg/whisper/network).
- CI `e2e` job — **`workflow_dispatch` + nightly `schedule` ONLY, never on a push or a PR**
  (`.github/workflows/ci.yml` job `if:`). So a green PR has NOT run it, and a `@pytest.mark.slow` or
  `integration` test you add is unproven until the nightly. It runs
  `python -m pytest -q -m integration -rs` (real ffmpeg/whisper/TTS; `FANOPS_REQUIRE_E2E=1` turns a skip into a
  FAILURE) plus the `-m slow` cross-face UNIT proofs: `test_account_first_e2e.py`,
  `test_hashtag_lifecycle_e2e.py`, `test_review_lanes_e2e.py`, `test_per_persona_e2e.py`.

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
- A registered `FANOPS_*` bool (a `Settings` field annotated `BoolEnv`/`BoolFlag`/`LiveSwitch`) is scrubbed
  automatically — that half of `_LEAKY_ENV` is derived from `settings.BOOL_ENV_FIELDS`, so declaring the field
  IS adding it here. Only a var with NO such registration (a credential, a tuning number, or one `config.py`
  reads directly with no `Settings` field) still needs a hand-added entry, and it goes in `_NON_FLAG_LEAKY`.
- `monkeypatch.delenv(..., raising=False)` is the safe form for a possibly-absent key (this gotcha bit the
  Go-Live tests).

Defect-fix tasks: take the `file:line` + class from the Linear ticket body and write the failing regression test
against the named SYMBOL (anchors drift; the line number in a ticket is a starting point). There is no tracked
defect register — `.reports/` is gitignored apart from `.reports/architecture/`.
