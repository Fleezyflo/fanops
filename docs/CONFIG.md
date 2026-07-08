<!-- Generated: 2026-07-03 | Source: docs/CODEMAPS + docs/CODEMAPS/subsystem-traces | Maintained by hand hereafter -->
# FanOps configuration reference — 64 environment variables

A projection of [CODEMAPS/system-lens-map.md](CODEMAPS/system-lens-map.md) §1.2–1.3 (the authoritative table,
each row with a verified `config.py` read-line). Read that for the read-site line numbers; read THIS for the
operator/dev overview. **63 distinct env vars** — **13 Studio-settable** (Go-Live tab via `golive._dual_write`,
which writes both `.env` and `os.environ`), **51 `.env`/shell-ONLY** (no UI). `Set` column: **S** = Studio-settable,
`.env` = shell-only. Defaults are the CODE defaults.

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
| `FANOPS_RESPONDER` | `manual` | THE explicit AI switch (`llm`/`manual`); presence of `claude` never auto-enables | S |
| `FANOPS_LLM_MODEL` | per-gate | Force ONE model across all gates | .env |
| `ANTHROPIC_API_KEY` | None | VESTIGIAL — responder uses the `claude` subscription; not required | .env |

## Pipeline: ingest / transcribe / signals / framing
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_CLIP_PROFILE` | `talk` | Global clip-length band | S |
| `FANOPS_VISUAL_START` | on | Strongest-opening-frame cut refinement | .env |
| `FANOPS_SMART_FRAMING` | on | Subject-aware reframe (fail-open to centered crop) | .env |
| `FANOPS_AWARE_REFRAME` | off | Global top-third crop bias | .env |
| `FANOPS_WHISPER_MODEL` | `turbo` | Legacy whisper CLI model | .env |
| `FANOPS_ASR_MODEL` | `medium` | faster-whisper model | .env |
| `FANOPS_ASR_LANGUAGE` | `en,ar` | Whisper candidate languages | .env |
| `FANOPS_ISOLATE_VOCALS` | on | Demucs beat-stripping before Whisper | .env |
| `FANOPS_BURN_SUBS` | off | Burn transcript captions (the on-screen hook is a separate layer) | .env |
| `FANOPS_SUBTITLE_FONT` | `Arial Unicode MS` | .ass subtitle font | .env |
| `FANOPS_IMPACT_CUT` | off | Impact-cut stitch producer | .env |
| `FANOPS_INTRO_TEASE` | off | Intro-tease stitch producer | .env |
| `FANOPS_ARTIST_NAME` | `Moh Flow` | YouTube title fallback display name | .env |
| `XDG_CACHE_HOME` | `~/.cache` | Whisper checkpoint cache root | .env |
| `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` | certifi | TLS bundle for the faster-whisper runner (setdefault) | .env |

## Per-account differentiation
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_CREATIVE_VARIATION` | on | Per-account hooks/renders | S |
| `FANOPS_ACCOUNT_CASTING` | on | Per-account moment casting | S |
| `FANOPS_HOOK_ROUTER` | off | Observe-only hook_strategy classifier | .env |

## Learning / bias switches (all default OFF, validation-frozen — see system-lens-map §C.3)
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_VARIANT_LEARNING` | off | A/B hook-learning master gate | S |
| `FANOPS_VARIANT_MIN_POSTS` | 3 | Variant trust: min analyzed posts | .env |
| `FANOPS_VARIANT_MIN_GAP` | 10.0 | Variant trust: min lift margin | .env |
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
| `FANOPS_HASHTAG_TRENDS` | on | Background Graph reach sampling in `hashtags refresh` | .env |
| `META_GRAPH_TOKEN` | None | Meta Graph token for hashtag trends (write-only) | .env |
| `META_GRAPH_TOKEN__<SLUG>` | falls back to global | Per-handle Graph token (dynamic key, write-only) | S |
| `META_IG_USER_ID` | None | IG Business account id for `ig_hashtag_search` (set into accounts.json, not env) | .env |
| `META_GRAPH_URL` | `https://graph.facebook.com/v21.0` | Graph base (overridable) | .env |

## Scheduling / infra / Studio
| Var | Default | Effect | Set |
|---|---|---|---|
| `FANOPS_OPERATOR_TZ` | `UTC` | Operator timezone for scheduling/buckets (fails closed to UTC) | .env |
| `FANOPS_REALISTIC_CADENCE` | off | 2–3h jittered cadence band | .env |
| `FANOPS_PUBLISH_LEAD_MINUTES` | 0 | Editorial lead window (clamped ≥0) | .env |
| `FANOPS_CONCURRENT_SOURCES` | off | Parallel per-source pipeline | .env |
| `FANOPS_CONCURRENT_WORKERS` | 4 | Concurrency pool size (clamped ≥1) | .env |
| `FANOPS_GC_KEEP_DAYS` | 30 | Manual-gc retention (clamped ≥1) | .env |
| `FANOPS_UPLOAD_MAX_MB` | 2048 | Studio upload body ceiling (clamped ≥1) | .env |

**Coverage note:** every trust-gate numeric and every Phase-2 reach-loop bias kill switch is `.env`/shell-only —
an operator-only (Studio-only) deployment cannot turn on the bias actuators or tune their thresholds without
shell access. This is by design (system-lens-map Finding 2).
