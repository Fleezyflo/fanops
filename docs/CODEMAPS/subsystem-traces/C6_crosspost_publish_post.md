# C6: Crosspost, Publish & Post

> **POST-REBUILD (P15 / MOL-156).** Crosspost mints posts only on owner-admitted surfaces
> (`casting.affinity_admits` — `Moment.affinities` single-owner). No `account_selection_admits`,
> `repair_casting_selections`, casting-gate defer, `hooks_by_persona`, or `Post.variant_hook`.
> Captions scope via `pipeline._owner_caption_surfaces` (same predicate). Closed-loop proof:
> `tests/test_per_persona_e2e.py`.

## Files covered (all 17 read in full, cross-checked against structural_index.json — function/class/method lists match exactly)

- `src/fanops/crosspost.py` (342 lines) — read
- `src/fanops/pipeline.py` (446 lines) — read
- `src/fanops/router.py` (69 lines) — read
- `src/fanops/responder.py` (172 lines) — read
- `src/fanops/signals.py` (154 lines) — read
- `src/fanops/agentstep.py` (137 lines) — read
- `src/fanops/autopilot.py` (102 lines) — read
- `src/fanops/postiz_lifecycle.py` (70 lines) — read
- `src/fanops/post/__init__.py` (42 lines) — read
- `src/fanops/post/compress.py` (131 lines) — read
- `src/fanops/post/dryrun.py` (42 lines) — read
- `src/fanops/post/media.py` (62 lines) — read
- `src/fanops/post/metrics.py` (460 lines) — read
- `src/fanops/post/postiz.py` (328 lines) — read
- `src/fanops/post/providers.py` (57 lines) — read
- `src/fanops/post/run.py` (408 lines) — read
- `src/fanops/post/zernio.py` (271 lines) — read

Plus ground truth: `src/fanops/models.py` `PostState` enum (lines 54-77), `src/fanops/ledger.py`
`approve_post`/`reject_post`/`unapprove_post` (lines 503-527) and the state-protection sets
`_LIVE_POST_STATES`/`_PROTECTED_POST_STATES` (lines 604-612).

Cross-checked against `.reports/structural_index.json` (17/17 files found, function/class/method
counts match this trace exactly — 9+18+4+2+9+10+3+4+2+6+1+5+13+12+7+14+10 = 129 module-level
functions plus the class methods enumerated below), `.reports/call_graph.json` (154 matched
qualified-name entries for these 17 modules, used for every "Called by" line below), and
`.reports/import_graph.json`.

## Post state machine

**States** (`src/fanops/models.py:54-77`, `class PostState(str, Enum)`):

| State | Meaning | Publishable? |
|---|---|---|
| `awaiting_approval` | BIRTH state — every post minted by `crosspost._mint_surface_post` lands here | No |
| `queued` | approved + scheduled (operator promoted it) | **Yes** — the only state `publish_due`/`publish_now` act on |
| `submitting` | claimed for publish, network call in flight | No (crash-safe intent marker) |
| `submitted` | backend accepted the POST, id captured, permalink not yet confirmed | No (transitional, gated to `published`) |
| `published` | backend accepted AND a `public_url` is present (R1/D2 gate) | N/A (terminal, successful) |
| `analyzed` | published post has had metrics pulled (outside this cluster — `track.py`) | N/A |
| `failed` | definitely did not post; safe to re-queue | No |
| `error` | generic error terminal | No |
| `rejected` | operator discarded an `awaiting_approval` post via `reject_post` | No, terminal |
| `needs_reconcile` | ambiguous publish outcome (5xx/timeout AFTER the request body was sent) — MAY already be live; never blindly re-POSTed | No |
| `retired` | a queued base post superseded by an approved stitch (M4, outside this cluster) | No |

**Transition functions and their file:line** (every place `Post.state` is set/read in these 17 files):

