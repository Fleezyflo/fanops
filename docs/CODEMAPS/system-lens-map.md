<!-- Generated: 2026-07-09 | Method: source read (large-chunk reads of ~40 src/fanops modules + studio routes + live control files), every claim carries a personally-verified file:line; no prior-conclusion inputs, navigation aids treated as maps not truth | Files read: 41 | Token estimate: ~7800 -->
# FanOps System Lens Map — three subsystem lenses + cross-cutting gates

Every statement below carries a `file:line` I verified against source. Where source could not settle a
question it is marked **UNRESOLVED**. Findings I observed (not descriptions) are quarantined in the final
`## Findings` section. Line numbers are as of the 2026-07-03 working tree.

---

## Lens 1 — Video from ingestion onward, and how every step is configured

### 1.1 The processing chain, function by function

Driver: `pipeline.advance(cfg, base_time)` (`pipeline.py:382`). One pass = ingest (short txn) → lock-free
produce → one main txn of state-flip stages → reconcile (out of lock) → publish (out of lock) → summary.
The main-txn saves ONLY on clean exit; an uncaught raise rolls the whole pass back by design
(`pipeline.py:408-416`).

**Intake paths (enumerated).** Three `source_origin` channels, all funnelling through one catalogue spine:
- **drop** — `ingest.ingest_drops(...)` default `origin="drop"` (`ingest.py:201`), scanning `cfg.inbox`
  (`01_inbox`). Called inside `advance` in a short transaction FIRST (`pipeline.py:393-394`).
- **url** — `ingest.download_url` shells `yt-dlp` into an isolated `.pull` stage (`ingest.py:258,274-279`),
  then `ingest_drops(origin="url", inbox=stage, origin_paths=produced)` (`ingest.py:298`). Wired to
  `download_source` (`ingest.py:294`); the CLI `pull` splits download-outside-lock from ingest-inside-txn.
- **scan** — `ingest.scan_local(roots)` enumerates local roots (`ingest.py:301`), returns paths (no
  catalogue); a `origin="scan"` catalogue path exists in `_catalogue_file`'s contract (`ingest.py:174`).
- **Studio browser upload** — the Run tab "Upload video" streams into `01_inbox` via
  `studio/actions_run.save_uploads` (atomic `.uploadpart`→`os.replace`, 2 GiB cap) then the operator clicks
  Ingest inbox → `ingest_drops`. (Contract described in CLAUDE.md; the streamed file is just another inbox
  drop consumed by `ingest_drops`.)
- **third-party intake** — `origin_kind="third_party"` staged in `01_thirdparty_inbox` (a PEER of `01_inbox`,
  `config.py:47-48`); INERT to clip production (`pipeline._stage_source_to_moments` skips it,
  `pipeline.py:80`).

**Stage-by-stage (owning fn | reads | writes | shells+timeout+absent | failure posture):**

| # | Stage | Owner (file:line) | Reads | Writes (ledger / disk) | External process (timeout; absent-binary) | Failure posture |
|---|-------|-------------------|-------|------------------------|-------------------------------------------|-----------------|
| 0 | Catalogue | `ingest._catalogue_file` (`ingest.py:156`) | inbox files, sha256 | `Source` born `catalogued`, copies bytes to `02_sources/<sid>.<ext>` (`ingest.py:178-189`); WRITE-ONCE `origin_kind`/`batch_id` | `ffprobe` for dims (`_run_ffprobe` `ingest.py:102`, `_FFPROBE_TIMEOUT=30.0` `ingest.py:99`); absent → `ToolchainMissingError` → clean exit 2 (`ingest.py:111-114`) | **Mixed**: ffprobe ABSENT is fail-loud (typed error, exit 2); a per-file ffprobe TIMEOUT is fail-soft (0×0 `degraded="probe_failed"`, re-probed next pass `ingest.py:185, 73-86`); copy ENOSPC = per-file skip, not pass-abort (`ingest.py:182-183`) |
| 0b | Video-stream guard | `ingest.has_video_stream` (`ingest.py:138`) | ffprobe codec_type | audio-only drops archived, not catalogued (`ingest.py:233-235`) | `ffprobe` (same wrapper) | Fail-loud on absent binary (must NOT drop a real video as audio, `ingest.py:145-147`) |
| 1 | Transcribe | `transcribe.transcribe_source` (`transcribe.py:155`) | `Source.source_path`, cached JSON | `Source.transcript`, `.language`, `meta.transcribed`, state→`transcribed` (`transcribe.py:254-256`); JSON under `04_agent_io/transcripts` | faster-whisper via `python -m fanops._fwrun` else `whisper` CLI (`transcribe.py:217-220`); `_WHISPER_TIMEOUT=2700.0` length-scaled ×1.5 (`transcribe.py:27-37`); optional Demucs vocal isolation (`vocals.isolate_vocals`, `transcribe.py:198-210`) | **Fail-soft, per-source**: absent binary / timeout / no-JSON / malformed-JSON all → `SourceState.error` with a typed reason, `transcribed` unset so it re-runs (`transcribe.py:224-253`); vocal isolation fails OPEN to raw audio |
| 2 | Signals | `signals.detect_signals` (`signals.py:108`) | source path, sidecar | `Source.signal_peaks` (top-400 capped `signals.py:22-35`), `.duration`, state→`signalled`; sidecar `04_agent_io/signals/<sid>.json` | 2× ffmpeg (`silencedetect`, `scdet`) + optional `ebur128`/astats energy (`signals.py:79-86,134`); `_FFMPEG_TIMEOUT=600.0` (`signals.py:92`); absent → `ToolchainMissingError` | **Fail-loud on the two required passes** (typed error → per-source quarantine); the ENERGY pass is an ENHANCEMENT that fails SOFT to today's scoring (`signals.py:129-138`) |
| 3 | Request moments (pick) | `moments.request_moments` (`moments.py:137`) | transcript (char-budget bounded `moments.py:117`), peaks, `cfg.clip_profile`, 6 survey frames | opens `moments` agent gate, state→`moments_requested` | `keyframes.extract_keyframes` (fail-open `[]`) | LLM gate; per-source quarantine in pipeline (`pipeline.py:88-89`) |
| 3b | Ingest picks | `moments.ingest_moments` (`moments.py:161`) | agent response | `Moment` born `picked`, state→`picks_decided`; `[]` → non-terminal `moments_empty` (`moments.py:206-207`); all-invalid → `error` (`moments.py:197-198`) | none | Fail-visible: empty is LOUD but non-terminal, preserves prior moments; discards stale hook/casting gates (`moments.py:215-227`) |
| 3c | Hook author (pass 2) | `moments.request_moment_hooks`/`ingest_moment_hooks` (`moments.py:394,437`) | picked window + window frames + **owner-only** persona | per-pick `moment_hooks` gate; on ingest → `Moment.hook` (+ `hook_removed` when stripped), state `picked`→`decided`, source→`moments_decided` | `keyframes.extract_keyframes` over window (fail-open `[]`) | ATOMIC per source (waits for every pick); `is_weak_hook`+`brand_risk_flag` strip mechanical/off-brand hooks, PRESERVED for restore |
| 4 | *(no separate stage — routing is pick-stamped)* | `casting.affinity_admits` (`casting.py:10`) gates crosspost mint + caption scope via `Moment.affinities` (stamped at pick in `ingest_moments` `:330-340`; operator override `cast_add`/`cast_remove`) | owner handle(s) on each moment | same `affinities` list is the sole gate input | none | `cfg.account_casting` DEFAULT ON (`config.py:593`); `=0` ignores persisted affinities and fans all |
| 5 | Render | `clip.render_moment` → `render_aspects_for` (`clip.py:571,694`); pipeline `_stage_render_and_caption` (`pipeline.py:156`) | source, moment window, framing detect | `Clip` born `rendered` under `03_clips/<cid>.mp4`; state moment→`clipped`; render fingerprint sidecar (`clip.py:688-689`) | ffmpeg (`ffmpeg_clip_cmd`/`ffmpeg_segments_cmd`), `_FFMPEG_TIMEOUT=600.0` (`clip.py:24`); framing detect (YuNet, `[framing]` extra) fail-open | **Fail-safe per-moment**: ffmpeg absent/hung/rc≠0/0-byte → `ClipState.error`, moment left `decided` to retry (`clip.py:634-662`); smart framing fails OPEN to centered crop (`clip.py:533,550`) |
| 6 | Captions | `caption.request_captions`/`ingest_captions` (`caption.py:200,283`); pipeline (`pipeline.py:172,231`) | clip, scoped surfaces, corpus, content tags | `Clip.meta_captions[surface]`, state→`captioned` (or `held`) (`caption.py:356`) | none (LLM gate) | HOLD on brand-risk/language-mismatch (`caption.py:349-353`); SEED-TAG FALLBACK on missing surface, NOT a hold (`caption.py:342-348`) |
| 7 | Crosspost | `crosspost.crosspost_clips` → `_mint_surface_post` (`crosspost.py:299,168`); pipeline (`pipeline.py:243`) | captioned clips, surfaces, selections, batch target | `Post` born `awaiting_approval` (`crosspost.py:228-232`), clip state→`queued` | none | Wrapped so a raise doesn't cost the pass; a FATAL `AuthError` deliberately escapes (`pipeline.py:249-255`) |
| 8 | Reconcile | `reconcile.reconcile_due` (`reconcile.py:339`); pipeline `_reconcile_safe` (`pipeline.py:258`) | stranded posts, backend status | back-fills `public_url`, `publish_hour`/`publish_dow` (`reconcile.py:452-453`) | backend status GETs (out of lock) | Gated `cfg.is_live_backend`; `AuthError` halts, else logged (`pipeline.py:265-271`) |
| 9 | Publish | `post.run.publish_due` → `_publish_one` (`run.py:337,213`); pipeline `_publish_safe` (`pipeline.py:274`) | queued+due posts | claim→`submitting`→`published`/`needs_reconcile`/`failed`; `published_at`+`publish_hour`/`dow` stamped (`run.py:266-270`); `06_published/<day>/<pid>.json` archive (`run.py:25`) | media upload + `poster.publish` (out of lock); Postiz throttle `postiz_publish_per_min` (`run.py:95`) | `AuthError` halts the run (`run.py:277-279`); other errors → per-post `failed` (re-queueable) except `needs_reconcile` (not downgraded, `run.py:280`) |
| 10 | Summary/digest | `pipeline._build_summary` (`pipeline.py:339`) | post-publish reload | `write_digest` (read-only, out of lock) | none | Read-only |

