<!-- Generated: 2026-06-18 | Files scanned: pyproject.toml, config.py, llm.py, post/*, .github/workflows/ci.yml | Token estimate: ~730 | CI ruff-gate corrected -->
# FanOps Dependencies

## External binaries (subprocess, every call hard-timeout-bounded)

| Tool | Used by | Bound | Absent behavior |
|---|---|---|---|
| ffprobe | ingest/discover probes | 30s | ToolchainMissingError (ingest) / fail-soft (discover) |
| ffmpeg | clip render, signals, overlay burn, thumbnails | 600s (thumbs 60s, `-filters` probe 30s) | per-unit error / overlay fails open (skip subtitles) |
| whisper | transcribe (optional `[transcribe]` extra) | 1800s | Source -> error, retriable |
| yt-dlp | `pull` download (outside the flock) | 600s | ToolchainMissingError -> exit 2 |
| claude CLI | llm.py `claude -p` (responder; subscription/OAuth login, NOT ANTHROPIC_API_KEY) | 180s | preflight blocks the run |

## Services

- **Blotato REST** (full learning loop: metrics + reconcile + cutover):
  `POST /media/uploads` presign (https-only PUT enforced) -> binary PUT (size-scaled timeout
  60s+2s/MB cap 600s, 500MB max) -> `POST /v2/posts` -> `GET /v2/posts/:id` (reconcile poll)
  -> metrics list. Auth: `blotato-api-key` header from env `BLOTATO_API_KEY`; 401 -> typed
  BlotatoAuthError halts the queue (response bodies withheld from auth errors).
  Retry shape: 429 jittered bounded backoff; 5xx/network-after-send -> needs_reconcile (never re-POST).

- **Postiz** (free, self-hosted alternative; no learning loop):
  `GET /public/v1/integrations` (list connected platforms) -> `POST /public/v1/posts` (schedule with integration_id).
  Auth: `x-api-key` header from env `POSTIZ_API_KEY`; failures -> typed PostizAuthError.
  Schema: account → integration_id stored in `accounts.json` (shared with Blotato model).

- **Poster backends** (post/__init__.get_poster):
  - `dryrun` (default, offline, file:// media, never publishes)
  - `rest` (BlotatoRestPoster, learning-loop capable)
  - `postiz` (Postiz self-hosted, no learning loop, operator-gated via Studio Go-Live tab)
  - `mcp` (blotato_mcp wrapper, LEGACY)

## Python deps (pyproject)

Core: pydantic>=2.7, requests>=2.31, python-dotenv>=1.0, yt-dlp>=2024.0 · py 3.12–3.13.
Extras:
- `[dev]` pytest/pytest-mock/pytest-timeout(60s global guardrail)/ruff
- `[studio]` flask>=3.0 (imported LAZILY — core install runs Flask-free)
- `[transcribe]` openai-whisper
- `[compose]` moviepy>=2.0 (imported LAZILY in compose.py; core install MoviePy-free)

## Env flags (config.py; bools parse "1/true/yes/on")

**Posting backend:**
- `FANOPS_POSTER` (dryrun|rest|postiz|mcp); `BLOTATO_API_KEY` for rest; `POSTIZ_URL` + `POSTIZ_API_KEY` for postiz.
- `cfg.is_live_backend` = (rest or postiz) + required keys present.

**Pipeline:**
- `FANOPS_ARTIST_NAME`, `FANOPS_BURN_SUBS`, `FANOPS_SUBTITLE_FONT`, `FANOPS_WHISPER_MODEL`,
  `FANOPS_PUBLISH_LEAD_MINUTES`, `FANOPS_RESPONDER` (manual default), `FANOPS_HOOK_EDITOR` (ON), `FANOPS_VISUAL_START` (ON).

**Structural hooks (M2–M4, ALL default OFF):**
- `FANOPS_HOOK_ROUTER` (M2: read-only Moment hook_strategy classifier), `FANOPS_IMPACT_CUT`
  (M4: produce + render operator-approved impact-cuts; needs the router on to reserve moments).

**Learning family (ALL default OFF, fail-safe):**
- `FANOPS_CREATIVE_VARIATION`, `FANOPS_VARIANT_LEARNING` (+_MIN_POSTS/_MIN_GAP),
  `FANOPS_VARIANT_UCB` (+_C), `FANOPS_VARIANT_AMPLIFY` (+_MIN_POSTS/_MIN_GAP/_MIN_STREAK),
  `FANOPS_VARIANT_TRANSFER` (+_MAX_HOOKS/_MIN_DONORS).

**Autonomous:**
- `FANOPS_RESPONDER=llm` (set durably by `autopilot`).

**Vestigial:**
- `ANTHROPIC_API_KEY` (kept for third-party/Bedrock setups; responder uses `claude` login).

## CI (.github/workflows/ci.yml)

Two jobs: `unit (fast, no toolchain)` runs **`ruff check .` (whole repo, a GATE — pyflakes F +
pycodestyle E house ruleset) THEN `pytest -m "not integration"`** with a coverage report (report-only,
no `--cov-fail-under`) · `real-tooling E2E (must run, not skip)` = integration suite with real
ffmpeg/whisper/espeak, `FANOPS_REQUIRE_E2E=1` (a skip FAILS). ALWAYS run `ruff check .` whole-repo before
pushing — a per-file ruff pass misses unused-import (F401) regressions the CI gate catches.