1. **Birth: `None → awaiting_approval`** — `crosspost.py:269-295` (`_mint_surface_post`, inside `led.add_post(Post(... state=PostState.awaiting_approval ...))`). This is the ONLY place in the entire 17-file set that constructs a brand-new `Post`.
2. **`awaiting_approval → queued`** — `ledger.py:503-519` (`Ledger.approve_post`) — **NOT in this file cluster**, but is the sole gate function these 17 files rely on. Guarded by `if p is None or p.state is not PostState.awaiting_approval: return` — only an unapproved post can promote.
3. **`queued → awaiting_approval`** — `ledger.py:524-527` (`Ledger.unapprove_post`) — reverse gate, guarded on `p.state is PostState.queued`.
4. **`awaiting_approval → rejected`** — `ledger.py:520-523` (`Ledger.reject_post`) — guarded on `p.state is PostState.awaiting_approval`.
5. **`queued → submitting`** — `post/run.py:233` (`_publish_one`, the CLAIM transaction) — `post.state = PostState.submitting`, guarded by the check at `post/run.py:229`: `if post is None or post.state is not PostState.queued: return None`. **This is the sole gate examined in the SAFETY AUDIT below.**
6. **`submitting → submitted`** — set inside each poster's `.publish()`: `post/postiz.py:303` (`post.state = PostState.submitted`) and `post/zernio.py:252` (same). Guarded implicitly — only reached if `post/run.py:237` re-verified `post.state is PostState.submitting` before calling `poster.publish()`.
7. **`submitted → published`** — `post/run.py:258-266` (`_publish_one`), gated on `if (post.public_url or "").strip():` (the R1/D2 permalink gate) — a `submitted` state with no confirmed `public_url` does NOT promote to `published`.
8. **`submitted`(no url) `→ needs_reconcile`** — `post/run.py:271-276` — the else-branch of the same gate: `post.state = PostState.needs_reconcile` with `error_reason = "publish_missing_url: ..."`.
9. **network exception `→ needs_reconcile`** — `post/postiz.py:288-291` (`RequestException` before any response), `post/postiz.py:314-317` (5xx after the body was sent), `post/zernio.py:236-240`, `post/zernio.py:259-262` — all deliberately park rather than fail, because a bad-network-after-send is ambiguous (may already be live).
10. **network exception `→ failed`** — `post/postiz.py:311-312,321-326` (401 raises `PostizAuthError` — fatal, does NOT set `failed`; other non-2xx/non-401/non-5xx falls to `post.state = PostState.failed`), `post/zernio.py:256-257,266-269` (same shape), and `post/run.py:277-283` (`_publish_one`'s `except Exception as exc:` — any non-fatal exception during `_ensure_media`/`poster.publish` sets `failed` UNLESS already `needs_reconcile`, guarded: `if post.state is not PostState.needs_reconcile:`).
11. **`queued → failed`** — `post/run.py:329-334` (`_due_or_fail`) — a malformed/unparseable `scheduled_time` fails the post in its own short transaction rather than blocking the whole publish pass.
12. **`queued → queued`** (un-claim, no state change persisted as a transition but worth noting as a rollback) — `post/run.py:246-250` — a live publish attempt with an empty `account_id` un-claims `submitting → queued` (never went to network, so it's a rollback not a real "publish attempt").
13. **FINALIZE merge** — `post/run.py:292-308` — the tight transaction that writes back only `_NET_POST_FIELDS = ("state", "submission_id", "error_reason", "public_url", "media_urls", "published_at", "account_id")` onto a freshly-loaded ledger row, never the stale in-memory snapshot (avoids clobbering a concurrent writer — B4 lost-update guard).

**Read-only consumers of `PostState` in this cluster** (gate checks, not transitions):
- `post/run.py:229` — CLAIM guard (`is not PostState.queued`)
- `post/run.py:237` — NETWORK guard (`is not PostState.submitting`)
- `post/run.py:231` — `is_real_submission_id` re-publish breadcrumb (not a state check, a submission_id check)
- `post/run.py:345` — `publish_due`'s `led.posts_in_state(PostState.queued)` — the sole entry-point filter
- `pipeline.py:346,354-355,362` — `_build_summary`'s read-only counts (`posts_in_state(PostState.published)`, `.failed`, `.needs_reconcile`)

## Per-file breakdown

### crosspost.py — clip → per-(account,platform) Post fan-out, all posts born `awaiting_approval`

Module docstring: "one captioned, non-held, non-retired clip -> one Post per (active account,
platform)." No publish/network calls in this file — it is purely a minting stage.

- `_seed(account, platform, date_str, clip_id="") -> int` — deterministic SHA1-derived integer seed for the per-surface stagger RNG (NOT Python's builtin `hash()` — cross-process stable, FIX F00). Pure. Called by `surface_time`.
- `surface_time(base, account, platform, date_str, index, *, clip_id="", lead_minutes=0, hour_hint=None, tz=None) -> str` — computes the deterministic, monotonic-in-`index` scheduled ISO time for one surface/clip/index. Uses a stable `random.Random(seed)` stream (not reseeded per index) so `jitter < step` guarantees monotonicity (AUDIT H1/H2). `hour_hint` (from the Leg-3 timing-bias feature, outside this cluster) can pin the hour in operator-local tz. Pure function, no I/O. Called by `_mint_surface_post`.
- `_clip_for_aspect(led, cfg, moment_id, aspect) -> Clip` — finds a reusable rendered clip in the target aspect (`_REUSABLE_CLIP_STATES` allowlist) or renders one on demand via `render_moment` (in `clip.py`, outside this cluster — shells ffmpeg). **Side effect**: may mutate `led.clips` via `render_moment`. Called by `_mint_surface_post`, and (per call_graph) `studio.actions._warm_target_aspect`, `studio.actions.crosspost_to_account`.
- `render_spec(cfg, *, clip, hook, moment) -> tuple[render_id, wants_cut, profile, top_bias]` — pure, content-addressed owner-moment Render identity + cut decision (`wants_cut = bool(hook)`). Single source of truth shared by the crosspost mint AND the Studio re-burn action, so the two never drift. Called by `render_moment_file`, and per call_graph by `studio.actions_approve`, `studio.actions_review` (re-burn path).
- `render_moment_file(led, cfg, *, post, acct, target_clip, src, caller="approve") -> RenderPlan` — **the actual ffmpeg burn**: reads `m.hook` from the owner moment, renders (or falls back to a shared-clip hook burn via `overlay.burn_hook_only`) the owner-moment file. PURE render-to-disk, NO ledger mutation (safe to call lock-free). Fail-open: an ffmpeg failure logs a breadcrumb (`account_cut_failed` / `hook_burn_failed`) and falls back; `vpath` always exists on return. Called by Studio's approval/re-burn actions (per call_graph, `studio.actions_approve`).
- `_moment_is_live_target(m) -> bool` — MOM-1 guard: a captioned clip may only seed a post while its parent moment is `decided`/`clipped` (not `picked`, which means a re-pick superseded it). `m is None` fails open (existing behavior preserved). Pure. Called by `_seed_clips`.
- `_seed_clips(led) -> list[Clip]` — the crosspost candidate set: `captioned` clips, not held, not retired (clip or moment), whose moment is still a live render target. Pure read. Called by `crosspost_clips`.
- `_mint_surface_post(led, cfg, clip, m, surf, i, *, base, date_str, clip_dur, tgt, src_batch) -> int` — **the core per-(clip,surface) minting body**, hoisted from `crosspost_clips`'s deepest nesting. Returns `1` only for a batch-target exclusion (tally purposes), else `0`. Owns every per-surface skip gate in order:
  1. batch-target skip (`tgt and surf.account not in tgt`) — logs `batch_target_skip`, returns 1
  2. `affinity_admits` owner gate — logs `skipped_surface why=not_cast`, returns 0
  3. per-platform duration cap (`PLATFORM_MAX_SECONDS`) — logs `skipped_surface why=over_cap_...`, returns 0 (fail-OPEN on unknown duration)
  4. on-demand render via `_clip_for_aspect`; if the rendered clip's state isn't reusable — logs `skipped_surface why=render_...`, returns 0
  5. caption lookup (`clip.meta_captions.get(surface_key)`); `None` → logs `skipped_surface`, returns 0
  6. `decide_tag` (in `tagging.py`, outside this cluster) appends the artist handle line
  7. Leg-3 timing-bias hour hint (fail-open try/except around `timing_bias_hour_for`)
  8. `surface_time` computes the schedule
  9. **existing-post reconciliation**: if a post already exists for this content-addressed `pid` and is `rejected`/`failed`, it's popped and re-minted; if it's `awaiting_approval` and already present, skip (no hook rewrite — one owner-moment hook on the clip)
  10. `led.add_post(Post(..., state=PostState.awaiting_approval, ...))` — **the birth**, with a content-addressed `submission_id=f"fanops_{_hash('idemp', pid)}"` idempotency token stamped at birth (AUDIT H1) so an ambiguous publish is always pollable.

  Called by `crosspost_clips` only.
- `crosspost_clips(led, cfg, accounts, *, base_time) -> Ledger` — **the module's public entry point**. For each seed clip: resolves its moment/source, resolves the batch's `target_accounts`, loops every surface via `_mint_surface_post`, tallies batch-target skips vs. an unbatched zero-post outcome, and finally `led.set_clip_state(clip.id, ClipState.queued)` — the clip itself (not the posts) is marked queued once its fan-out pass completes. No casting-gate defer/repair (P8). Called by `pipeline._stage_crosspost`.

**Class `RenderPlan`** (`@dataclass`) — the pure result of `render_moment_file`: `render_id, vpath, produced, realized, profile, hook_source, batch_id, source_id`. No methods, pure data holder.

Module constants: `_STEP_MIN=40`, `_JITTER_MAX=30` (must stay `< _STEP_MIN` for monotonicity — asserted only in a comment, not code, see Anomalies), `_ANCHOR_SPAN=50`, `_REUSABLE_CLIP_STATES`, `_ASPECT_WH`.

### pipeline.py — the stage DAG orchestrator (`advance()`), owns the transaction boundaries

- `_aspects_for(accts) -> set[Fmt]` — the set of aspects any active account's platform needs, defaulting to `{Fmt.r9x16}` if empty. Pure. Called by `advance`.
- `_enabled_strategies(cfg) -> set[str]` — which structural-hook formats (`impact_cut`, `intro_tease`) are turned on. Pure. Called by `_stage_structural_hooks`.
- `_parse(ts)` — defensive ISO-8601 parse returning `None` on any failure, never raises. Pure. Called by `_build_summary`.
- `_quarantine(coll, eid, error_state, stage, exc, log) -> None` — **the shared per-unit failure stamp** (FIX F03): flips one entity to its error state via an immutable `model_copy(update=...)`, logs, and returns — so one bad source/moment/clip never wedges the whole pass. Mutates `coll` (the live ledger dict) in place. Called by `_stage_source_to_moments`, `_stage_ingest_moments`, `_stage_moment_hooks`, `_stage_render_and_caption`, `_stage_ingest_captions`.
- `_stage_source_to_moments(led, cfg, accts, log) -> Ledger` — transcribe → signals → request moments, per source, quarantined; skips `third_party` sources entirely (M1: inert to clip-production). **Side effects**: shells ffprobe/whisper/ffmpeg transitively (via `transcribe_source`, `detect_signals`, outside/partially-in this cluster). Called by `advance`.
- `_stage_ingest_moments(led, cfg, log) -> Ledger` — ingests decided moments for `moments_requested` sources, quarantined. Called by `advance`.
- `_stage_moment_hooks(led, cfg, accts, log) -> Ledger` — the pass-2 frame-seeing hook-author gate for `picks_decided` sources, quarantined. Called by `advance`.
- `_owner_caption_surfaces(cfg, m, accts) -> list` — pure: the (account, platform) tuples the moment OWNER should get captions for, gated by `affinity_admits` (same predicate as crosspost). Called by `_stage_render_and_caption`, `_stage_refresh_caption_requests`.
- `_stage_render_and_caption(led, cfg, accts, aspects, log) -> Ledger` — for each `decided` moment: renders its aspects, then requests captions for each successfully-rendered clip scoped to `_owner_caption_surfaces` (P10). Called by `advance`.
- `_stage_structural_hooks(led, cfg, log) -> Ledger` — M4/M5/M6: opens the intro-tease matcher gate, mines structural-hook suggestions, renders approved plans. Fail-open (log-only, no quarantine) since it's additive/opt-in. Called by `advance`.
- `_stage_refresh_caption_requests(led, cfg, accts, log) -> Ledger` — re-opens stale/incomplete caption gates BEFORE ingest, so incomplete caption coverage never silently blocks crosspost. Runs per-`try/except` per clip (log-only, not quarantine). Called by `advance`.
- `_stage_ingest_captions(led, cfg, log) -> Ledger` — ingests landed captions for `captions_requested` clips, quarantined (clip → error). Called by `advance`.
- `_stage_crosspost(led, cfg, accts, base_time, log) -> Ledger` — **wraps `crosspost_clips`** so a raise doesn't abandon the whole pass's in-memory progress, EXCEPT a fatal `AuthError` which re-raises by design (AUDIT M2 — crosspost has no direct backend call today, but the guard is future-proofed). Called by `advance`.
- `_reconcile_safe(cfg, log) -> None` — runs `reconcile_due` (outside this cluster) AFTER the main transaction commits, lock-free; gated on `cfg.is_live_backend`; fatal `AuthError` re-raises, anything else is logged and swallowed. Called by `advance`.
- `_publish_safe(cfg, log) -> None` — **runs `publish_due(cfg, now=None)` AFTER the main transaction commits**, lock-free (`publish_due` owns its own per-post locking internally); fatal `AuthError` re-raises; anything else logged and swallowed. This is the ONLY call site of `publish_due` found across all 17 files plus the pipeline. Called by `advance`.
- `pending_gate_count(cfg) -> int` — sums `len(pending(cfg, kind=k))` over `GATE_KINDS` — the "is there AI work?" signal for smart driving. Pure read (delegates to `agentstep.pending`). Per call_graph, called from `cli.py`/`daemon.py` (outside this cluster).
- `_build_summary(cfg, before) -> RunSummary` — **post-publish READ-ONLY reload** (`Ledger.load`, no lock) that builds the heartbeat dict: counts of sources/moments/clips/posts, `published`, `failed`, `published_in_run` (delta vs. the `before` snapshot taken at transaction entry), `last_published_age_hours`, `needs_reconcile`, `holds`, `hook_burn_failed`, `frames_unread`, `errors`, and `awaiting` (per-gate-kind pending counts). Also writes the read-only digest. Called by `advance`.
- `advance(cfg, *, base_time) -> RunSummary` — **THE top-level pipeline entry point**. Sequence: (1) validate accounts, (2) short ingest transaction, (3) lock-free `produce.run_all` pre-warm pass, (4) THE MAIN TRANSACTION — `Ledger.transaction(cfg)` wraps every deterministic stage from `_stage_source_to_moments` through `_stage_crosspost` (an uncaught exception here rolls back the ENTIRE pass, by design — heavy artifacts were already warmed lock-free so nothing expensive is lost), (5) `_reconcile_safe` (lock-free, after commit), (6) `_publish_safe` (lock-free, after commit — **this is where actual network publish happens**), (7) `_build_summary`. Called by `cli.py`/`daemon.py` (outside this cluster, per call_graph and CLAUDE.md).

**Class `AwaitingCounts(TypedDict)`** — `{moments, moment_hooks, captions}`. No methods. (`moment_casting` removed P11.)
**Class `RunSummary(TypedDict)`** — the full heartbeat shape documented above. No methods.

Module constant: `GATE_KINDS = tuple(_RESPONDER_SCHEMA)` — derived from `responder._SCHEMA` so the two can never drift (WS2 fix for a past bug where a 5th gate was added to the schema but not to a hand-copied literal here).

### router.py — read-only Moment-level hook-strategy classifier (structural-hooks M2), no publish surface

- `awaiting(key) -> str` — returns `f"clean_awaiting_strategy:{key}"`. Pure. Called by `route_moments`, and (per call_graph, outside this cluster) `intro_match.py`.
- `stitched(key) -> str` — returns `f"stitch:{key}"`. Pure. Called outside this cluster (`stitch_render.py`).
- `_has_peak_in_window(led, m) -> bool` — true if a source signal peak falls inside the moment's `[start, end]` window; guards non-numeric `t` values from the unvalidated on-disk sidecar (mirrors `clip.py`'s guard). Pure. Called by `route_moments`.
- `route_moments(led, cfg) -> Ledger` — classifies every `decided` Moment's `hook_strategy` into `text` / `clean_final` / `clean_awaiting_strategy:<key>`, RENDERS NOTHING (observe-only). Forward-only: never demotes an existing structural reservation. Called by `pipeline.advance` (gated on `cfg.hook_router`, default OFF).

Module constants: `STRATEGY_KEYS` (8 reserved format names, only `impact_cut`/`intro_tease` built today), `TEXT`, `CLEAN_FINAL`, `CLEAN_AWAITING`, `STITCH`.

### responder.py — autonomous agent-gate answerer (`claude -p` bridge), no direct publish

- `ManualResponder.__init__(self, cfg)` — stores cfg.
- `ManualResponder.answer_pending(self, cfg) -> int` — no-op, returns 0 (a human/external cron writes response files by hand).
- `_default_claude_model(kind, payload, *, cfg=None, log=None) -> dict` — the production model function: builds the gate's JSON schema + prompt, calls `claude_json_meta`, pins `cfg.llm_model_for(kind)`, attaches `frames` for vision gates (`moments`, `moment_hooks` only — `moment_casting` removed P11), emits provenance log line, stamps `hook_frames_unread` on degraded `moment_hooks` responses. Called by `LlmResponder.__init__` (as the default `_model`).
- `LlmResponder.__init__(self, cfg, model=None)` — binds `cfg` and either an injected test model or the cfg-bound `_default_claude_model`.
- `LlmResponder._answer_one(self, cfg, kind, model_cls, key, log) -> bool` — **the per-gate answer body**: reads the pending request JSON, captures `rid_before` (TOCTOU guard, AUDIT A3), calls the model (retrying once on `LlmTimeoutError`), re-verifies `rid_after == rid_before` (drops a stale answer if the gate was re-seeded mid-call — never applies a wrong-payload answer), verifies the model's echoed `request_id` (logs `rid_mismatch` on divergence but proceeds, self-stamping the authoritative rid), validates via `model_cls(**out)`, and writes the response atomically. Catches `LlmContextLimitError` (marks a visible `degraded_reason`), `ValidationError` (logs `invalid`, stays pending), and any other `Exception` (logs `error`, stays pending) — **WEDGE RISK: the bare `except Exception` swallows every failure silently; a degraded or failed gate leaves the key pending indefinitely with no caller-visible signal, so callers cannot distinguish a slow gate from a broken one**. Thread-safe by construction (all state is per-key local). Called by `answer_pending`.
- `LlmResponder._mark_context_limit(self, cfg, kind, key, reason) -> None` — back-compat shim: delegates to `_mark_gate_degraded` with a context-limit prefix. `_mark_gate_degraded` resolves the owning source for `moments`/`moment_hooks` keys directly, or via clip→moment→source for `captions` keys (`responder.py:148-153`). Loads+saves the ledger OUTSIDE the advance() flock. Wrapped in its own outer try/except. Called by `_answer_one`.
- `LlmResponder.answer_pending(self, cfg) -> int` — snapshots every pending `(kind, model_cls, key)` tuple serially (the glob-over-directory MUST be read serially, never inside a worker), then either runs sequentially (`cfg.concurrent_sources` OFF, default) or fans out over a `ThreadPoolExecutor(max_workers=cfg.concurrent_workers)` — each `(kind, key)` is a unique response path so concurrent writes never collide. Returns the count of fresh answers written. Called per call_graph by `cli.py`/`daemon.py` (outside this cluster).
- `get_responder(cfg)` — factory: `LlmResponder(cfg)` if `cfg.responder_mode == "llm"`, else `ManualResponder(cfg)`. Called outside this cluster (`cli.py`, `daemon.py`).

Module constants: `_SCHEMA` (kind → pydantic model), `_PROMPT` (kind → prompt-builder function), `_VISION_GATES = ("moments", "moment_hooks")` (`responder.py:46-48`).

### signals.py — ffmpeg silencedetect/scdet/astats free local signal pass (upstream of crosspost, not publish)

- `_cap_peaks(peaks) -> list` — top-`_MAX_PEAKS` (400) by score, re-sorted chronologically; a set already under the cap is returned unchanged (byte-identical for short sources). Fail-soft per peak (a missing/non-numeric score sorts as 0.0). Pure. Called by `detect_signals`.
- `parse_silences(stderr) -> list[dict]` — regex-extracts `silence_end:` timestamps into `{"t":..., "kind":"speech_resume", "score":0.5}` rows. Pure. Called by `detect_signals`.
- `parse_scene_changes(stderr) -> list[dict]` — regex-extracts `lavfi.scd.score/time` pairs into `{"t":..., "kind":"scene_cut", "score":...}` rows. Pure. Called by `detect_signals`.
- `_nearest_rms(t, windows)` — the RMS of the energy window closest in time to `t`, or `None` if no windows. Pure. Called by `apply_energy`.
- `apply_energy(peaks, windows) -> list[dict]` — pure, returns a NEW peak list scored on real loudness (speech_resume) / normalized scene-change (scene_cut) when energy windows are present; unchanged when absent (fail-soft enhancement). Called by `detect_signals`.
- `_silence_cmd(src) -> list[str]` — builds the `ffmpeg silencedetect` command. Pure. Called by `detect_signals`.
- `_scene_cmd(src) -> list[str]` — builds the `ffmpeg scdet` command. Pure. Called by `detect_signals`.
- `_run_ffmpeg(cmd) -> subprocess.CompletedProcess` — **shells ffmpeg**, translating an absent binary into a typed `ToolchainMissingError` (so the per-source quarantine in `pipeline.py` catches it cleanly) and letting a `_FFMPEG_TIMEOUT=600s` hang propagate as `TimeoutExpired` (same quarantine treatment). Called by `detect_signals`.
- `detect_signals(led, cfg, source_id) -> Ledger` — **the module's sole public entry point**. Phase-D lock-free-pre-warm-aware: checks a per-source sidecar JSON (`_SIDECAR_V=3`) first and adopts it (skipping both ffmpeg passes) if present, parseable, and version-current; otherwise shells the silence + scene-cut passes, then a best-effort (fail-soft) energy pass, merges via `apply_energy`, sorts + caps the peaks, sets `src.signal_peaks`/`src.duration`, transitions `led.set_source_state(source_id, SourceState.signalled)`, and persists the sidecar (best-effort, `OSError` swallowed — a write failure just means the next pass re-runs ffmpeg). Called by `pipeline._stage_source_to_moments`.

Module constants: `_SIL_END`, `_SCD` (regexes), `_SIDECAR_V=3`, `_MAX_PEAKS=400`, `_SCENE_SCALE=100.0`, `_FFMPEG_TIMEOUT=600.0`.

### agentstep.py — the file-contract primitive between deterministic code and the LLM agent (used by responder.py and every gate producer, no direct publish)

- `_dir(cfg) -> Path` — `cfg.agent_io / "requests"`, mkdir side effect. Called by every other function in this file.
- `request_path(cfg, kind, key) -> Path` — path builder. Pure. Called throughout the codebase (responder.py, moments.py, caption.py, etc.).
- `response_path(cfg, kind, key) -> Path` — path builder. Pure. Called widely.
- `latest_request_id(cfg, kind, key) -> str | None` — reads the request JSON's `request_id`; corrupt/torn file → logs `corrupt_request` and returns `None` (fail-closed, but with a breadcrumb so a stuck gate is distinguishable from "no request yet"). Called by `write_request`, `read_response`, `pending`, and `LlmResponder._answer_one` (TOCTOU rid capture).
- `write_request(cfg, *, kind, key, payload) -> str` — mints a fresh `request_id` (hash of kind/key/prev-id/payload), atomically writes (temp + `os.replace`, same-filesystem asserted), and deletes any stale response file (a fresh request invalidates the old answer). **Side effect**: disk write, unlinks the response. Called widely across moments/caption/casting producer modules (outside this cluster).
- `write_response(cfg, kind, key, json_text) -> None` — atomically writes the answer (temp + `os.replace`), `chmod 0o600` best-effort (owner-only — carries hook/caption/casting content). Called by `LlmResponder._answer_one`.
- `read_response(cfg, kind, key, model) -> T | None` — reads + validates the response against `model`, returning `None` on corruption (logged), staleness (`request_id` mismatch — silently ignored, by design: this is the real safety net against applying a stale answer), or `ValidationError`. Called widely by ingest-side gate consumers (moments.py, caption.py, casting.py, intro_match.py — outside this cluster).
- `discard_gate(cfg, kind, key) -> None` — unlinks both request+response files, idempotent. Called outside this cluster (amplify/re-pick flows).
- `discard_gates_for(cfg, kind, key_prefix) -> int` — globs and discards every gate under a prefix; returns count. Called outside this cluster.
- `pending(cfg, *, kind) -> list[str]` — globs all request files of `kind`, returns keys whose response is missing or stale (mismatched `request_id`); logs `corrupt_response_in_pending` on a torn response file (fail-closed: stays pending). Called by `pipeline.pending_gate_count`, `pipeline._build_summary`, `LlmResponder.answer_pending`.

### autopilot.py — one-command "make autonomous" (env + daemon install), NEVER publishes

- `set_env_var(env_path, key, value) -> None` — idempotently sets `KEY=value` in a `.env` file preserving all other lines (secrets included), tolerates `export KEY=value` and spacing variants, rejects a value containing a newline (`ValueError` — would inject an arbitrary line), atomic write (temp + `os.replace`). Called by `autopilot`.
- `unset_env_var(env_path, key) -> None` — mirror removal, atomic write, no-op if file/key absent. Called outside this cluster (per call_graph, likely `studio.actions` go-live flows).
- `autopilot(cfg, *, interval, install_daemon=True) -> dict` — sets `FANOPS_RESPONDER=llm` durably (`.env` + in-process `os.environ`), calls `doctor.doctor_report` for a readiness snapshot, and (unless `install_daemon=False`) installs the launchd daemon via `daemon.install` — off-darwin this raises `RuntimeError` which is caught and surfaced as a `daemon_note`, never crashing. **Explicitly documented as never publishing and never editing the ledger** — it only enables the *work* pipeline's autonomy. Called by `cli.py` (outside this cluster).

### postiz_lifecycle.py — on-demand Docker start for the self-hosted Postiz stack, fail-open by construction

- `_is_local(url) -> bool` — `"localhost" in url or "127.0.0.1" in url`. Pure. Called by `_should_autostart`.
- `_backend_is_postiz(cfg) -> bool` — true if `cfg.poster_backend == "postiz"`, OR (M3 fix) if the system `is_live` and ANY account's `live_ready_channels()` resolves to a `postiz` provider (tracks actual per-channel providers, not the legacy global). Wrapped in a bare `except Exception: return False`. Called by `_should_autostart`.
- `_should_autostart(cfg) -> bool` — the full autostart gate: not under pytest, `FANOPS_POSTIZ_AUTOSTART != '0'`, backend is postiz, `POSTIZ_URL` is local, the on-demand script exists AND `docker` is on PATH. Pure/read-only. Called by `ensure_up`.
- `ensure_up(cfg) -> None` — **shells `bash postiz-ondemand.sh ensure`** with a 150s timeout, no-op unless `_should_autostart`. Any exception is caught and written to stderr (fail-open — a still-down Postiz surfaces normally through the poster's own connection error). Called by `post/run.py:publish_due` (line 351, only when `due` is non-empty) and `post/run.py:publish_post` (line 389, always, on the operator's explicit "Publish now" click).

### post/\_\_init\_\_.py — Poster protocol + factory dispatch

- `Poster.publish(self, led, post_id) -> Ledger` — the `Protocol` method signature only (structural typing, no implementation). Implemented by `DryRunPoster`, `PostizPoster`, `ZernioPoster`.
- `get_poster(cfg, backend=None) -> Poster` — **the poster factory, and the one hard safety refusal in this cluster**: if `cfg.is_live` AND the resolved backend is `"dryrun"`, **raises `RuntimeError`** rather than constructing a `DryRunPoster` on a live system (ROOT FIX for a bug that once wrote 7 fake-published rows). Otherwise resolves through `providers.get_provider`, falling back to `DryRunPoster` if unresolved. Called by `post/run.py:_publish_one` (line 252).
- `get_media_uploader(cfg, backend=None) -> Callable[[Config, Path], str]` — resolves the (cfg, Path)→URL uploader function through the provider registry, falling back to the dryrun `file://` uploader for an unknown backend (fail-safe, never a crash — no live account should ever route here). Called by `post/media.py:ensure_render_media`, `post/run.py:_ensure_media`.

### post/compress.py — fail-open video shrink for upload size caps (Zernio TikTok 413)

- `maybe_shrink_for_cap(cfg, path, cap, *, label="upload") -> Path` — returns `path` unchanged if within cap; else **shells ffmpeg** with escalating CRF (28→32→36→40) into a temp dir, returning the first output that clears the cap, or the original `path` on total failure (fail-open — a still-oversize upload will fail normally downstream, not silently). Called by `apply_shrink_to_post`, `post/zernio.py:zernio_upload_media`.
- `media_path_for_post(led, post) -> Path | None` — resolves the on-disk media file a post would upload, preferring `post.render_id`'s Render path, then `file://` media_urls, then falling back to the parent clip's path. Pure read. Called by `apply_shrink_to_post`.
- `publish_backend_for_post(cfg, post) -> str` — resolves the effective per-channel provider via `Accounts.load(cfg).effective_provider`, falling back to `cfg.poster_backend or "dryrun"` on any exception (with a breadcrumb — "fallback isn't silent"). Called outside this cluster + by tests (not directly called within these 17 files' bodies per the grep, but exported for `_ensure_media`'s `backend` param resolution upstream in `publish_due`/`publish_post`).
- `upload_cap_bytes(cfg, post, backend) -> int | None` — returns `cfg.zernio_max_upload_bytes` only for `(tiktok, zernio)`, else `None` (no cap). Pure. Called by `apply_shrink_to_post`, `post/run.py:_ensure_media`.
- `apply_shrink_to_post(cfg, led, post, *, backend=None) -> bool` — shrinks local media under the publish cap if needed, persisting the new path onto the Render and `file://` media_urls; returns `True` if within cap (after shrink or originally). Called by `post/run.py:_ensure_media`.
- `persist_post_shrink(cfg, snapshot_led, post_id) -> None` — persists an in-memory shrink result from a lock-free snapshot into a fresh transaction. **CORRECTED: LIVE — called via a lazy import at `studio/actions.py:395-396` (C9); not in the 17-file set, which is why the call graph missed it. See Anomalies.**

### post/dryrun.py — dry-run PREVIEW writer, the "not live" default poster

- `write_preview(cfg, post) -> None` — writes the exact would-send payload (account/platform/text/media_urls/scheduled_time) to `<scheduled>/<post_id>.json`, `chmod 0o600` best-effort. **Deliberately writes NO state, no submission_id, no public_url** — a dry run distributes nothing and fabricates no distribution artifacts. Called by `post/run.py:publish_due` (line 365) and `post/run.py:publish_post` (line 401) — the sole two call sites, both at the dryrun-boundary check (`provider == "dryrun"`).
- `DryRunPoster.__init__(self, cfg)` — stores cfg.
- `DryRunPoster.publish(self, led, post_id) -> Ledger` — calls `write_preview` only; kept as the `Poster`-protocol handle for `get_poster`'s fallback construction, though the module docstring notes this path is exercised only by a caller that still routes a dryrun post through the `Poster` contract (post-M1, `publish_due`/`publish_post` call `write_preview` directly and never reach this method in the normal flow).

### post/media.py — media-URL resolution + per-clip/per-render upload caching

- `dryrun_media_url(path) -> str` — `f"file://{Path(path).resolve()}"`. Pure. Called by `providers._dryrun_uploader`.
- `_media_cache_hit(url, backend) -> bool` — determines whether a cached `media_url` is safe to reuse for the CURRENT backend (a `file://` URL is only valid for dryrun; a Postiz `"id|path"` URL is only valid for postiz; etc. — prevents reusing a wrong-backend cached URL after a channel remap). Pure. Called by `ensure_render_media`, `ensure_clip_media`.
- `_uploader_kwargs(backend, account_id) -> dict` — only postiz/zernio uploaders receive `account_id` (never the dryrun uploader). Pure. Called by `ensure_render_media`, `ensure_clip_media`, and `post/run.py:_ensure_media`.
- `ensure_render_media(led, cfg, render_id, local_path, backend, **kw) -> str` — uploads a per-account render's file ONCE, caching the URL on the `Render` (CULM-2, FIX-F44 parity for variant renders). A missing render (race/GC) falls back to a direct upload with no cache home, never crashes. **Side effect**: network upload (unless cache hit), mutates `r.media_url` in-memory (persisted by `post/run.py`'s finalize transaction). Called by `post/run.py:_ensure_media`.
- `ensure_clip_media(led, cfg, clip_id, backend=None, *, account_id=None) -> str` — uploads the clip's file ONCE, caching the URL on the `Clip` (FIX F44 — v1 re-uploaded per post). **Side effect**: network upload (unless cache hit), mutates `clip.media_url` in-memory. Called by `post/run.py:_ensure_media`.