**Framing detail** (`clip._resolve_framing` `clip.py:527`): classifies the window
(`multi-speaker-talk | single | music | silent | no-people`), routes to active-speaker TRACK (segment-concat,
locked-off static crop per shot) / subject FOCUS / motion SALIENCY / centered. Zoom is face-size-adaptive
(`_adaptive_zoom_max` `clip.py:427`). Entirely gated by `cfg.smart_framing` (default ON) and FAIL-OPEN at
every step to the centered crop (`clip.py:533,550`).

### 1.2 Configuration layer — EXHAUSTIVE environment variable table

Every `os.getenv`/`os.environ` read in `src/fanops/` (verified via `grep -rEn "os\.getenv|os\.environ"`).
**64 distinct environment variables** are read across the tree, listed in the 63 table rows below (row 63
holds TWO variables). Table is complete (no sampling):

| # | Variable | Read at (file:line) | Default | Controls |
|---|----------|---------------------|---------|----------|
| 1 | `ANTHROPIC_API_KEY` | config.py:160 | None | VESTIGIAL — responder uses `claude` subscription; not required |
| 2 | `FANOPS_POSTER` | config.py:174 (also 643,456; accounts.py:151) | `dryrun` | Legacy global poster backend; unknown→dryrun+warn |
| 3 | `FANOPS_LIVE` | config.py:237 | derived from POSTER | THE dryrun↔live switch |
| 4 | `POSTIZ_URL` | config.py:275 | None | Postiz instance base URL |
| 5 | `POSTIZ_API_KEY` | config.py:283 | None | Postiz public API key (write-only) |
| 6 | `ZERNIO_API_URL` | config.py:291 | `https://zernio.com/api/v1` | Zernio API base |
| 7 | `ZERNIO_API_KEY` | config.py:300 | None | Zernio API key (write-only) |
| 8 | `META_GRAPH_TOKEN` | config.py:309 | None | Meta Graph token for hashtag trends (write-only) |
| 9 | `META_IG_USER_ID` | config.py:315 | None | IG Business account id for `ig_hashtag_search` |
| 10 | `META_GRAPH_URL` | config.py:321 | `https://graph.facebook.com/v21.0` | Graph base (overridable) |
| 11 | `FANOPS_HASHTAG_TRENDS` | config.py:332 | ON | Background Graph reach sampling in `hashtags refresh` |
| 12 | `FANOPS_REQUIRE_FULL_OBJECTIVE` | config.py:341 | OFF | Refuse to amplify a lift-degraded winner |
| 13 | `FANOPS_RESPONDER` | config.py:392 (also doctor.py:31, autopilot.py:76, actions_run.py:36) | `manual` | THE explicit AI switch (llm/manual) |
| 14 | `FANOPS_LLM_MODEL` | config.py:408 | per-gate defaults | Force ONE model across all gates |
| 15 | `FANOPS_ARTIST_NAME` | config.py:421 | `Moh Flow` | YouTube title fallback display name |
| 16 | `FANOPS_CLIP_PROFILE` | config.py:430 | `talk` | Global clip-length band |
| 17 | `FANOPS_VISUAL_START` | config.py:466 | ON | Strongest-opening-frame cut refinement |
| 18 | `FANOPS_SMART_FRAMING` | config.py:476 | ON | Subject-aware reframe |
| 19 | `FANOPS_WHISPER_MODEL` | config.py:484 (also 511, 501-check) | `turbo` | Legacy whisper CLI model |
| 20 | `FANOPS_ASR_MODEL` | config.py:492 (also 501) | `medium` | faster-whisper model |
| 21 | `FANOPS_ASR_LANGUAGE` | config.py:519 | `en,ar` | Whisper candidate languages |
| 22 | `FANOPS_ISOLATE_VOCALS` | config.py:530 | ON | Demucs beat-stripping before Whisper |
| 23 | `FANOPS_BURN_SUBS` | config.py:541 | OFF | Burn transcript captions (hook is separate) |
| 24 | `FANOPS_AWARE_REFRAME` | config.py:551 | OFF | Global top-third crop bias |
| 25 | `FANOPS_SUBTITLE_FONT` | config.py:559 | `Arial Unicode MS` | .ass subtitle font |
| 26 | ~~`FANOPS_CREATIVE_VARIATION`~~ | — | — | **Documentation-only in `config.py`** (no `getenv`); Go-Live still dual-writes `.env` (`golive.py:225`) but per-account hook/render differentiation is intrinsic when `account_casting` is ON — see [fresh-ingestion-trace.md](fresh-ingestion-trace.md) §4 |
| 27 | `FANOPS_ACCOUNT_CASTING` | config.py:581 | ON | Per-account moment casting |
| 28 | `FANOPS_HOOK_ROUTER` | config.py:589 | OFF | Observe-only hook_strategy classifier |
| 29 | `FANOPS_IMPACT_CUT` | config.py:598 | OFF | Impact-cut stitch producer |
| 30 | `FANOPS_INTRO_TEASE` | config.py:608 | OFF | Intro-tease stitch producer |
| 31 | `FANOPS_VARIANT_LEARNING` | config.py:619 | OFF | A/B hook-learning master gate |
| 32 | `FANOPS_VARIANT_MIN_POSTS` | config.py:629 | 3 | Variant trust: min analyzed posts |
| 33 | `FANOPS_VARIANT_MIN_GAP` | config.py:640 | 10.0 | Variant trust: min lift margin |
| 34 | `FANOPS_VARIANT_AMPLIFY` | config.py:655 | OFF | Variant-driven source amplify |
| 35 | `FANOPS_VARIANT_AMPLIFY_MIN_POSTS` | config.py:664 | 8 | Amplify trust: min posts |
| 36 | `FANOPS_VARIANT_AMPLIFY_MIN_GAP` | config.py:674 | 25.0 | Amplify trust: min gap |
| 37 | `FANOPS_VARIANT_AMPLIFY_MIN_STREAK` | config.py:685 | 3 | Amplify trust: min distinct windows |
| 38 | `FANOPS_VARIANT_UCB` | config.py:705 | OFF | UCB1 bandit caption bias |
| 39 | `FANOPS_VARIANT_UCB_C` | config.py:716 | sqrt(2) | UCB exploration weight |
| 40 | `FANOPS_VARIANT_TRANSFER` | config.py:732 | OFF | Cross-surface hook-style transfer |
| 41 | `FANOPS_VARIANT_TRANSFER_MIN_DONORS` | config.py:742 | 2 | Transfer: min donor surfaces |
| 42 | `FANOPS_VARIANT_TRANSFER_MAX_HOOKS` | config.py:752 | 2 | Transfer: max borrowed styles/caption |
| 43 | `FANOPS_ADJUST_PER_SURFACE` | config.py:763 | OFF | Per-surface winner ranking |
| 44 | `FANOPS_P4_DIM_BIAS` | config.py:774 | OFF | Creative-dim reach amplify |
| 45 | `FANOPS_TIMING_BIAS` | config.py:784 | OFF | Reach-winning publish-hour schedule bias |
| 46 | ~~`FANOPS_CASTING_BIAS`~~ | — | — | **REMOVED P11** (`casting_bias.py` deleted with LLM casting teardown) |
| 47 | `FANOPS_IG_RETENTION_PROOF` | config.py:811 | OFF | Require IG retention to prove learning |
| 48 | `FANOPS_MOMENT_HOOK_LEARNING` | config.py:820 | OFF | Feed winning hook styles to moment author |
| 49 | `FANOPS_P4_MIN_REACH_GAP` | config.py:832 | 0.0 | P4/timing comparative reach margin |
| 50 | `FANOPS_GC_KEEP_DAYS` | config.py:844 | 30 | Manual-gc retention (clamped ≥1) |
| 51 | `FANOPS_UPLOAD_MAX_MB` | config.py:855 | 2048 | Studio upload body ceiling (clamped ≥1) |
| 52 | `FANOPS_OPERATOR_TZ` | config.py:867 | `UTC` | Operator timezone for scheduling/buckets |
| 53 | `FANOPS_REALISTIC_CADENCE` | config.py:876 | OFF | 2-3h jittered cadence band |
| 54 | `FANOPS_PUBLISH_LEAD_MINUTES` | config.py:912 | 0 | Editorial lead window (clamped ≥0) |
| 55 | `FANOPS_ZERNIO_MAX_UPLOAD_MB` | config.py:922 | 4 | Zernio TikTok upload preflight cap |
| 56 | `FANOPS_POSTIZ_PUBLISH_PER_MIN` | config.py:932 | 4 | Postiz publish throttle (0=off) |
| 57 | `FANOPS_CONCURRENT_SOURCES` | config.py:947 | OFF | Parallel per-source pipeline |
| 58 | `FANOPS_CONCURRENT_WORKERS` | config.py:959 | 4 | Concurrency pool size (clamped ≥1) |
| 59 | `FANOPS_POSTIZ_AUTOSTART` | postiz_lifecycle.py:51 | `1` (on) | Auto-start local Postiz stack |
| 60 | `FANOPS_POSTIZ_COMPOSE_DIR` | health.py:96 | (blank) | Postiz docker-compose dir for health |
| 61 | `META_GRAPH_TOKEN__<SLUG>` | meta_graph.py:68 (via per_account_token_env_key) | falls back to global | Per-handle Graph token (dynamic key, write-only) |
| 62 | `XDG_CACHE_HOME` | transcribe.py:43 | `~/.cache` | Whisper checkpoint cache root |
| 63 | `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` | _fwrun.py:27-28 | certifi | TLS bundle for the fw runner (setdefault) |

