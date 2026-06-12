<!-- Generated: 2026-06-13 | Files scanned: pyproject.toml, config.py, llm.py, post/* | Token estimate: ~600 -->
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

- **Blotato REST** (the only posting backend that talks to the world):
  `POST /media/uploads` presign (https-only PUT enforced) -> binary PUT (size-scaled timeout
  60s+2s/MB cap 600s, 500MB max) -> `POST /v2/posts` -> `GET /v2/posts/:id` (reconcile poll)
  -> metrics list. Auth: `blotato-api-key` header from env `BLOTATO_API_KEY`; 401 -> typed
  BlotatoAuthError halts the queue (response bodies withheld from auth errors).
  Retry shape: 429 jittered bounded backoff; 5xx/network-after-send -> needs_reconcile (never re-POST).
- Poster backends (post/__init__.get_poster): `dryrun` (default, offline, file:// media) ·
  `rest` (BlotatoRestPoster) · `mcp` (blotato_mcp wrapper).

## Python deps (pyproject)

Core: pydantic>=2.7, requests>=2.31, python-dotenv>=1.0, yt-dlp>=2024.0 · py 3.12–3.13.
Extras: `[dev]` pytest/pytest-mock/pytest-timeout(60s global guardrail)/ruff ·
`[studio]` flask>=3.0 (imported LAZILY — core install runs Flask-free) · `[transcribe]` openai-whisper.

## Env flags (config.py; bools parse "1/true/yes/on")

- Live: `FANOPS_POSTER` (dryrun|rest|mcp), `BLOTATO_API_KEY` — `cfg.is_live_backend` = both set.
- Pipeline: `FANOPS_ARTIST_NAME`, `FANOPS_BURN_SUBS`, `FANOPS_SUBTITLE_FONT`,
  `FANOPS_WHISPER_MODEL`, `FANOPS_PUBLISH_LEAD_MINUTES`, `FANOPS_RESPONDER` (manual default).
- Learning family (ALL default OFF, fail-safe): `FANOPS_CREATIVE_VARIATION`,
  `FANOPS_VARIANT_LEARNING` (+_MIN_POSTS/_MIN_GAP), `FANOPS_VARIANT_UCB` (+_C),
  `FANOPS_VARIANT_AMPLIFY` (+_MIN_POSTS/_MIN_GAP/_MIN_STREAK),
  `FANOPS_VARIANT_TRANSFER` (+_MAX_HOOKS/_MIN_DONORS).
- Vestigial: `ANTHROPIC_API_KEY` (kept for third-party/Bedrock setups; responder uses `claude` login).

## CI (.github/workflows/ci.yml)

Two jobs: `unit (fast, no toolchain)` = `pytest -m "not integration"` · `real-tooling E2E
(must run, not skip)` = integration suite with real ffmpeg/whisper/espeak, `FANOPS_REQUIRE_E2E=1`
(a skip FAILS). No ruff step in CI yet (local pre-push hook runs the full suite).
