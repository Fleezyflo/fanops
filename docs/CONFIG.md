<!-- Generated: 2026-07-03 | Source: docs/CODEMAPS + docs/CODEMAPS/subsystem-traces | Maintained by hand hereafter -->
# FanOps configuration reference

A projection of [CODEMAPS/system-lens-map.md](CODEMAPS/system-lens-map.md) §1.2–1.3 (the authoritative table,
each row with a verified `config.py` read-line). Read that for the read-site line numbers; read THIS for the
operator/dev overview. Each var is either **Studio-settable** (Go-Live tab via `golive._dual_write`, which
writes both `.env` and `os.environ`) or **`.env`/shell-ONLY** (no UI). `Set` column: **S** = Studio-settable,
**.env** = shell-only product knob, **shell** = bootstrap (not an operator `.env` row), **process** = daemon-written
(never operator), **deprecated** = vestigial (not Studio-settable). Defaults are the CODE defaults. (Absolute var counts are intentionally omitted — a
hardcoded total rots; the `FANOPS_*` name-set here is instead enforced against the code by `ARCH-003`.)

## Bootstrap (process environment only)
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_ROOT` | cwd | MohFlow-FanOps tree root; locates `.env`. **Shell/export only — never a `.env` key** (circular). | shell |
| `FANOPS_POSTIZ_ONDEMAND` | `$HOME/postiz-selfhost/postiz-ondemand.sh` | Path override for `fanops up` Postiz on-demand script. | shell |
| `FANOPS_STUDIO_GENERATION` | (daemon) | Written by `daemon.install_studio` into the Studio plist; Studio reads at import. Never set in `.env`. | process |

Because `FANOPS_ROOT` is shell-only, an interactive shell that never exported it silently roots at `cwd` — a different ledger than the daemon (pinned via its plist `WorkingDirectory`). Any `fanops` CLI command other than `fanops daemon status` exits 2 with an `ERROR:` on stderr when it falls back to `cwd` while an installed daemon is pinned elsewhere (`daemon.root_divergence`); `daemon status` still runs and shows both roots. Export `FANOPS_ROOT` (or `cd` to the workspace) to clear it.

Hand-editing `.env` while a long-lived process runs requires restart; Studio go-live/autopilot dual-writes (`os.environ` + `.env`) remain live without restart.

## Publish / live (the dryrun↔live boundary + credentials)
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_LIVE` | derived | THE dryrun↔live switch (set only through `go_live`, confirm-gated) | S |
| `FANOPS_POSTER` | `dryrun` | Legacy global poster backend; unknown→dryrun+warn. Studio can UNSET (clear) only | .env |
| `POSTIZ_URL` | None | Postiz instance base URL | S |
| `POSTIZ_API_KEY` | None | Postiz API key (write-only, never rendered) | S |
| `ZERNIO_API_URL` | `https://zernio.com/api/v1` | Zernio (TikTok) API base | .env |
| `ZERNIO_API_KEY` | None | Zernio API key (write-only) | S |
| `FANOPS_POSTIZ_AUTOSTART` | `1` (on) | Auto-start the local Postiz stack (`postiz_lifecycle`) | .env |
| `FANOPS_POSTIZ_COMPOSE_DIR` | (blank) | Postiz docker-compose dir for `health` | .env |
| `FANOPS_POSTIZ_PUBLISH_PER_MIN` | 4 | Postiz publish throttle (0=off) | .env |
| `FANOPS_MEDIA_PUBLIC_BASE` | None | Public HTTPS base for mirrored clip media (R2/CDN) | .env |
| `R2_ACCOUNT_ID` | None | Cloudflare R2 account id (S3-compatible mirror) | .env |
| `R2_ACCESS_KEY_ID` | None | R2 access key (write-only) | .env |
| `R2_SECRET_ACCESS_KEY` | None | R2 secret key (write-only) | .env |
| `R2_BUCKET` | None | R2 bucket for mirrored clips | .env |
| `FANOPS_ZERNIO_MAX_UPLOAD_MB` | 4 | Zernio TikTok upload preflight cap | .env |