**True count, re-derived from the table itself: 64 distinct environment variables** across 63 rows (row 63
holds both `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`). Counting decision, stated explicitly: the dynamic
per-handle pattern `META_GRAPH_TOKEN__<SLUG>` (row 61 — one env-key FAMILY, one read site
`meta_graph.py:68`, N concrete keys at runtime) is counted as **ONE distinct variable slot**.

Split, from the table rows: **FANOPS_\* = 52** (rows 2, 3, and 11–60 — 2 + 50 rows, all FANOPS-prefixed).
**Non-FANOPS = 12**: `ANTHROPIC_API_KEY` (row 1), `POSTIZ_URL`, `POSTIZ_API_KEY`, `ZERNIO_API_URL`,
`ZERNIO_API_KEY`, `META_GRAPH_TOKEN`, `META_IG_USER_ID`, `META_GRAPH_URL` (rows 4–10),
`META_GRAPH_TOKEN__<SLUG>` (row 61, the one dynamic slot), `XDG_CACHE_HOME` (row 62), `SSL_CERT_FILE` and
`REQUESTS_CA_BUNDLE` (row 63). **52 + 12 = 64.**

Grep cross-check: `grep -rhoE 'FANOPS_[A-Z_]+' src/fanops --include='*.py' | sort -u` yields 52 lines, but
that is NOT 52 real vars directly — subtract `FANOPS_CFG` (a Flask `app.config` KEY at `studio/app.py:249`,
not an env var) and the fragment `FANOPS_P` (the `[A-Z_]+` regex stops at the digit, truncating BOTH
`FANOPS_P4_DIM_BIAS` `config.py:774` AND `FANOPS_P4_MIN_REACH_GAP` `config.py:832` into one fragment), then
add those 2 real P4 variables back: 52 − 2 + 2 = **52 distinct FANOPS_\* env vars** — matching the table.

