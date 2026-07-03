<!-- Generated: 2026-07-03 | Source: docs/CODEMAPS + docs/CODEMAPS/subsystem-traces | Maintained by hand hereafter -->
# tests — suite layout & gotchas

~239 test files. Product/CI commands live in the ROOT `CLAUDE.md`; this file = the traps.

## Layout & markers

- **`tests/*.py`** — the fast UNIT suite (hermetic: no ffmpeg/whisper/network). Run:
  `python -m pytest -q -m "not integration"` (the CI `unit` job).
- **`tests/integration/*.py`** — real-tooling E2E (`@pytest.mark.integration`): real ffmpeg/whisper/TTS
  (`say`/`espeak`) + live-backend probes. Run: `python -m pytest -q -m integration -rs`. Skips cleanly
  when tooling is absent LOCALLY. The only declared marker (`pyproject.toml [markers]`).
- **`FANOPS_REQUIRE_E2E=1`** (CI `e2e` job, `conftest.py` + `integration/test_e2e_real.py`) turns the
  "tooling absent" skip into a FAILURE — so the real-tooling path is guaranteed to execute in CI, never
  silently skipped (audit H10).
- Big cross-face proofs (slow UNITs, no marker): `test_account_first_e2e.py`, `test_hashtag_lifecycle_e2e.py`,
  `test_review_lanes_e2e.py`, `test_e2e_transcript_assertion.py`.

## Hard rules

- **The 60s global timeout (`pytest-timeout`, `pyproject.toml`) is a DEADLOCK GUARDRAIL** — it exists so a
  concurrency regression that self-deadlocks on the ledger `flock` fails fast instead of hanging (30s lock
  timeout × N). A hanging test IS the bug — NEVER raise the timeout to make it pass.
- **Run under the venv** (`source .venv/bin/activate`) — bare `pytest` mis-reports the `mocker` fixture.

## The os.environ leak gotcha

`conftest.py` strips a `_LEAKY_ENV` allowlist before every test, because a test that calls `load_dotenv`
pulls the OPERATOR's live repo `.env` (e.g. `FANOPS_POSTER=postiz`, a real `POSTIZ_API_KEY`, default-ON
flags like `FANOPS_CREATIVE_VARIATION=1`) into `os.environ` — and `load_dotenv` does NOT override an
already-set var, so a leaked value would silently make tests assert against the operator's config instead of
the CODE default. A test that WANTS a live backend or a non-default flag sets it explicitly via
`monkeypatch` (which gets clean teardown). When adding a new default-ON flag or credential env var that a
repo `.env` might carry, add it to `_LEAKY_ENV`. Note: `monkeypatch.delenv(..., raising=False)` for an
absent key is the safe form (an env-leak gotcha bit the Go-Live tests).