## LLM gates (the AI switch + models)
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_RESPONDER` | `llm` | Vestigial validate-or-refuse — leave unset or set `llm`; any other value is a hard refuse (`doctor`/preflight). Not Studio-settable. | deprecated |
| `FANOPS_LLM_TRANSPORT` | `claude` | LLM CLI transport (`claude` / `cursor`) | S |
| `FANOPS_LLM_MODEL` | per-gate | Force ONE model across all gates | .env |
| `ANTHROPIC_API_KEY` | None | Vestigial — responder uses the `claude` subscription; do not set | deprecated |

## Pipeline: ingest / transcribe / signals / framing
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_CLIP_PROFILE` | `talk` | Global clip-length band | S |
| `FANOPS_VISUAL_START` | on | Strongest-opening-frame cut refinement | .env |
| `FANOPS_SMART_FRAMING` | on | Subject-aware reframe. ON REQUIRES the `[framing]` extra (opencv) — render REFUSES (`ToolchainMissingError`, exit 2) if cv2 absent, not a silent centre-crop. Set `0` to centre-crop without cv2 (a detection miss still fails open to centered) | .env |
| `FANOPS_QUEUE_GATE` | on | Hold new footage as pending until operator queues + releases; also PARKS machine-origin moment re-opens (`adjust.amplify`) on the source until the Make tab releases them (`0` restores auto-ingest and serves re-opens unparked) | .env |
| `FANOPS_AWARE_REFRAME` | off | Global top-third crop bias | .env |
| `FANOPS_WHISPER_MODEL` | duration-aware | Legacy whisper CLI model pin; unset = large-v3→turbo→… by timeout budget | .env |
| `FANOPS_ASR_MODEL` | duration-aware | faster-whisper model pin; unset = large-v3→medium→… by timeout budget. A pin wins verbatim and DISABLES the timeout downgrade — the 2026-07-12 subtitle-garbage incident was a stale `small` pin | .env |
| `FANOPS_ASR_LANGUAGE` | `en,ar` | Comma list enables faster-whisper `multilingual=True` (per-segment detection over all languages — NOT restricted to listed langs); a single value FORCES that language | .env |
| `FANOPS_ISOLATE_VOCALS` | on | Demucs beat-stripping before Whisper | .env |
| `FANOPS_BURN_SUBS` | on | Legacy transcript-caption toggle (render is hook-only since PR 994; flag ignored at render) | .env |
| `FANOPS_SUBTITLE_FONT` | `Arial Unicode MS` | .ass subtitle font | .env |
| `FANOPS_IMPACT_CUT` | off | Impact-cut stitch producer | .env |
| `FANOPS_INTRO_TEASE` | off | Intro-tease stitch producer | .env |
| `FANOPS_ARTIST_NAME` | `Moh Flow` | YouTube title fallback display name | .env |
| `XDG_CACHE_HOME` | `~/.cache` | Whisper checkpoint cache root | .env |
| `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` | certifi | TLS bundle for the faster-whisper runner (setdefault) | .env |

### Speech layers (always-on — no env toggle)

Every transcript segment is stamped `trust_tier` at finalize time (`transcribe._finalize_segments`):

| Tier | Meaning | Production effect |
|---|---|---|
| **full** | L1 ASR quality metadata within thresholds (`avg_logprob`, `no_speech_prob`, `compression_ratio`) AND L2 script coherence for the source language | Consumed by `trusted_segments`, `window_has_trusted_speech`, `excerpt_for_window`, and `segment_trusted` |
| **degraded** | Text present and script-coherent but missing one or more L1 quality keys (typical of legacy whisper-CLI cache) | **Re-transcribe signal**: `_adopt_cached_transcript` refuses incomplete caches (`_cache_is_quality_complete` → `False`), so the next pass re-runs ASR and overwrites with quality-complete segments |
| **rejected** | Empty text, script junk (e.g. Latin flap on an Arabic source), or L1 metadata out of threshold | Never admitted to subs burn, moment pick, hook excerpt, or framing speech classification |

Speech-trust filtering is **invariant always-on** — there is no env switch for it. Production paths never use raw transcript text without passing through the full-tier gate. `real_transcript_signal` is a separate E2E-only helper (proves whisper ran on real audio); do not use it for per-segment trust.