### 1.3 Cross-reference — Studio-settable vs .env-only

The ONLY Studio setter of environment variables is the Go-Live tab via `golive._dual_write`
(`studio/golive.py:44`), which writes BOTH `.env` and `os.environ`. Verified via
`grep -oE '_dual_write\(cfg, "..."'`:

**Studio-settable env vars (12 total = 8 + 3 + 1):**
- `FANOPS_*` (8): `FANOPS_LIVE` (golive.py:632, via `go_live`), `FANOPS_ACCOUNT_CASTING` (237),
  `FANOPS_RESPONDER` (250), `FANOPS_CLIP_PROFILE` (292),
  `FANOPS_VARIANT_LEARNING` (306), `FANOPS_VARIANT_AMPLIFY` (315), `FANOPS_VARIANT_UCB` (322),
  `FANOPS_VARIANT_TRANSFER` (333). (`FANOPS_CREATIVE_VARIATION` is dual-written by Go-Live but has no
  `config.py` reader — documentation-only runtime switch; see fresh-ingestion-trace §4.)
- Non-FANOPS creds (3 static): `POSTIZ_URL` (91), `POSTIZ_API_KEY` (96), `ZERNIO_API_KEY` (136).
- Dynamic (1): the per-handle `META_GRAPH_TOKEN__<SLUG>` slot (golive.py:390) — counted as one settable
  variable, consistent with the one-slot counting decision in §1.2. `META_IG_USER_ID` is set per-account
  NOT via env but into accounts.json (`set_ig_user_id`, golive.py:366 comment). `FANOPS_POSTER` is
  Studio-UNSET-only (a stale-value scrape via `_dual_unset`, golive.py:645) — a clear, not a set, so it is
  NOT counted settable.

**.env/shell-ONLY (51 of the 64 — never settable from any Studio route):** all trust-gate numerics
(`FANOPS_VARIANT_*_MIN_*`, `FANOPS_VARIANT_UCB_C`, `FANOPS_P4_MIN_REACH_GAP`), all Phase-2 bias kill switches
(`FANOPS_P4_DIM_BIAS`, `FANOPS_TIMING_BIAS`, `FANOPS_MOMENT_HOOK_LEARNING`,
`FANOPS_ADJUST_PER_SURFACE`, `FANOPS_IG_RETENTION_PROOF`), the stitch producers (`FANOPS_IMPACT_CUT`,
`FANOPS_INTRO_TEASE`, `FANOPS_HOOK_ROUTER`), all ASR/framing knobs (`FANOPS_ASR_*`, `FANOPS_WHISPER_MODEL`,
`FANOPS_ISOLATE_VOCALS`, `FANOPS_BURN_SUBS`, `FANOPS_AWARE_REFRAME`, `FANOPS_SUBTITLE_FONT`,
`FANOPS_VISUAL_START`), scheduling (`FANOPS_OPERATOR_TZ`, `FANOPS_REALISTIC_CADENCE`,
`FANOPS_PUBLISH_LEAD_MINUTES`), infra (`FANOPS_CONCURRENT_*`, `FANOPS_GC_KEEP_DAYS`, `FANOPS_UPLOAD_MAX_MB`,
`FANOPS_*_PER_MIN`, `FANOPS_ZERNIO_MAX_UPLOAD_MB`, `FANOPS_POSTIZ_*`, `FANOPS_HASHTAG_TRENDS`,
`FANOPS_REQUIRE_FULL_OBJECTIVE`, `FANOPS_LLM_MODEL`, `FANOPS_ARTIST_NAME`, `FANOPS_POSTER`), the Meta/TLS
creds (`META_GRAPH_TOKEN`, `META_IG_USER_ID`, `META_GRAPH_URL`, `ANTHROPIC_API_KEY`, `XDG_CACHE_HOME`,
`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`).

**Exact counts: 13 Studio-settable, 51 .env/shell-only — 13 + 51 = 64 distinct variables**, matching the
§1.2 table total.

### 1.4 First quality/content judgment in the chain, and compute spent by then

The FIRST stage that can REJECT or park footage for **content** reasons is the **moments (pick) LLM gate**,
ingested by `moments.ingest_moments` (`moments.py:161`). The model returning `[]` ("nothing worth posting")
parks the source in `moments_empty` (`moments.py:206-207`); a wholly-invalid decision → `error`
(`moments.py:197-198`). Everything upstream is content-BLIND: PII/legal name exclusion (`ingest.is_excluded`
`ingest.py:25`) is a NAME filter not content judgment; the audio-only guard is structural; transcribe/signals
never judge worth.

**Compute spent before the first content gate:** full ingest (sha256 + ffprobe), full **Whisper transcription**
(the single most expensive local step, `_WHISPER_TIMEOUT` up to 2700s+, with optional Demucs isolation), full
**ffmpeg signal detection** (silence + scene + energy), and **6 survey-frame keyframe extractions**
(`moments.py:29,42`). i.e. every heavy per-source subprocess has already run — the content gate is the LLM,
which sits AFTER transcribe+signals+keyframes by construction (`pipeline._stage_source_to_moments`
`pipeline.py:82-87`).

---

## Lens 2 — Hashtags: derivation, persistence, presentation to the LLMs

### 2.1 Sources a tag can enter from (end-to-end)

1. **Frozen reach-ranked pools** — `_MEGA/_RELEVANCE/_ARABIC/_DISCOVERY` (`hashtags.py:15-24`); `VETTED` is
   their union (`hashtags.py:30`), which is the cold-start FLOOR (comment `hashtags.py`, header).
2. **Live Graph reach store** — `fanops_hashtags.refresh_store` (`fanops_hashtags.py:41`) harvests
   co-occurring candidates (`meta_graph.harvest_cooccurring` `meta_graph.py:460`), measures live reach
   (`sample_trends` `meta_graph.py:417`), ranks by reach, writes `00_control/hashtags.json`
   `{tags, reach}` (`fanops_hashtags.py:77`). Read side: `hashtags.load_store` (`hashtags.py:37`),
   `load_store_reach` (`hashtags.py:53`). Refreshed on a 12h throttle inside `run` via `refresh_store_if_due`
   (`fanops_hashtags.py:81`).
