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

- **Postiz** (self-hosted; the headline live publish path for IG):
  `GET /public/v1/integrations` (list connected platforms) -> `POST /public/v1/posts` (schedule with integration_id).
  Auth: `x-api-key` header from env `POSTIZ_API_KEY`; failures -> typed PostizAuthError.
  Schema: account → integration_id stored in `accounts.json`. Publishing feeds the learning loop; IG
  performance is read separately from the Meta Graph (see Insight / `GraphInsightsClient`, the sole IG metric reader).

- **Zernio** (hosted TikTok poster; no learning loop):
  `ZERNIO_API_URL` (default `https://zernio.com/api/v1`) upload + schedule.
  Auth: `ZERNIO_API_KEY` (WRITE-ONLY — never logged/echoed). TikTok-only; a per-account live backend.

- **Poster backends** (post/providers.get_poster):
  - `dryrun` (default, offline, file:// media, never publishes)
  - `postiz` (Postiz self-hosted, the headline live IG path, operator-gated via Studio Go-Live tab)
  - `zernio` (hosted TikTok poster)
  - (`rest`/`mcp` Blotato posters were deleted in the Blotato-removal leg — codemap `insights-culmination.md`)

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
- `FANOPS_POSTER` (dryrun|postiz|zernio); `POSTIZ_URL` + `POSTIZ_API_KEY` for postiz; `ZERNIO_API_URL` + `ZERNIO_API_KEY` for zernio.
- `cfg.is_live_backend` = (postiz or zernio) + that backend's required key present (per-channel readiness aware; dryrun/unknown → never live).

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

**Learning family (default OFF, fail-safe):**
- `FANOPS_VARIANT_LEARNING` (+_MIN_POSTS/_MIN_GAP),
  `FANOPS_VARIANT_UCB` (+_C), `FANOPS_VARIANT_AMPLIFY` (+_MIN_POSTS/_MIN_GAP/_MIN_STREAK),
  `FANOPS_VARIANT_TRANSFER` (+_MAX_HOOKS/_MIN_DONORS).

**Account routing (Account-First, default ON — set =0 to restore fan-to-all):**
- `FANOPS_ACCOUNT_CASTING` (gates `casting.affinity_admits`: when ON, `Moment.affinities` single-owner routing; `[]` fans to all; =0 admits every surface). Studio Go-Live toggle writes `.env` + `os.environ`. Batch targeting (`Batch.target_accounts`) is a SEPARATE hard bound at crosspost (no flag).
- ~~`FANOPS_CAST_PICK_BUDGET`~~ / ~~`FANOPS_CASTING_BIAS`~~ — removed with LLM casting teardown (P11).
- `FANOPS_CREATIVE_VARIATION` — Studio write-only (`golive.set_per_account_hooks`); **not read by `config.py`** (documentation-only until wired).

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