## Per-account differentiation
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_ACCOUNT_CASTING` | on | Per-account moment casting | S |
| `FANOPS_HOOK_ROUTER` | off | Observe-only hook_strategy classifier | .env |

## Learning / bias switches (all default OFF, validation-frozen — see system-lens-map §C.3)
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_VARIANT_LEARNING` | off | A/B hook-learning master gate | S |
| `FANOPS_VARIANT_MIN_POSTS` | 3 | Variant trust: min analyzed posts | .env |
| `FANOPS_VARIANT_MIN_GAP` | 10.0 | Variant trust: min lift margin | .env |
| `FANOPS_LEARN_AMPLIFY` | off | Learn-pass source amplify — **mints new moments/clips/posts unattended**; this flag is the whole gate | S |
| `FANOPS_LEARN_RETIRE` | off | Learn-pass loser retire — **suppresses the loser's clip, moment and unshipped posts unattended**; this flag is the whole gate | S |
| `FANOPS_VARIANT_AMPLIFY` | off | Variant-driven source amplify | S |
| `FANOPS_VARIANT_AMPLIFY_MIN_POSTS` | 8 | Amplify trust: min posts | .env |
| `FANOPS_VARIANT_AMPLIFY_MIN_GAP` | 25.0 | Amplify trust: min gap | .env |
| `FANOPS_VARIANT_AMPLIFY_MIN_STREAK` | 3 | Amplify trust: min distinct windows | .env |
| `FANOPS_VARIANT_UCB` | off | UCB1 bandit caption bias | S |
| `FANOPS_VARIANT_UCB_C` | sqrt(2) | UCB exploration weight | .env |
| `FANOPS_VARIANT_TRANSFER` | off | Cross-surface hook-style transfer | S |
| `FANOPS_VARIANT_TRANSFER_MIN_DONORS` | 2 | Transfer: min donor surfaces | .env |
| `FANOPS_VARIANT_TRANSFER_MAX_HOOKS` | 2 | Transfer: max borrowed styles/caption | .env |
| `FANOPS_ADJUST_PER_SURFACE` | off | Per-surface winner ranking | .env |
| `FANOPS_P4_DIM_BIAS` | off | Creative-dim reach amplify (length/opening/framing) | .env |
| `FANOPS_TIMING_BIAS` | off | Reach-winning publish-hour schedule bias | .env |
| `FANOPS_MOMENT_HOOK_LEARNING` | off | Feed winning hook styles to the moment author | .env |
| `FANOPS_IG_RETENTION_PROOF` | off | Require IG retention to prove learning | .env |
| `FANOPS_P4_MIN_REACH_GAP` | 0.0 | P4/timing comparative reach margin | .env |
| `FANOPS_REQUIRE_FULL_OBJECTIVE` | off | Refuse to amplify a lift-degraded winner | .env |

## Hashtags / Meta Graph
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_CORPUS_TARGET` | 80 | Ceiling on a persona's DERIVED corpus (never padded to reach it) | .env |
| `FANOPS_IG_SCRAPE_USER` | None | Instagram username(s) for hashtag scrape. Comma-separated membership + LRU tiebreak. Lock picker is LRU, under day budget, Safari (no envelope json). Harvest remesure still needs a session file. Each qualifying user may spend up to `FANOPS_HASHTAG_SCRAPE_TRY_CAP` attempts per pass, bounded by that user's UTC day budget (MOL-857/900) | .env |
| `FANOPS_IG_SCRAPE_PASSWORD` | None | Shared Instagram password for hashtag Layer A scrape-login (write-only; never logged). Optional per-user override: append `_` + sanitized username (uppercase, non-alnum → `_`), e.g. user `perca.late` → `…_PASSWORD_PERCA_LATE` | .env |
| `FANOPS_HASHTAG_SCRAPE_TRY_CAP` | 25 | Max tag-attempts **per qualifying scrape user** per lock walk or Layer A pass (bounded by that user’s UTC day budget ~40 on `.hashtag_scrape_cooldown.json` `accounts[user].used`). Lock and remesure share the same `used` counter (+1 per tag-attempt on the wire). Incomplete remesure does not advance `last_complete_pass`. Lock unfinished (all peers at cap) does not stamp `researched_at` (MOL-900) | .env |
| `FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE` | 40 | Max NEW co-tags enqueued to measure per Layer A scrape pass | .env |
| `FANOPS_HASHTAG_SCRAPE_PARALLEL` | 1 | Tags per Layer A wave. Layer A is single-client and serialized (`client_lock`) since MOL-698 — the session-clone fan-out that made this concurrent on the wire earned an account lock, and instagrapi is not thread-safe. Raising it groups tags into a wave; it does NOT emit concurrent requests | .env |
| `FANOPS_HASHTAG_SCRAPE_DELAY` | `1,3` | Shared scrape pace: instagrapi `delay_range` for Layer A harvest, and live Safari XHR sleep in `IgWebSession._json` (injected `_fetch` does not sleep). `"lo,hi"` seconds. `0` disables pacing. Runtime falls back to `1,3` on anything unparseable/negative/inverted (never drops pacing mid-run); `fanops doctor`/`Settings.strict_validate` FAIL LOUD via `config.parse_scrape_delay` | .env |
| `META_GRAPH_TOKEN` | None | Meta Graph token for IG insights / media verification (write-only). Not used by hashtag Layer A refresh (deferred Graph hashtag path) | S |
| `META_GRAPH_TOKEN__<SLUG>` | falls back to global | Per-handle Graph token (dynamic key, write-only) | S |
| `META_IG_USER_ID` | None | IG Business account id (insights / deferred Graph hashtag helpers; set into accounts.json) | .env |
| `META_GRAPH_URL` | `https://graph.facebook.com/v21.0` | Graph base (overridable) | .env |