3. **Per-persona curated corpus** — `Persona.hashtag_corpus` (`personas.py:41`), hydrated onto the account
   (`accounts._hydrate_from_personas` `accounts.py:258`), the **SOLE per-account hashtag differentiator**
   since the tag_lean fold (M3, `hashtags.py:32-35`).
4. **Per-clip content derivation** — `hashtags.content_tag_candidates` (`hashtags.py:107`): deterministic,
   pure, NO NLP — latin word tokens 3-20 chars, stopword-filtered, frequency-then-first-seen ordered,
   capped at 6. Blank/Arabic/numbers → `[]` (byte-identical).
5. **Operator actions** — Studio Personas tab: `add_corpus_tag`/`remove_corpus_tag`
   (`studio/personas.py:112,129`), `research_corpus` (Graph co-occurrence discovery → propose,
   `studio/personas.py:185`), `recommend_tag` (live single-tag Graph reach, `studio/personas.py:165`).
   **Discovery NEVER auto-writes a caption tag** — the operator ACCEPTS into the corpus (curation gate,
   `fanops_hashtags.py:121-145`).

### 2.2 Persistence locations + schemas (from code)

- `00_control/hashtags.json` — `{"tags": [str,...], "reach": {tag: number}}` (`fanops_hashtags.py:77`,
  read `hashtags.py:47,63-67`). Provenance source label `graph-reach` (`fanops_hashtags.py:76` comment;
  `hashtags._tag_source` `hashtags.py:220`).
- `00_control/personas.json` — `Persona.hashtag_corpus: list[str]` (`personas.py:41`).
- `Clip.meta_captions[surface]` — `_caption_entry` (`caption.py:266`) writes
  `{"caption": " ".join(tags), "hashtags": tags, "hashtags_raw": [...verbatim model picks...],
  "hook": None, "axis": None, "rationale": None, "tag_sources": {tag: source}}` (`caption.py:276-277`);
  `fallback: True` on seed synthesis (`caption.py:278`).
- `Post.hashtags: list[str]` (`models.py:227`) — copied from `cap.get("hashtags")` at mint
  (`crosspost.py:275`).
- Budget counter `00_control/hashtag_budget.json` — `{"queries": [{"tag", "ts"}, ...]}`
  (`meta_graph.py:385`), lock `hashtag_budget.lock` (`config.py:108`).

### 2.3 Meta Graph budget accounting

`_BUDGET_LIMIT=30` unique hashtags per IG user per rolling `_BUDGET_WINDOW_DAYS=7`
(`meta_graph.py:73-74`). `budget_remaining` = 30 − (unique tags in last 7 days), or **None = FAIL-CLOSED**
when the counter is unreadable (`meta_graph.py:340-358`). `record_query` appends a `(tag, ts)` under an
fcntl flock (lost-update guard, `meta_graph.py:360-387`). Every consumer checks budget then spends:
`sample_trends` (`meta_graph.py:417`), `harvest_cooccurring` (one slot per unique seed, `meta_graph.py:486`),
`tag_metrics` (one slot, `meta_graph.py:411`), `discover_candidates` (top-K only, `meta_graph.py:529-533`).
An unreadable budget queries NOTHING (better a stale store than a banned app, `meta_graph.py:11-13`).

### 2.4 How tags reach the LLM prompt (exact text)

`caption_prompt` (`prompts.py:361`). Menu injected via `vetted_menu()` (`prompts.py:390`). Two pick-rules:
- WITHOUT content tags (`prompts.py:398`): *"Choose ONLY from this REACH-VETTED menu (ranked by real post
  volume); do NOT invent tags: {menu}."*
- WITH content tags (`prompts.py:393-396`): *"Choose from this REACH-VETTED menu ... OR the CLIP-SPECIFIC
  tags listed next; do NOT invent anything outside BOTH lists ... CLIP-SPECIFIC tags (derived from THIS clip
  — prefer them when they fit the content)."*