### post/metrics.py — read-only metrics/status clients (Postiz, Zernio, Meta Graph); NOT a publish surface but load-bearing for `reconcile.py`'s status polling (outside this cluster) which feeds the publish→published transition indirectly via URL backfill

- `_safe(cfg, text, limit=200) -> str` — scrubs every provider key from an external response body before it can land in an error message. Pure. Called throughout this file.
- `_json_or_raise(resp, label, cfg=None)` — converts a non-JSON 200 response into a diagnosable `RuntimeError` instead of letting `resp.json()`'s raw `JSONDecodeError` propagate and abort the whole metrics pass (ECC fix #4). Called throughout this file.
- `_latest_total(series) -> float | None` — collapses a Postiz analytics label's time-series to its latest dated point (never a positional guess). Pure. Called by `_map_analytics`.
- `_map_analytics(arr) -> dict` — maps Postiz's documented `[{label, data, percentageChange}]` array to `lift_score`-compatible keys via `_POSTIZ_LABEL_MAP`. Pure. Called by `PostizMetricsClient._fetch_one`.
- `PostizMetricsClient.__init__(self, cfg, *, submission_ids=None)` — resolves base URL + API key (raises `PostizAuthError` if missing).
- `PostizMetricsClient._fetch_one(self, submission_id, date) -> tuple[dict, list]` — **GETs** `{base}/public/v1/analytics/post/{id}` with `Authorization: {apiKey}`; 401 raises `PostizAuthError`; other non-2xx raises `RuntimeError`. Called by `list_posts`.
- `PostizMetricsClient.list_posts(self, window="30d") -> list[dict]` — per-post-isolated fetch loop: a 401 is fatal (re-raised, halts the whole pass), any other exception is logged and the id skipped (never wholesale-zeroes an already-captured metric snapshot). Called outside this cluster (`track.py` — the learning-loop metrics puller, per CLAUDE.md's insights-culmination note).
- `PostizStatusClient.__init__(self, cfg)` — resolves base URL + key.
- `PostizStatusClient.get_status(self, submission_id, publish_date=None) -> dict` — **GETs** `{base}/public/v1/posts` with a `±35d` ISO `startDate`/`endDate` window anchored on `publish_date`; maps Postiz's `state` field via `_POSTIZ_STATE_MAP` (only `PUBLISHED`→published, `ERROR`/`FAILED`→failed; everything else, including an absent row, parks as `scheduled`/`unknown` — NEVER guesses `failed` for an unrecognized state, avoiding a double-post via re-queue). Returns `publicUrl` (the real IG permalink from `releaseURL`) only on a published row. Called outside this cluster (`reconcile.py`).
- `_extract_zernio_state` / `_extract_zernio_permalink` / `_zernio_platform_rows` — Zernio response-shape extractors (status/permalink live under `platforms[]`, not top-level — a 2026-06-30-verified live-shape fix). Pure. Called by `ZernioStatusClient.get_status`.
- `ZernioMetricsClient` / `ZernioStatusClient` — mirror the Postiz classes for the Zernio/TikTok backend (Bearer auth, `/analytics?postId=`, `/posts/{id}`). Same per-post isolation + fatal-401 pattern.
- `_zernio_num`, `_map_zernio_analytics`, `_zernio_platform_metric_payload`, `_zernio_analytics_payload`, `_zernio_raw_labels` — Zernio's more defensive response-shape normalization (flat dict / labeled array / one-level-nested wrapper, all INTEGRATION CHECKPOINTS per the module comments). Pure. Called by `ZernioMetricsClient._fetch_one`.
- `_retention_fraction(avg_watch_ms, duration_s) -> float | None` — computes a `[0,1]` watch-through rate, clamped, `None` if either input is missing/non-positive (never fabricated). Pure. Called by `GraphInsightsClient.list_posts`.
- `GraphInsightsClient.__init__(self, cfg, *, posts=None, insights_fn=None)` — the sole IG performance reader (Leg 2 of the insights-culmination rebuild per CLAUDE.md); `PostizMetricsClient` is dead for IG.
- `GraphInsightsClient._default_insights(self, media_id, product_type, handle)` — resolves per-account Meta creds and calls `meta_graph.media_insights`. Called by `list_posts`.
- `GraphInsightsClient.list_posts(self, window="30d") -> list[dict]` — per-post isolated; a `MetaInsightsScopeError` is the ONE external gate that fails the WHOLE pass CLOSED + LOUD (`insights_blocked` flag persisted via `meta_graph._set_insights_blocked`, loop `break`s) — deliberately not per-post isolated, because a scope refusal means every subsequent call would also fail identically. A transient `None` result skips just that post. Called outside this cluster (`track.py`).

### post/postiz.py — the Postiz REST poster backend

- `_base(cfg) -> str` — resolves `cfg.postiz_url`, raises `RuntimeError` if unset. Called throughout this file.
- `_key(cfg) -> str` — resolves `cfg.postiz_api_key`, raises `PostizAuthError` if unset. Called throughout this file.
- `_extract_postiz_id(body) -> str | None` — accepts several id-key aliases + a nested `posts[0].id` shape (integration checkpoint — the exact response key isn't pinned in public docs). Pure. Called by `PostizPoster.publish`.
- `_postiz_permalink(cfg, post_id) -> str | None` — **always returns `None`** by design: the publish-time 2xx response never carries a permalink for a freshly-scheduled post; the real permalink is captured later by `PostizStatusClient` during reconcile. Pure. Called by `PostizPoster.publish`.
- `_postiz_image(u) -> dict` — splits a cached `"id|path"` upload reference back into `{id, path}` (this Postiz version validates both on `image[]`). Pure. Called by `build_postiz_payload`.
- `_youtube_tags(hashtags) -> list[dict]` — strips leading `#`, dedupes, caps total label length under ~480 chars (guards Postiz's `@IsYoutubeTagsLength` 422). Pure. Called by `build_postiz_payload`.
- `build_postiz_payload(*, integration_id, platform, content, media_urls, scheduled_time, title=None, hashtags=None) -> dict` — builds the exact Postiz create-post body, branching on YouTube's distinct settings schema (`title` 2-100 chars required, `type: "schedule"`, `settings.__type` discriminated union). Pure. Called by `PostizPoster.publish`.
- `postiz_upload_media(cfg, path, **kw) -> str` — **POSTs multipart** to `/public/v1/upload`; 401 → `PostizAuthError`; non-2xx → `RuntimeError`; missing `id`/`path` in the response → `RuntimeError`. Returns `"{id}|{path}"`. Called by `providers._postiz_uploader` (lazy factory — see Anomalies re: call-graph false-positive dead-code flag).
- `postiz_list_integrations(cfg) -> list[PostizIntegration]` — **GETs** `/public/v1/integrations`; 401 → `PostizAuthError`; accepts a bare list or `{"integrations":[...]}`, skips malformed entries rather than raising. Called by `postiz_health_probe`, `postiz_check_auth`, and (per call_graph, outside this cluster) `studio.actions_golive`.
- `postiz_health_probe(cfg) -> PostizHealth` — exercises the real `/integrations` endpoint (goes past nginx's own always-healthy Docker health check, R5/D13). Never raises; a 401 is reported as unhealthy-with-status, not thrown; any other exception is logged (type + truncated message, never the key) and reported unhealthy. Called by `postiz_check_auth`.
- `_status_of(exc) -> int | None` — regex-extracts a status code from a `RuntimeError` message. Pure. Called by `postiz_health_probe`.
- `postiz_check_auth(cfg) -> bool` — thin bool wrapper over `postiz_health_probe`, re-deriving the legacy raise-on-401 contract (the Go-Live "Save & test" button needs to name the key on 401). Called outside this cluster (`studio.actions_golive`, `doctor.py`).
- `PostizPoster.__init__(self, cfg)` — resolves base URL + auth header.
- `PostizPoster._youtube_title(self, post, led=None) -> str` — reads the owner-moment hook (`m.hook` via clip→moment) as the YouTube title, floored to `cfg.artist_name` if too short/empty (`postiz.py:353-362`). Called by `publish`.
- `PostizPoster.publish(self, led, post_id) -> Ledger` — **THE actual publish network call**: builds the payload, retries up to `_MAX_RETRIES=4` with exponential backoff+jitter on 429, and on completion:
  - `RequestException` (never reached the server, or response lost) → `needs_reconcile` (ambiguous, may be live)
  - 200/201 → extract id; no id → `needs_reconcile` ("2xx but no recognizable id"); id found → `submitted` + `submission_id` + `public_url = _postiz_permalink(...)` (always `None` today, so effectively `post.public_url` stays whatever it was, typically empty)
  - 401 → raises `PostizAuthError` (fatal, halts the whole publish pass upstream)
  - 5xx → `needs_reconcile` (ambiguous after body sent, no idempotency key — never re-POST)
  - 429 → sleep + retry (up to 4 times)
  - other 4xx → falls through the loop to the final defensive guard: `if post.state is not PostState.needs_reconcile: post.state = PostState.failed` (never downgrades an ambiguous-live park to a re-queueable `failed`)

  Called by `post/run.py:_publish_one` (via `poster.publish(led, post.id)`).

**Classes**: `PostizIntegration(NamedTuple)` — `id, name, platform`, no methods. `PostizHealth(NamedTuple)` — `healthy, status_code, hint`, no methods.

### post/providers.py — the provider registry (single home for "who publishes a channel")

- `_postiz_poster(cfg)` / `_zernio_poster(cfg)` / `_dryrun_poster(cfg)` — lazy-import factory lambdas returning the concrete `Poster` instance. Called via the `PROVIDERS` dict's `make_poster` field (i.e., indirectly through `Provider.make_poster(cfg)` calls in `get_poster`). **Flagged as "unreferenced" by the static call-graph tool** — false positive, see Anomalies.
- `_postiz_uploader(cfg)` / `_zernio_uploader(cfg)` — lazy-import factory lambdas returning the concrete upload function. Same false-positive dead-code flag.
- `_dryrun_uploader(cfg)` — returns a closure wrapping `dryrun_media_url` that absorbs any `**kw` (so `account_id` passed by `ensure_*` callers is silently accepted and ignored). Called directly by `post/__init__.py:get_media_uploader` (the one NON-lazy-factory caller — this one IS a real, directly-invoked function, not routed through the `PROVIDERS` dict, matching the call-graph's correct "called by" entry).
- `Provider.has_creds(self, cfg) -> bool` — `bool(self.creds_env) and cfg.backend_has_creds(self.name)`; dryrun (empty `creds_env`) is never live-capable. Called outside this cluster (`doctor.py`, `studio.actions_golive`).
- `get_provider(cfg, name) -> Provider | None` — `PROVIDERS.get(name)`; the CALLER picks the fallback (both `get_poster` and `get_media_uploader` fall back to dryrun for an unrecognized name). Called by `post/__init__.py:get_poster`, `get_media_uploader`, and `post/media.py:ensure_render_media`.

**Class `Provider(@dataclass frozen=True)`** — `name, kind, creds_env, available, make_poster, make_uploader` + the `has_creds` method above.

Module constant: `PROVIDERS = {"postiz": ..., "zernio": ..., "dryrun": ...}`.

### post/run.py — **the publish stage**: the CLAIM→NETWORK→FINALIZE machine and its two public entry points

- `_now(now) -> datetime` — parses an optional ISO string or defaults to `datetime.now(utc)`. Pure. Called by `publish_due`.
- `_archive_published(cfg, post) -> None` — writes a day-bucketed, human-browsable JSON record to `06_published/<YYYY-MM-DD>/<post_id>.json` (0600, atomic create via `os.open`+`O_CREAT|O_WRONLY|O_TRUNC`). **Fully fail-open**: any exception is caught, logged, and re-caught if even the logging fails — the archive is a convenience artifact, never a publish blocker. Called by `_publish_one` (only on a confirmed `PostState.published` final state, OUTSIDE the finalize transaction so an archive failure can never roll back the just-committed publish).
- `_is_fatal_auth_error(exc) -> bool` — `isinstance(exc, AuthError)` — matched by TYPE, not message substring (AUDIT H8 fix for both under- and over-firing on message-matching). Pure. Called by `_publish_one`.
- `reset_publish_throttle() -> None` — test-only helper clearing the module-level in-process throttle dict. **Flagged as "unreferenced" by the call-graph tool** — genuinely test-only per its own docstring, likely called from `tests/` (outside the `src/` scope the call graph indexes). Not a real defect.
- `_publish_throttle_key(provider, account_id) -> tuple[str,str]` — pure key builder. Called by `_publish_throttle_wait`.
- `_publish_throttle_wait(cfg, provider, account_id) -> None` — sleeps if the last publish on this `(provider, integration)` pair was too recent; **Postiz-only, live-only** (`cfg.postiz_publish_per_min`). Mutates the module-level `_publish_throttle_last` dict. Called by `_publish_one`.
- `_post_provider(cfg, accounts, post) -> str | None` — **the per-post provider resolver**: `"dryrun"` unconditionally when `not cfg.is_live` (the global switch cannot be bypassed by any per-channel override); when live, delegates to `accounts.effective_provider(post.account, post.platform)`, which may return `None` if the channel has no configured provider (never global-defaults a new deployment). Called by `publish_due`, `publish_post`.
- `_materialize_variant_media(led, cfg, post, accts) -> None` — **P9 no-op stub** (`post/run.py:178-180`): owner-moment hook is burned on the shared clip at `render_moment`; there is no per-post publish-time materialize path. Called by `_ensure_media` (call retained for API stability).
- `_resolve_publish_account_id(accounts, post, *, cfg=None) -> str | None` — re-resolves the CURRENT integration id at publish time (a Go-Live remap since crosspost minted the post would otherwise use a frozen stale id); fail-open to `None` on any resolution error, with a breadcrumb when `cfg` is supplied. Called by `publish_due`, `publish_post`.
- `_ensure_media(led, cfg, post, backend, *, account_id=None) -> None` — resolves `post.media_urls` to network-fetchable URLs: calls the no-op `_materialize_variant_media` first, applies shrink if the backend has an upload cap, then either uploads the clip fresh (`ensure_clip_media`) or, for a pre-stamped render post, uploads the actual render file (`ensure_render_media`) rather than the parent clip's base render. Called by `_publish_one`.
- **`_publish_one(cfg, post_id, backend, *, account_id=None) -> str | None`** — **THE per-post publish state machine, the highest-priority function in this audit.** Three phases:
  1. **CLAIM** (`Ledger.transaction`): re-reads the post under the ledger lock; `if post is None or post.state is not PostState.queued: return None` — the double-post guard; flips `queued → submitting` and persists on transaction exit (crash-safe intent, FIX F11).
  2. **NETWORK** (no lock): loads a throwaway ledger snapshot, re-verifies `post.state is PostState.submitting` (else vanished/changed under us, return `None`); refreshes `account_id` if it diverged from the frozen `post.account_id` (Go-Live remap); refuses to construct an empty-integration-id live POST (un-claims back to `queued` instead — CULM-1 guard); calls `get_poster(cfg, backend)` then `_ensure_media` + `poster.publish(led, post.id)`; applies the R1/D2 `submitted → published` gate on `public_url` presence; catches any exception, re-raising a fatal `AuthError` (H8) or else marking `failed` (unless already `needs_reconcile`, C1/#17 guard against downgrading an ambiguous-live park).
  3. **FINALIZE** (`Ledger.transaction`): merges ONLY `_NET_POST_FIELDS` onto a freshly-loaded ledger row (never the stale in-memory snapshot — B4 lost-update guard), plus the clip/render media-URL caches and a possibly-shrunk render path.

  Fires `_archive_published` outside the finalize transaction on a confirmed `published` final state. Called by `publish_due`, `publish_post`.
- `_due_or_fail(cfg, post, cutoff) -> bool` — schedule gate (FIX F12): a post with no `scheduled_time` is treated as NOT due (parks, breadcrumb, stays `queued` — CULM-4 no-auto-publish defense-in-depth); a malformed/unparseable time fails the post in its own short transaction. Called by `publish_due`.
- **`publish_due(cfg, *, now=None, account=None, batch_id=None) -> dict`** — **the module's primary entry point** (and the sole call site is `pipeline._publish_safe`). Loads a lock-free snapshot, filters to `posts_in_state(PostState.queued)` that are `_due_or_fail`-true, optionally filtered by `account`/`batch_id`; if any posts are due, calls `postiz_lifecycle.ensure_up` (starts the local Docker stack on demand); then per due post: resolves the provider (skip with `no_provider` breadcrumb if `None`), routes dryrun posts through `write_preview` + `dryrun_not_distributed` breadcrumb (never claims, stays `queued`), resolves the current `account_id` (skip with `no_integration_id` breadcrumb if empty), and calls `_publish_one`. Returns a summary dict. Called by `pipeline._publish_safe`.
- **`publish_post(cfg, post_id) -> str | None`** — the Studio "Publish now" manual override: same dryrun/no-provider/no-integration-id checks as `publish_due` but for exactly ONE post, IGNORING its schedule (no `_due_or_fail` gate). Also calls `ensure_up` unconditionally (operator explicitly clicked). Called outside this cluster (per call_graph: `studio.actions_review`/`studio.actions_schedule`, the "Publish now" button handler).

Module constants: `_NET_POST_FIELDS`, `_publish_throttle_last` (module-level mutable dict — the one piece of true global mutable state in this cluster, documented as in-process-only since the daemon is single-process).

### post/zernio.py — the Zernio (hosted TikTok) REST poster backend

- `_base(cfg) -> str` — `cfg.zernio_url or "https://zernio.com/api/v1"`. Pure. Called throughout.
- `_key(cfg) -> str` — resolves `cfg.zernio_api_key`, raises `ZernioAuthError` if unset. Called throughout.
- `_extract_zernio_id(body) -> str | None` — accepts `_id`/`id`/`postId` aliases + a nested `post.{...}` shape. Pure. Called by `ZernioPoster.publish`.
- `_tiktok_settings() -> dict` — the required `platformSpecificData.tiktokSettings` block (privacy/consent flags) — discovered live 2026-06-29 as mandatory (omitting it 400s with a misleading "require media content" error). Pure. Called by `build_zernio_payload`.
- `_zernio_media_url(u) -> str` — strips a Postiz-style `"id|url"` cache value down to the bare URL Zernio needs. Pure. Called by `build_zernio_payload`.
- `build_zernio_payload(*, account_id, platform, content, media_urls, scheduled_time) -> dict` — builds the create-post body with `publishNow: true` (FanOps owns the schedule via its own `queued`/due-gate, so Zernio is never handed a future time). Pure. Called by `ZernioPoster.publish`.
- `_extract_zernio_media_url(body) -> str | None` — accepts several media-upload response shapes (integration checkpoint). Pure. Called by `zernio_upload_media` (as a back-compat fallback).
- `zernio_upload_media(cfg, path, *, account_id=None) -> str` — **the two-step upload contract discovered live 2026-06-29**: (1) POST `/media/upload-token` with `{accountId}` → single-use token; (2) POST `/media/upload?token=...` multipart field `files`. Shrinks via `maybe_shrink_for_cap` first if oversize, raising `RuntimeError` if still over cap after shrink. 401 at either step → `ZernioAuthError`. **Raises `RuntimeError` if `account_id` is omitted** (required for the live per-account token mint). Called by `providers._zernio_uploader` (lazy factory — same false-positive flag as the Postiz uploader).
- `zernio_list_accounts(cfg) -> list[ZernioAccount]` — GETs `/accounts`; 401 → `ZernioAuthError`; accepts a bare list or `{"accounts":[...]}`, skips malformed entries. Called by `zernio_check_auth` and (per call_graph, outside this cluster) `studio.actions_golive`.
- `zernio_check_auth(cfg) -> bool` — probes `zernio_list_accounts`; re-raises `ZernioAuthError`, else `False` on any other failure (logged, key never echoed). Called outside this cluster (`studio.actions_golive`, `doctor.py`).
- `ZernioPoster.__init__(self, cfg)` — resolves base URL + Bearer header.
- `ZernioPoster.publish(self, led, post_id) -> Ledger` — **the actual publish network call**, structurally identical retry/error shape to `PostizPoster.publish`: `RequestException` → `needs_reconcile`; 200/201 with no extractable id → `needs_reconcile`; 200/201 with an id → `submitted` + `submission_id` (the `public_url` is left as `safe_public_url(None) or post.public_url` — a placeholder mirroring Postiz's "no permalink at publish time" pattern, the real permalink comes later from `ZernioStatusClient`); 401 → `ZernioAuthError` (fatal); 5xx → `needs_reconcile`; 429 → backoff+retry (max 4); other 4xx → falls to the same never-downgrade-needs_reconcile-to-failed guard. Called by `post/run.py:_publish_one`.

**Classes**: `ZernioAccount(NamedTuple)` — `id, name, platform`, no methods.

## Postiz/Zernio API integration

### Postiz (self-hosted, free, `docs.postiz.com/public-api`)

| Endpoint | Method | Auth | Payload / notes |
|---|---|---|---|
| `/public/v1/upload` | POST multipart | `Authorization: {apiKey}` (raw key, not Bearer) | `{"file": <bytes>}` → `{"id", "path"}`, cached as `"id\|path"` |
| `/public/v1/posts` | POST | `Authorization: {apiKey}` | `{type:"schedule", date, shortLink:false, tags:[], posts:[{integration:{id}, value:[{content, image:[{id,path}...]}], settings:{__type, post_type|title/type for youtube}}]}` |
| `/public/v1/integrations` | GET | `Authorization: {apiKey}` | `[{id, identifier/platform, name/displayName}]` or `{"integrations":[...]}` |
| `/public/v1/analytics/post/{id}` | GET | `Authorization: {apiKey}` | `?date=<unix-ms>` → `[{label, data:[{total,date}], percentageChange}]` |
| `/public/v1/posts` (list, reconcile) | GET | `Authorization: {apiKey}` | `?startDate&endDate` (ISO date, required — `display`/`date` params 400) → row with `state`, `releaseURL` |

Auth: a single `POSTIZ_API_KEY` env var, sent verbatim as the `Authorization` header value (no `Bearer` prefix). Never logged/echoed; a 401 body is explicitly withheld from every error message (`redact()` scrubs it elsewhere too).

### Zernio (hosted TikTok, `zernio.com/api/v1`)

| Endpoint | Method | Auth | Payload / notes |
|---|---|---|---|
| `/media/upload-token` | POST | `Bearer {key}` | `{"accountId"}` → `{"token"}` (single-use, ~60s TTL) |
| `/media/upload?token=` | POST multipart | `Bearer {key}` | field name `files` (plural) → `{"success", "files":[{"url"}]}` |
| `/posts` | POST | `Bearer {key}` | `{content, publishNow:true, platforms:[{platform, accountId, platformSpecificData?}]}`, TikTok requires `tiktokSettings` |
| `/accounts` | GET | `Bearer {key}` | `[{_id, platform, name}]` or `{"accounts":[...]}` |
| `/analytics?postId=` | GET | `Bearer {key}` | response shape varies — flat dict / labeled array / `platformAnalytics[]` (documented integration checkpoints) |
| `/posts/{id}` | GET | `Bearer {key}` | status + permalink under `platforms[]` (2026-06-30-verified live shape) |

Auth: `ZERNIO_API_KEY` as a `Bearer` token. Same never-logged / 401-body-withheld discipline.

### Meta Graph (IG insights only, read via `meta_graph.py` outside this cluster, invoked from `GraphInsightsClient` in `post/metrics.py`)

Not a publish path — read-only insights. Per-account creds resolved via `meta_graph.resolve_meta_creds`. A `MetaInsightsScopeError` fails the whole metrics pass closed+loud (`insights_blocked`).

## SAFETY AUDIT: publish-capable call sites and their approval-gate status

**Claim under test**: is `_publish_one`'s CLAIM transaction (`post.state is PostState.queued` check, `post/run.py:229`) the SOLE gate standing between an `awaiting_approval` post and a live network publish, and does any path bypass it?

**Verdict: the gate holds. No bypass found across all 17 files.**

Evidence, traced exhaustively:

1. **Only two functions ever reach the network**: `PostizPoster.publish` (`post/postiz.py:268`) and `ZernioPoster.publish` (`post/zernio.py:226`). Both are `Poster.publish(self, led, post_id) -> Ledger` implementations, invoked ONLY from `post/run.py:_publish_one` line 256 (`led = poster.publish(led, post.id)`). Grepped: no other call site of `.publish(` on a poster object exists in these 17 files.

2. **`_publish_one` is called from exactly two places**: `publish_due` (line 376) and `publish_post` (line 407) — both in `post/run.py`. No other file in the 17 calls `_publish_one` directly (confirmed via `call_graph.json`: `fanops.post.run._publish_one` → `called_by_in_repo: ["fanops.post.run.publish_due", "fanops.post.run.publish_post"]`).

3. **`publish_due` is called from exactly one place** in the entire pipeline: `pipeline._publish_safe` (line 281), which itself is called only from `pipeline.advance` (line 444), which runs AFTER the main ledger transaction commits — i.e., after every state transition a pass can make to a post has already landed, including any operator `approve_post` calls made via the Studio (which write directly to the ledger through `ledger.py`, not through `pipeline.py`). `publish_post` is the Studio's synchronous "Publish now" button handler (confirmed via call_graph: called from `studio.actions_review`/`studio.actions_schedule`), scoped to exactly one post id supplied by the operator's click — it still runs the identical `_publish_one` CLAIM check.

4. **The CLAIM check is unconditional and cannot be short-circuited**: `post/run.py:228-233`
   ```python
   with Ledger.transaction(cfg) as led:
       post = led.posts.get(post_id)
       if post is None or post.state is not PostState.queued:
           return None                                # lost the race / not eligible — no-op (F11)
       ...
       post.state = PostState.submitting
   ```
   This runs under the ledger's flock (mutual exclusion), so no concurrent writer (Studio approve/reject, another `fanops run`/daemon tick) can race it — the read-check-flip is atomic within the transaction. There is no parameter, flag, or code path in `_publish_one`, `publish_due`, or `publish_post` that skips this block.

5. **No post is ever born `queued`.** Traced the ONLY `Post(...)` construction in the whole cluster: `crosspost.py:269` inside `_mint_surface_post`, which hard-codes `state=PostState.awaiting_approval`. There is no second Post-construction site anywhere in these 17 files (or in `ledger.py`'s `approve_post`/`reject_post`/`unapprove_post`, which only ever `model_copy` an EXISTING post — they never construct one). Therefore the ONLY way a post ever reaches `queued` is `Ledger.approve_post` (`ledger.py:503-519`), which is itself gated on `p.state is not PostState.awaiting_approval: return` — i.e., promotion is idempotent and one-directional per approval, and only reachable from an operator action (Studio approve endpoint) or an explicit CLI/script call to `Ledger.approve_post`. No file in this 17-file cluster calls `approve_post`.

6. **The dryrun boundary cannot be bypassed to reach a real poster.** `post/run.py:_post_provider` (line 113-122) returns `"dryrun"` unconditionally whenever `not cfg.is_live` — this is checked BEFORE any claim/network activity, in both `publish_due` (line 356) and `publish_post` (line 394). A dryrun-routed post never calls `_publish_one` at all — it calls `write_preview` and `continue`/`return`, staying `queued` forever until a real publish pass runs (post-live-flip). Additionally, `post/__init__.py:get_poster` (line 13-29) has its own independent hard refusal: `if cfg.is_live and (resolved or "").lower() == "dryrun": raise RuntimeError(...)` — a defense-in-depth check that fires even if some future caller tried to construct a `DryRunPoster` on a live system directly.

7. **The `submitted → published` transition has its own gate** (R1/D2, `post/run.py:258-266`): a backend returning `submitted` without a confirmed `public_url` does NOT promote to `published` — it parks `needs_reconcile` instead. This means "the network call succeeded" alone is insufficient for the terminal published state; a real permalink must be observed.

8. **needs_reconcile is never silently re-queued to a publishable state within these 17 files.** No function in this cluster transitions `needs_reconcile → queued`. (Reconciliation and any re-queue decision live in `reconcile.py`, outside this cluster's scope per the task's file list — noted as an information boundary, not a defect.)

9. **`autopilot.py` and `agentstep.py`/`responder.py` never touch `PostState`.** Confirmed by grep: neither file references `PostState` or `Ledger.posts` at all. `autopilot()`'s docstring is explicit: "it never publishes and never edits the ledger."

10. **`postiz_lifecycle.ensure_up`** only starts a local Docker container — it has no ledger access and cannot alone cause a publish; it's called from inside `publish_due`/`publish_post` AFTER the due/claim gates already ran their filtering, purely to make sure the backend is reachable before the (already-decided) network call.

**Conclusion**: `_publish_one`'s `post.state is PostState.queued` check is confirmed as the sole structural gate a post must clear to reach a real network publish call, and `Ledger.approve_post` is confirmed as the sole path that can ever produce a `queued` post from a freshly-minted `awaiting_approval` one. No ungated publish path exists in these 17 files.

## Anomalies found

1. **`post/compress.py:persist_post_shrink` (lines 114-131) — CORRECTED ON VALIDATION: NOT dead.** The first pass called this genuinely unreferenced because `call_graph.json` showed no caller — but the call graph is name-based and cannot see lazy in-function imports. `persist_post_shrink` IS called, via `from fanops.post.compress import persist_post_shrink` at `studio/actions.py:395` followed by `persist_post_shrink(cfg, led, post_id)` at `:396`. It is a live shrink-then-persist helper reached from the Studio actions layer (C9), not dead code. (The superseded-by-`apply_shrink_to_post` reasoning was wrong.)

2. **False-positive "unreferenced" flags from the static call-graph tool** (`.reports/unreferenced_candidates.json`), all actually reachable via a lazy-import dict-of-lambdas indirection the AST-based tool can't trace through:
   - `post/providers.py:_postiz_poster`, `_zernio_poster`, `_dryrun_poster`, `_postiz_uploader`, `_zernio_uploader` (lines 19-24) — all reachable via `PROVIDERS[name].make_poster(cfg)` / `.make_uploader(cfg)` in `post/__init__.py:get_poster`/`get_media_uploader`.
   - `post/postiz.py:postiz_upload_media` (line 134) — reachable via `PROVIDERS["postiz"].make_uploader` → `_postiz_uploader(cfg)` → returns this function.
   - `post/zernio.py:zernio_upload_media` (line 122) — same pattern via `PROVIDERS["zernio"].make_uploader`.
   - `post/run.py:reset_publish_throttle` (line 86) — its own docstring says "Test-only"; almost certainly called from `tests/`, which the call graph doesn't index. Not a defect.

   None of these are real dead code; they're a known blind spot of static call-graph extraction against factory-dict/lazy-import indirection. Flagging so a future automated dead-code sweep doesn't remove them.

3. **`_JITTER_MAX < _STEP_MIN` monotonicity invariant is enforced only by a code comment, not an assertion.** `crosspost.py:28-29` — the comment says "AUDIT H1/H2: `_JITTER_MAX` MUST stay strictly less than `_STEP_MIN`... do not raise past it without breaking monotonicity," but there is no runtime `assert _JITTER_MAX < _STEP_MIN` guarding the two module constants. A future edit changing either constant without re-reading the comment would silently break the monotonic-scheduling guarantee `surface_time` depends on. Low risk (both are hardcoded literals, not operator-configurable), but a one-line module-level assert would make the invariant self-enforcing instead of comment-enforced.

4. **No bare `except:` and no `except Exception: pass` with zero trace anywhere in these 17 files.** Every broad `except Exception` in the cluster either logs (via `get_logger`) before continuing, sets a typed `error_reason`/`degraded_reason`, or is a documented best-effort decoration (e.g., `postiz_lifecycle.py:68` writes to `sys.stderr` on `ensure_up` failure; `post/postiz.py:postiz_health_probe`'s `except Exception as exc:` at line 218 logs via `_log.warning` before constructing the unhealthy result). This matches the C2 trace's finding that the codebase is disciplined about typed, breadcrumbed failure over silent swallowing.

5. **No TODO/FIXME/XXX comments** found in any of the 17 files (grepped).

6. **`post/dryrun.py:DryRunPoster.publish` is effectively dead in the current call graph** but intentionally retained. Its own docstring states: "this path is exercised only by a caller that still routes a dryrun post through a Poster contract" — post-M1, `publish_due`/`publish_post` both call `write_preview` directly at the dryrun-boundary check and never construct/invoke a `DryRunPoster`. The class is kept alive as the `Poster`-protocol fallback inside `get_poster` (so a hypothetical direct caller doesn't crash), and as the target of `post/__init__.py`'s defensive live-refusal check comment ("A LIVE system asking for the dryrun poster is the bug..."). Not a defect — a deliberately-retained defensive fallback, but worth naming since a naive dead-code sweep would flag `DryRunPoster.publish` as unreachable in the normal flow.

7. **`_postiz_permalink` (`post/postiz.py:73-86`) always returns `None`.** This is explicitly documented as correct-by-timing (the publish-time 2xx response has no permalink to give; the real one arrives later via `PostizStatusClient` during reconcile) rather than a bug, but it means `post.public_url` is never set by a successful Postiz publish call itself — the `submitted → published` promotion in `_publish_one` (gated on `public_url` presence) can therefore NEVER fire for a fresh Postiz publish inside `_publish_one` alone; it necessarily waits for `reconcile.py` (outside this cluster) to backfill the URL on a subsequent pass. This is a real, if intentional, two-phase-commit-style dependency worth flagging: a Postiz post's `published` state is structurally unreachable without a working reconcile pass, even though `_publish_one`'s code reads as if it could reach `published` directly.

8. **Module-level mutable state**: `post/run.py:_publish_throttle_last` (a plain `dict`) is the one piece of true global mutable state found in this cluster, explicitly documented as "in-process only — the daemon is single-process," with a `reset_publish_throttle()` test-only clear function. Not a defect for the current single-process daemon architecture, but would need revisiting if `fanops` were ever run as multiple concurrent processes against the same Postiz account (the per-`(provider, account_id)` rate limit would not be shared across processes).