## Scheduling / infra / Studio
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_OPERATOR_TZ` | `UTC` | Operator timezone for scheduling/buckets (fails closed to UTC) | .env |
| `FANOPS_REALISTIC_CADENCE` | off | 2–3h jittered cadence band | .env |
| `FANOPS_MAX_POSTS_PER_ACCOUNT_PER_DAY` | 2 | Hard cap of queued slots per handle per operator-local day (Approve + Re-spread). `0` = unlimited. Stops a bulk-approve from laying 8–12 posts/day on a 2–3h gap × 24h window | .env |
| `FANOPS_PUBLISH_LEAD_MINUTES` | 0 | Editorial lead window (clamped ≥0) | .env |
| `FANOPS_CONCURRENT_SOURCES` | off | Parallel per-source pipeline | .env |
| `FANOPS_CONCURRENT_WORKERS` | 4 | Concurrency pool size (clamped ≥1) | .env |
| `FANOPS_GC_KEEP_DAYS` | 30 | Manual-gc retention (clamped ≥1) | .env |
| `FANOPS_UPLOAD_MAX_MB` | 2048 | Studio upload body ceiling per request — legacy single-shot POST and each chunked PUT (clamped ≥1) | .env |
| `FANOPS_SOURCE_SHARD_MIN` | 45 | Native inbox videos longer than this (minutes) split once at catalogue into stream-copy parts; 0 = off (clamped ≥0) | .env |
| `FANOPS_SHOW_EXTRAS` | off | Show Footage + Stitches in the Studio Library rail group (U13); default OFF hides the power-user extras | .env |
| `FANOPS_AUTO_ADOPT` | on | Daemon code-drift self-heal: the keeper kickstarts the pump when the SHA it reports in its heartbeat differs from the SHA on disk (`daemon.ensure`). A registered `BoolEnv` — off-words disable, a typo keeps the default ON (was a raw `!= "0"` read where `false`/`off` stayed ON) | .env |

When `FANOPS_POSTIZ_ONDEMAND` is set but points at a missing script, `fanops doctor` adds an informational note (the on-demand plane is a `fanops up` bring-up concern, not a publish gate).

**Machine health:** primary operator channel is `fanops doctor` over `build_health_report`. Studio Home /
Go-Live / `/metrics` are projections of that report. `/healthz` is process liveness only; `fanops up`
is bring-up, not machine-healthy. Channel registry + CI ratchets: [MACHINE_HEALTH.md](MACHINE_HEALTH.md).

**Coverage note:** every trust-gate numeric and every Phase-2 reach-loop bias kill switch is `.env`/shell-only —
an operator-only (Studio-only) deployment cannot turn on the bias actuators or tune their thresholds without
shell access. This is by design (system-lens-map Finding 2).

## Secrets storage (M4 — keychain, not `.env`)

Studio Go-Live now persists the three operator secrets (`POSTIZ_API_KEY`, `ZERNIO_API_KEY`, `META_GRAPH_TOKEN`
and per-handle `META_GRAPH_TOKEN__<slug>`) in the **OS keychain only** — never plaintext `.env`. New writes
scrub the key from `.env`, but **pre-existing plaintext copies are not auto-removed**: rotate or delete any
stale `KEY=...` lines in `.env` by hand (or re-save via Go-Live, which unsets them). `fanops config` labels
a keyring-backed secret `keychain` even when a stale `.env`/`os.environ` copy remains. Non-secret config still
lives in `.env` (owner-only `0600` after every `set_env_var` write).
