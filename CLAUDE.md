# FanOps — project notes for Claude

MOH FLOW FAN OPS: intelligent clip + cross-post engine. Pure-Python `src/` layout
(`src/fanops/`), console script `fanops`, Python 3.12–3.13.

## Commands

- Install: `pip install -e '.[dev]'` — extras: `[studio]` (Flask cockpit, imported lazily), `[transcribe]` (whisper CLI), `[compose]` (MoviePy produced-clip compositing — `fanops compose`, lazy + fail-open)
- Fast unit suite (CI `unit` job): `python -m pytest -q -m "not integration"`
- Integration suite (CI `e2e` job): `python -m pytest -q -m integration -rs` — needs real ffmpeg/ffprobe/whisper/espeak on PATH; skips locally when absent; CI sets `FANOPS_REQUIRE_E2E=1` so a skip fails
- Lint: `ruff check .` (pyflakes F + pycodestyle E only)
- Studio dev server: `fanops studio` (localhost:8787; requires `[studio]` extra)

## Constraints

- NEVER mass-reformat: no `black`, no `ruff format`. The compact one-liner house style
  (E701/E702/E401/E501 ignored) is deliberate — rationale in pyproject.toml comments.
- The global 60s pytest timeout is a deadlock guardrail (ledger flock). A hanging test
  is the bug; don't raise the timeout to make it pass.
- The `fanops` CLI has live verbs that hit external services (Blotato). Don't run it
  speculatively; tests and read-only verbs only unless the operator asks.
- `.claude/workflows/*.js` are tracked, load-bearing build workflows — never delete.