The HARD RULE (`prompts.py:420-426`): *"Each `caption` is HASHTAGS ONLY: a single line of AT MOST 4 hashtags
(MAX 4 — fewer is fine) ... Compose a balanced 4: one mega genre tag (#hiphop/#rap), one relevance tag
(#rapper/#bars), one language/region tag for an Arabic clip ... else a second music tag (#newmusic), and one
platform-discovery tag (#fyp/#reels). ... Anything beyond 4 or off-menu is dropped by the system."* Corpus
instruction (`prompts.py:429-431`): *"When a surface carries a `corpus` ... PREFER the tags in that
surface's `corpus` for that surface ... fill any remaining slots (up to 4) from the menu above."*

### 2.5 Enforcement on LLM output (the full vet algorithm, step by step)

`vet_hashtags` (`hashtags.py:140`), traced variant `vet_hashtags_traced` (`hashtags.py:224`). Called from
`ingest_captions` (`caption.py:328,344`) — **it never trusts the model**:
1. Normalize+dedupe corpus and content (`hashtags.py:157-158`).
2. Membership set = `store OR VETTED` ∪ corpus ∪ content — corpus and content JOIN the gate so a curated or
   clip-specific tag the frozen set doesn't know SURVIVES (`hashtags.py:159`).
3. Rank base = store order if store else `_RANK` (`hashtags.py:160`); preference float: corpus > content
   ahead of the frozen rank (negative-indexed, `hashtags.py:162-166`).
4. Seed the WHOLE corpus first (`hashtags.py:170-172`), then honor the model's picks but ONLY vetted ones
   (`hashtags.py:174-177`).
5. Sort by rank (`hashtags.py:178`).
6. **Reserved floors** take TAIL slots so corpus/reach lead is preserved: region (Arabic) floor first when a
   corpus + Arabic clip (`hashtags.py:187-188`), then ONE content tag (`hashtags.py:189-190`).
7. **Backfill** REACH-first: corpus + one platform discovery floor + store + `_composition` default 4 +
   content (`hashtags.py:198-205`).
8. **HARD cap `kept[:max_tags]`** with `max_tags=4` (`hashtags.py:206`).

Provenance (`vet_hashtags_traced` → `_tag_source` `hashtags.py:211`): each shipped tag labelled
`content > corpus > region > graph-reach > discovery > genre-floor` — **never empty** (a sourceless tag
cannot ship, `hashtags.py:222`), recorded in `meta_captions[surface].tag_sources` (`caption.py:332`).

### 2.6 What determines rank; feedback; caption composition

- **Rank everywhere**: in the store, by LIVE Graph reach desc (`fanops_hashtags.py:71`); in `vet_hashtags`,
  corpus > content > (store reach OR frozen `_RANK`) (`hashtags.py:160-178`); the frozen `_RANK` is a static
  class-ranking (`hashtags.py:27`).
- **Post-performance feedback into tag selection: NONE.** The `lift_score` weight map `_W` (`track.py:30`)
  carries NO hashtag dimension; the store is ranked by the tag's OWN live Graph reach, never a post that used
  it (`fanops_hashtags.py:2-4`). Pinned by `tests/test_hashtag_attribution_severance.py`
  (`test_lift_weights_carry_no_hashtag_dimension`, `test_no_learning_module_attributes_a_post_outcome_to_hashtags`).
- **A shipped caption is composed of: hashtags ONLY.** `_caption_entry` sets `caption = " ".join(tags)`
  (`caption.py:276`); the prompt forbids prose/@mentions/emoji (`prompts.py:420-421`). The ONE non-hashtag
  addendum is the artist tag `ARTIST_HANDLE` appended on its own line by `decide_tag` at crosspost
  (`crosspost.py:231-232`) — subject to `decide_tag`'s non-synchronized gate. This holds for BOTH account
  types (fan accounts and the main handle route through the same caption pipeline); the third-person
  fan-voice is a prompt instruction (`prompts.py:400-404`), not a caption-content difference. The on-screen
  HOOK is a SEPARATE burned layer authored by the moment gate (`moments.py`), not caption content.

### 2.7 Tests pinning hashtag behavior

`tests/test_hashtags.py` (hard cap at 4 `test_hard_caps_at_four`; drops non-vetted; reach ordering; Arabic
floors; corpus float/floors), `tests/test_content_aware_hashtags.py` (content extraction, floor reserves a
slot, every-kept-tag-has-a-source, byte-identical-without-content), `tests/test_hashtag_attribution_severance.py`
(no post→hashtag feedback), `tests/test_fanops_hashtags.py`, `tests/test_hashtag_lifecycle_e2e.py`,
`tests/test_graph_tag_metrics.py`, `tests/test_persona_corpus.py`.

---

## Lens 3 — Persona fields: determination and downstream effects

### 3.1 The Persona data model (exact, from code)

`Persona(BaseModel)` (`personas.py:37`):
| Field | Type | Default | Validation | Notes |
|-------|------|---------|-----------|-------|
| `id` | str | (required) | slug (`_slug` `personas.py:84`) | the link key on `Account.persona_id` |
| `name` | str | "" | none | operator display name |
| `voice` | str | "" | none | the freeform string the pipeline reads |
| `hashtag_corpus` | list[str] | `[]` | normalize/dedupe/cap at write (`persona_store.add_corpus_tag`) | SOLE per-account hashtag differentiator |
| `intake` | dict | `{}` | only live field `genre` (`studio/personas.py:15-20`) | seeds Graph research |
| `content_focus` | list[str] | `[]` | `CONTENT_FOCUS` = `_lever_vocab("content_focus")` (`personas.py:32`) | which moment KINDS (casting) + DERIVES cut length |
| `energy` | Optional[str] | None | `ENERGY_LEVELS` (`personas.py:33`) | casting energy + DERIVES framing |
| `hook_angle` | Optional[str] | None | `HOOK_ANGLES` (`personas.py:34`) | on-screen hook strategy |

**Retired fields (documented in code):** per-persona `clip_profile`/`framing` PINS retired M3
(`personas.py:49-52` — length now derives from `content_focus`, framing from `energy`); the 3 freeform
per-dimension OVERRIDES (casting/hook/caption_directive) retired M3e (`personas.py:53-57`); `tag_lean` folded
into the corpus M3 (`hashtags.py:32-35`, `persona_levers.py:76-77`). `resolved_cut_spec` is duck-typed so an
absent pin still resolves (`persona_directives.py:46`).

### 3.2 Determination — every write path + its validation

- **Studio create** — `studio/personas.create_persona` → `core.add_persona` (`studio/personas.py:61-68`);
  validates non-blank name + each lever at the A1 boundary (`personas.py` re-exports `persona_store`).
- **Studio edit** — `edit_persona` → `update_persona` (`studio/personas.py:77-87`); form AUTHORITATIVE (blank
  clears a lever); unknown lever/blank name → ValueError → clean one-line error.
- **Studio delete** — `delete_persona` (`studio/personas.py:97`).
- **Corpus mutation** — `add_corpus_tag`/`remove_corpus_tag` (`studio/personas.py:112,129`); normalize,
  dedupe, cap; corpus-full surfaced not silently dropped.
- **Live compose preview** — `preview_compose` (`studio/personas.py:23`) validates levers against
  `CONTENT_FOCUS/ENERGY_LEVELS/HOOK_ANGLES` (`studio/personas.py:32-53`); builds a TRANSIENT Persona, never
  persists.
- **Research seed** — `research_corpus` persists `intake.genre` then runs discovery (`studio/personas.py:196`).
- **Migration** — `migrate_from_accounts` lifts inline persona strings into records + links
  (`studio/personas.py:205`, `persona_store.migrate_from_accounts`).
- **Load validation** — `Personas.load` raises `ControlFileError` on a hand-edit typo (`personas.py:73-74`);
  each field validated by pydantic against the lever vocabularies.

Write boundary vocabularies all project from the single `LEVER_REGISTRY` (`persona_levers.py:45`), so the
validation vocab, clause maps, and catalog cannot drift (`persona_levers.py:1-8`).

### 3.3 Linking + hydration

`Account.persona_id` (`accounts.py:29`) is the link. `link_persona`/Studio `connect_account`
(`accounts.py:271`, `studio/personas.py:144`) sets it atomically; blank clears it. At `Accounts.load`,
`_hydrate_from_personas` (`accounts.py:240`) runs:
- Resolves the Persona via `_persona_for_account` — explicit `persona_id` first, else an exact inline-voice
  match (`accounts.py:226-237`).
- Copies IN MEMORY: `acc.persona = per.voice` (`accounts.py:256`), `acc.hashtag_corpus = per.hashtag_corpus`
  (`accounts.py:258`), `acc.content_focus/energy/hook_angle` (`accounts.py:261-263`), and the DERIVED cut spec
  `resolved_cut_spec(per)` → `acc.clip_profile`/`acc.framing` + `persona_owns_profile` provenance flag
  (`accounts.py:264-266`).
- **Persists NOTHING** — hydration is in-memory only; the corpus is never stored on the account row
  (`accounts.py:47-51`). `set_*` mutators write the raw accounts.json dict, not hydrated values.

**Link-failure behavior:** FAIL-OPEN. A dangling `persona_id`, absent/corrupt personas.json, or any error
leaves the account's inline values intact — byte-identical when unlinked (`accounts.py:250-255`). **Observable?**
The failure itself is SILENT (no log/badge — the `except Exception: return` at `accounts.py:250-251` swallows).
Downstream, `Accounts.validate` (`accounts.py:207-216`) surfaces a "no persona linked" or "cut spec matches
global" problem string when `creative_variation` is on, and `advance` logs those as `differentiation_warn`
(`pipeline.py:385-387`). So a link that fails to resolve is not itself flagged, but its DOWNSTREAM effect
(no differentiation) is a validate-time warning. `delete_persona` deliberately leaves accounts with a dangling
id that falls open (`studio/personas.py:97-99`).

### 3.4 Downstream effects — every consumer of every field

| Field | Lands at (payload/render key) | Full chain (file:line) |
|-------|-------------------------------|------------------------|
| `voice` | casting/hook/caption prompt per-account slot | `_base_voice` (`persona_directives.py:56`) → leads `casting_directive` (`:68`), `hook_directive` (`:82`), `caption_directive` (`:107`) → carried in casting `personas[].persona` (`casting.py:78`), hook `personas[].persona` (`moments.py:243`), caption `surfaces[].persona` (`caption.py:209,226`) |
| `content_focus` | casting SELECTION language + DERIVED cut LENGTH | `_FOCUS_CLAUSE` → `casting_directive` "Clip for this account: ..." (`persona_directives.py:75-76`); `_FOCUS_PROFILE` → `derive_cut_spec` length tier (`:41`) → `resolved_cut_spec` → `acc.clip_profile` → `cfg.resolve_clip_profile(acct)` (`config.py:433`) → `crosspost.account_render_spec` — `resolve_clip_profile` call at `crosspost.py:86`, `wants_cut` decision `crosspost.py:86-91` → `render_account_cut` band (`clip.py:706,723`) — physically cuts the clip length |
| `energy` | casting energy clause + DERIVED framing | `_ENERGY_CLAUSE` → `casting_directive` (`persona_directives.py:77-78`); `_ENERGY_FRAMING` → `derive_cut_spec` framing (`:42`) → `acc.framing` → `cfg.resolve_top_bias(acct)` (`config.py:443`) → `top_bias` in `render_account_cut`/`reframe_filter` (`clip.py:310-311`), and stamped on `Post.top_bias` at mint (`crosspost.py:294`) |
| `hook_angle` | on-screen hook strategy | `_ANGLE_CLAUSE` → `hook_directive` (`persona_directives.py:88-89`) → `hook_author_slot` → owner-only hook gate (`moments._hook_personas_for_moment` `moments.py:384`) → `Moment.hook` → burned at render (`clip.render_account_cut`) → surfaced as `variant_hook` in Studio |
| `hashtag_corpus` | caption hashtags (deterministic post-step) | hydrated `acc.hashtag_corpus` → `corpora[handle]` in caption request (`caption.py:213`) → surface `corpus` key (`caption.py:227`) → prompt "PREFER ... corpus" (`prompts.py:429-431`) AND `vet_hashtags(corpus=...)` float+floor+backfill (`caption.py:330`, `hashtags.py:159-205`) |
| `intake.genre` | Graph research seeds only | `_seed_tags` (`fanops_hashtags.py:32`) + `discover_corpus` — never a live caption |

**Lever/vocabulary machinery + consistency.** One registry `LEVER_REGISTRY` (`persona_levers.py:45`) is the
UPSTREAM of three projections: the validation vocabularies (`personas.CONTENT_FOCUS/ENERGY_LEVELS/HOOK_ANGLES`
= `_lever_vocab(...)` `personas.py:32-34`), the compile+derive clause maps
(`persona_directives._FOCUS_CLAUSE/_ENERGY_CLAUSE/_ANGLE_CLAUSE/_FOCUS_PROFILE/_ENERGY_FRAMING`
`persona_directives.py:19-32`), and the operator catalog (`lever_catalog` → `build_catalog`
`persona_directives.py:131`). Coherence is a SEPARATE facet declaration
`PERSONA_EDITABLE_CHANNELS` (`persona_levers.py:78`) mapping each editable field to the output channel(s) it
owns, enforced by the "≤1 owner per channel" rule; `compose_breakdown`/`manifest` derive the live "what this
persona produces" from the SAME resolvers the pipeline runs (`persona_directives.py:178,211`) — so the
operator view cannot drift from output. Pinned by `tests/test_persona_lever_coherence.py`,
`test_persona_lever_editor_parity.py`, `test_persona_lever_registry.py`, `test_persona_cut_derivation.py`.

### 3.5 Feedback — does performance flow back into persona fields?

**NO.** Persona field values are write-once-by-operator. I checked every reach/lift/metrics consumer:
- ~~`casting_bias.casting_reach_prior`~~ — **REMOVED P11** (was a read-only casting-brief hint; never mutated a Persona).
- `p4_dim_bias`/`timing_bias` (`p4_dim_bias.py:56`, `timing_bias.py:79`) amplify sources / write a schedule
  prior — never a persona field.
- `variant_learning`/`variant_transfer` bias captions/hooks at request time — never a persona.
- `persona_facts`/`compose_breakdown` SURFACE derived stats (lead tags, cut band) but only from persona
  values + the reach store, not into the persona (`persona_directives.py:272,178`).

No proposal/surfaced-stat/auto-update writes any persona field. Persona values change ONLY via the Studio
Personas write routes (§3.2).

### 3.6 Live state (counts only)

`MohFlow-FanOps/00_control/personas.json` readable: **3 personas** (`craft-curator`, `underground-zine`,
`burner-bold`), **3 distinct voices**. `MohFlow-FanOps/00_control/accounts.json` readable: **5 accounts, all
active; 5 linked by `persona_id`; 0 inline-only; 0 unlinked.** No tokens/keys read or shown.

---

## Cross-cutting — the gates that frame all three lenses

### C.1 Post construction and initial state

Posts are constructed in exactly one production path: `crosspost._mint_surface_post` → `led.add_post(Post(...))`
(`crosspost.py:228`). **Every Post is BORN `PostState.awaiting_approval`** (`crosspost.py:232`, model default
`models.py:220`) with `submission_id="fanops_<hash>"` (client idempotency token, `crosspost.py:281`),
`render_id=None`, `media_urls=[]`, and the P1/P3 attribution dims stamped (`first_frame_kind`, `cut_seconds`,
`clip_profile=cfg.clip_profile`, `top_bias=cfg.resolve_top_bias(surf.account)`, `batch_id`,
`variation_axis`, `crosspost.py:284-295`). `repost_post` mints a fresh `awaiting_approval` repost
(CLAUDE.md). A `Post` model_validator refuses `published`/`analyzed`/`retired` without a non-empty
`public_url` (R1 terminal-URL invariant, `models.py:279-299`).

### C.2 Promotion toward publishing; the operator gate

Only `queued` posts can publish: `post.run.publish_due` filters `posts_in_state(PostState.queued)`
(`src/fanops/post/run.py:345`; `models.py:55-60`), and the Studio's `publish_now`
(`src/fanops/studio/actions.py:361`) drives the same queued-only path via `post.run.publish_post`
(`src/fanops/post/run.py:382`) — an unapproved post is structurally unpublishable even on a live backend. **The operator gate is `Ledger.approve_post`** promoting
`awaiting_approval`→`queued` (the Studio Review tab; CLAUDE.md, `models.py:57-59`). `queued` means
"approved + scheduled". `_publish_one` (`run.py:213`) does claim→`submitting`→network→finalize; a
`submitted`-without-URL parks in `needs_reconcile` (R1/D2 gate `run.py:264-276`); a timeless queued post
parks and does NOT auto-publish (`run.py:322-325`). `dryrun` posts HALT at the processing/distribution seam,
staying `queued`, never a phantom-published row (`run.py:361-370`).

### C.3 Learning/bias gating — every actuator, its kill switch, its validation gate, thresholds

**Validation gate (shared):** `validation_gate.learning_validated(cfg)` = `cutover.json metrics_confirmed`
(`validation_gate.py:22`). Auto-stamped by `track._auto_validate_metrics_shape` on the FIRST real
non-degraded analyzed metric from a LIVE backend (`track.py:322-349`) — NOT an operator step; dryrun never
proves it (`track.py:331-332`); a degraded row never stamps (`track.py:341-346`). `p4_unlocked(led,cfg,dim)`
= `learning_validated` AND `enough_attributed_signal` (≥8 attributed posts across ≥2 distinct values,
`_MIN_ATTRIBUTED_N=8`/`_MIN_VALUES=2`, `validation_gate.py:18-19,42-46`).

| Actuator (file:line) | Kill switch (default) | Validation gate | Thresholds |
|----------------------|------------------------|-----------------|-----------|
| `p4_dim_bias.apply_p4_dim_bias` (`p4_dim_bias.py:56`) — amplify a rep source per winning creative dim (`first_frame_kind`, `clip_profile`, `top_bias`) | `FANOPS_P4_DIM_BIAS` (OFF) | `p4_unlocked(dim)` (`p4_dim_bias.py:38`) | leader beats runner-up by ≥`p4_min_reach_gap` (default 0.0); ≥8 posts × ≥2 values |
| `timing_bias.apply_timing_bias` (`timing_bias.py:79`) — write reach-winning `publish_hour` prior consumed by `surface_time` | `FANOPS_TIMING_BIAS` (OFF) | `p4_unlocked('publish_hour')` (`timing_bias.py:36`) | ≥`p4_min_reach_gap` lead; window-clamped to `account_window` (`timing_bias.py:64`) |
| ~~`casting_bias.casting_reach_prior`~~ | — | — | **REMOVED P11** |
| `variant_amplify` (config.py:645; CLAUDE.md ref `variant_amplify.py:166`) — re-mine a source off a sustained hook winner | `FANOPS_VARIANT_AMPLIFY` (OFF) | `learning_validated` (validation-frozen) | ≥8 posts, ≥25.0 gap, ≥3 distinct windows |
| `variant_learning`/`ucb_rank` caption bias (`caption.py:153-173`) | `FANOPS_VARIANT_LEARNING` (OFF), `FANOPS_VARIANT_UCB` (OFF) | NOT validation-frozen (safe reversible read side, `config.py:698-704`) | ≥3 posts, ≥10.0 gap (v2); UCB c=sqrt(2) |
| `variant_transfer` cold-start bias (`caption.py:175-198`) | `FANOPS_VARIANT_TRANSFER` (OFF) | `learning_validated` (`caption.py:183-185`) | ≥2 donors, ≤2 borrowed |
| `moment_hook_learning` (config.py:815) — feed winning hook STYLES to the moment author | `FANOPS_MOMENT_HOOK_LEARNING` (OFF) + `FANOPS_VARIANT_LEARNING` | (rides variant_learning) | STYLE cue only |

Every actuator is AMPLIFY/BIAS-ONLY (audit C1: never retire/cascade/track — `p4_dim_bias.py:8-11`),
FAIL-SAFE (exception → logged once, ledger byte-identical), and introduces NO new
auto-publish path (biases GENERATION + SCHEDULE only).

**What output quality depends on before vs after these gates open:** BEFORE `learning_validated` (i.e. on
dryrun, or before the first real non-degraded live metric), all consequential/validation-frozen actuators are
INERT and output quality rests entirely on the STATIC craft: the moment-pick + frame-seeing-hook LLM gates
(`prompts._hook_spec` `prompts.py:68`), the deterministic hashtag vetting floor, per-account casting/personas,
and the smart-framing render — plus the operator approval gate. AFTER the gate opens, the reach-loop bias
actuators (once their own kill switches are turned on — all default OFF and .env-only) begin nudging
generation and schedule toward measured reach, but never past the operator approval gate.

---

## Findings (observed in source, strictly separated from the map above)

1. **The first content gate sits after the full expensive per-source pipeline.** Whisper transcription (up to
   2700s+, optional Demucs), ffmpeg signal detection, and 6 keyframe extractions all run BEFORE the moments
   LLM gate — the earliest step that can reject/park footage for content reasons (`moments.py:161`,
   `pipeline.py:82-87`). A dead-footage source burns the entire transcribe+signals+keyframes compute before
   any "is this worth posting" judgment. Evidence: `pipeline._stage_source_to_moments` ordering.

2. **Studio exposes only 13 of 64 distinct env variables; every trust-gate numeric and Phase-2 bias kill
   switch is .env/shell-only.** `_dual_write` covers 9 FANOPS_* + 3 static creds + the dynamic per-handle
   token slot (`studio/golive.py`), so `FANOPS_P4_DIM_BIAS`, `FANOPS_TIMING_BIAS`,
   all `FANOPS_VARIANT_*_MIN_*`, `FANOPS_OPERATOR_TZ`, etc. have no UI. An operator-only deployment cannot
   turn on the reach-loop bias actuators or tune their thresholds without shell access. Evidence: §1.3
   itemization vs the 64-variable table in §1.2.

3. **A persona-link resolution failure is silent at the point of failure and only surfaces indirectly.**
   `_hydrate_from_personas` swallows every exception with `return` (`accounts.py:250-251`); a dangling
   `persona_id` leaves inline values with NO log/badge. The downstream "no differentiation" is only caught by
   `Accounts.validate` → `differentiation_warn` and ONLY when `creative_variation` is on
   (`accounts.py:207-216`, `pipeline.py:385-387`). Evidence: the two cited spans.

4. **`learning_validated` is a single global boolean (`cutover.json metrics_confirmed`) that gates all
   validation-frozen actuators at once, and auto-flips on the FIRST qualifying live metric.** One
   non-degraded analyzed row from any live post unfreezes p4_dim_bias, timing_bias, and
   variant_transfer/amplify simultaneously (`track.py:347-348`, `validation_gate.py:22`). There is no
   per-actuator or per-account validation — the plumbing proof is system-wide. Evidence:
   `_auto_validate_metrics_shape` writes one flag consumed by every frozen actuator.

5. **Hashtag ranking is fully severed from post performance — the tag's OWN live Graph reach is the sole
   judge, enforced by an invariant test.** `_W` carries no hashtag dimension and the store ranks by
   `sample_trends` reach, never by a post that used the tag (`fanops_hashtags.py:71`, `track.py:30`), pinned
   by `tests/test_hashtag_attribution_severance.py`. Combined with `vet_hashtags`' hard `[:4]` cap and the
   never-empty provenance label (`hashtags.py:206,222`), no model-invented or performance-derived tag can ever
   ship. Evidence: the attribution-severance test names + the vet algorithm.

**UNRESOLVED:** None material to the mapped questions. One narrow item: the `origin="scan"` catalogue path is
declared in `_catalogue_file`'s contract (`ingest.py:174`) but I did not locate a production caller that
invokes `ingest_drops`/`_catalogue_file` with `origin="scan"` (only `scan_local` which merely enumerates,
`ingest.py:301`) — so whether the scan channel actually catalogues in the running system is not settled from
the files I read.
