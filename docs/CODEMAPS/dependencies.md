<!-- Generated: 2026-06-19 | Files scanned: pyproject.toml, config.py, llm.py, post/*, .github/workflows/ci.yml | Token estimate: ~770 | incl. M6 FANOPS_INTRO_TEASE + content-lifecycle FANOPS_GC_KEEP_DAYS -->
# FanOps Dependencies

## External binaries (subprocess, every call hard-timeout-bounded)

| Tool | Used by | Bound | Absent behavior |
|---|---|---|---|
| ffprobe | ingest/discover probes | 30s | ToolchainMissingError (ingest) / fail-soft (discover) |
| ffmpeg | clip render, signals, overlay burn, thumbnails | 600s (thumbs 60s, `-filters` probe 30s) | per-unit error / overlay fails open (skip subtitles) |
| whisper | transcribe (optional `[transcribe]`/`[asr]` extra) | 2700s | Source -> error, retriable |
| yt-dlp | `pull` download (outside the flock) | 600s | ToolchainMissingError -> exit 2 |
| claude CLI | llm.py `claude -p` (responder; subscription/OAuth login, NOT ANTHROPIC_API_KEY) | 300s | preflight blocks the run |

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

- **Zernio** (hosted TikTok poster; no learning loop):
  `ZERNIO_API_URL` (default `https://zernio.com/api/v1`) upload + schedule.
  Auth: `ZERNIO_API_KEY` (WRITE-ONLY — never logged/echoed). TikTok-only; a per-account live backend.

- **Poster backends** (post/__init__.get_poster):
  - `dryrun` (default, offline, file:// media, never publishes)
  - `postiz` (Postiz self-hosted, no learning loop, operator-gated via Studio Go-Live tab)
  - `zernio` (hosted TikTok poster, no learning loop)
  - `rest` (BlotatoRestPoster, learning-loop capable; Blotato being retired)
  - `mcp` (blotato_mcp wrapper, LEGACY)

## Python deps (pyproject)

Core: pydantic>=2.7, requests>=2.31, python-dotenv>=1.0, yt-dlp>=2024.0 · py 3.12–3.13.
Extras:
- `[dev]` pytest/pytest-mock/pytest-timeout(60s global guardrail)/ruff
- `[studio]` flask>=3.0 (imported LAZILY — core install runs Flask-free)
- `[transcribe]` openai-whisper
- `[asr]` demucs>=4.0 + faster-whisper>=1.0 + certifi (accurate music ASR: Demucs strips the beat, faster-whisper runs the CTranslate2 model; imported LAZILY + FAIL-OPEN — no-[asr] install isolates to raw audio and falls back to the legacy `whisper` CLI)
- `[compose]` moviepy>=2.0 (imported LAZILY in compose.py; core install MoviePy-free)

## Env flags (config.py; bools parse "1/true/yes/on")

**Posting backend:**
- `FANOPS_POSTER` (dryrun|postiz|zernio|rest|mcp); `BLOTATO_API_KEY` for rest/mcp; `POSTIZ_URL` + `POSTIZ_API_KEY` for postiz; `ZERNIO_API_URL` + `ZERNIO_API_KEY` for zernio.
- `cfg.is_live_backend` = (rest or postiz or zernio) + that backend's required key present.

**Pipeline:**
- `FANOPS_ARTIST_NAME`, `FANOPS_BURN_SUBS` (default OFF), `FANOPS_SUBTITLE_FONT`, `FANOPS_WHISPER_MODEL` (legacy whisper CLI, default turbo),
  `FANOPS_PUBLISH_LEAD_MINUTES`, `FANOPS_RESPONDER` (manual default), `FANOPS_VISUAL_START` (ON).
- ASR ([asr] extra): `FANOPS_ASR_MODEL` (faster-whisper model, default medium), `FANOPS_ASR_LANGUAGE` (default "en,ar" — multilingual EN+AR), `FANOPS_ISOLATE_VOCALS` (Demucs beat-strip before ASR, default ON, fail-open).
- `FANOPS_GC_KEEP_DAYS` (content-lifecycle: manual-`gc` retention window, default 30, clamped ≥1; sweeps retired/analyzed renders + 05_scheduled payloads, never 06_published).

**Structural hooks (M2–M6, ALL default OFF):**
- `FANOPS_HOOK_ROUTER` (M2: read-only Moment hook_strategy classifier), `FANOPS_IMPACT_CUT`
  (M4: produce + render operator-approved impact-cuts; needs the router on to reserve moments),
  `FANOPS_INTRO_TEASE` (M6: pair a clean clip with a third-party intro asset + compose-PREPEND a
  "wait for it" tease; needs the router on + `FANOPS_RESPONDER=llm` for the LLM-vision matcher gate).

**Learning family (default OFF, fail-safe — except `FANOPS_CREATIVE_VARIATION`, default ON):**
- `FANOPS_CREATIVE_VARIATION` (default ON; =0 restores the legacy single shared-clip path), `FANOPS_VARIANT_LEARNING` (+_MIN_POSTS/_MIN_GAP),
  `FANOPS_VARIANT_UCB` (+_C), `FANOPS_VARIANT_AMPLIFY` (+_MIN_POSTS/_MIN_GAP/_MIN_STREAK),
  `FANOPS_VARIANT_TRANSFER` (+_MAX_HOOKS/_MIN_DONORS).

**Account-casting (Account-First, default ON — set =0 to restore fan-to-all):**
- `FANOPS_ACCOUNT_CASTING` (gates the per-account moment-casting stage in `casting.py`: default ON — a cast
  Moment fans ONLY to its `affinities` accounts; =0 leaves `affinities=[]` and is render/post byte-identical);
  `FANOPS_CAST_PICK_BUDGET` (per-account winner cap, default 6, clamped ≥1). The Go-Live tab's **casting
  toggle** (Studio, go_live-style) now writes `FANOPS_ACCOUNT_CASTING` to `.env` + `os.environ`, so the flag
  is UI-reachable, not env-only. Batch targeting (`Batch.target_accounts`) is a SEPARATE, always-on hard
  bound enforced at crosspost (no flag) — distinct from this casting narrow.

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
