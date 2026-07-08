# C1: Core Data Model & Persistence

## Files covered (all 10 read in full)

| File | Lines | Read fully |
|---|---|---|
| `src/fanops/models.py` | 656 | Yes |
| `src/fanops/ledger.py` | 699 | Yes |
| `src/fanops/ledger_wipe.py` | 224 | Yes |
| `src/fanops/ids.py` | 26 | Yes |
| `src/fanops/config.py` | 962 | Yes |
| `src/fanops/controlio.py` | 40 | Yes |
| `src/fanops/control.py` | 55 | Yes |
| `src/fanops/errors.py` | 112 | Yes |
| `src/fanops/log.py` | 30 | Yes |
| `src/fanops/stage_lock.py` | 73 | Yes |

Total: 2,877 lines, 10/10 files, zero omissions. Cross-checked against `.reports/structural_index.json` and `.reports/call_graph.json`. The `fcntl.flock`-based locking claim was independently spot-verified against source (`grep -n "flock" ledger.py stage_lock.py`) — confirmed accurate.

## Data model (entities/fields/relationships)

All units live in `models.py` as Pydantic `BaseModel`s. Lineage chain: **Source → Moment → Clip → Post**, with **Render** as a per-account child of Clip, plus several auxiliary entities. Pydantic v2 default `extra="ignore"` is deliberately relied on everywhere (never `extra="forbid"`) so an older binary loading a newer-schema ledger silently drops unknown fields instead of crashing — pinned by `tests/test_models_extra_ignore.py`.

### `Source` (models.py:127-152)
Root ingest unit, one per ingested video file.
- `id: str`, `state: SourceState = catalogued`, `source_path: str`
- `source_origin: str = "drop"` (drop|url|scan — intake channel)
- `origin_kind: Literal["native","third_party"] = "native"` (write-once)
- `batch_id: Optional[str]` (Account-First Studio grouping, write-once)
- `sha256`, `duration`, `width`, `height`, `language`
- `transcript: Optional[list[dict]]` (None=not transcribed, []=ran no speech)
- `signal_peaks: Optional[list[dict]]`
- `error_reason`, `degraded_reason` (RF1 visible-degradation channel)
- `meta: dict`, `created_at: Optional[str]` (ISO-8601 UTC ingest day)

### `Moment` (models.py:154-194)
A candidate window within a Source. `parent_id` → Source.id.
- `id`, `parent_id`, `state: MomentState = decided`, `content_token: str = ""`
- `start: float`, `end: float`, `reason: str` (required)
- `transcript_excerpt`, `hook: Optional[str]`, `hook_removed: Optional[str]` (stripped-hook audit trail)
- `signal_score: float = 0.0`
- `hook_strategy: Optional[str]` (router annotation: text|clean_final|clean_awaiting_strategy:<key>|stitch:<format>)
- `intro_matches: Optional[list[dict]]` (M6 intro-tease pairings)
- `affinities: list[str]` — **the single-owner crosspost gate input** (stamped at pick `moments.py:340`; `[]` = persona-blind fan-to-all; operator-mutable via `cast_add`/`cast_remove`)
- `hook_frames_unread: bool = False` (AGENT-9 responder-stamped, not model-authored)
- `error_reason`

### `Clip` (models.py:196-215)
A rendered video file. `parent_id` → Moment.id.
- `id`, `parent_id`, `state: ClipState = rendered`, `path: str`, `aspect: Fmt = r9x16`
- `first_frame_kind: Optional[str]` ("visual"|"transcript" provenance)
- `cut_seconds: Optional[float]` (observational, not varied)
- `held: bool = False`, `held_reason`
- `tagged_artist: bool = False`
- `media_url: Optional[str]` (cached hosted URL — FIX F44)
- `meta_captions: dict` (surface → {caption, hashtags})
- `error_reason`, `hook_burn_failed: bool = False`

### `Post` (models.py:217-304)
One per (clip, account, platform) posting surface. `parent_id` → Clip.id.
- `id`, `parent_id`, `state: PostState = awaiting_approval` (RF1: born unapproved)
- `account: str`, `account_id: str`, `platform: Platform`
- `caption: str`, `hashtags: list[str]`, `media_urls: list[str]`, `aspect: Fmt`
- `scheduled_time: Optional[str]`, `submission_id: Optional[str]` (fanops_ prefix = idempotency token, not real backend id — see `is_real_submission_id`)
- `public_url: Optional[str]`, `media_id: Optional[str]` (IG Graph media id), `product_type: Optional[str]`
- `error_reason`, `metrics: dict` (latest snapshot), `metrics_series: list[dict]` (append-only time series, P3)
- `render_id: Optional[str]` → Render.id
- `variant_key`, `variant_hook` (read-only mirror of Render.hook_text)
- P1 attribution: `first_frame_kind`, `clip_profile`, `cut_seconds`, `variation_axis`
- Leg 3 attribution: `top_bias: Optional[bool]`, `publish_hour: Optional[int]`, `publish_dow: Optional[int]`
- `batch_id: Optional[str]` (denormalized), `created_at`, `published_at`
- **`@model_validator(mode="after") _enforce_published_url_invariant`** (R1 invariant): `published`/`analyzed`/`retired` states MUST carry a non-empty `public_url`, else raises `ValueError` at construction. `_POST_TERMINAL_REQUIRES_URL = frozenset({published, analyzed, retired})` module constant backs this.

### `Render` (models.py:325-358)
Per-account shippable artifact, content-addressed child of Clip. `clip_id` → Clip.id.
- `id` (child_id of clip+hook+band+framing), `clip_id`, `account: str`, `surface_key: str`
- `hook_text: Optional[str]` (single source of truth for burned hook)
- `path: str`, `media_url: Optional[str]`
- `state: RenderState = rendered`
- `batch_id`, `source_id` (denormalized lineage)
- `is_account_cut: bool = False` (real per-account length cut vs. hook burned onto shared band)
- `hook_source: HookSource = none` (per_account|shared_fallback|none)
- `cut_seconds: Optional[float]`

### `SelectionFact` / `AccountSelection` — **REMOVED v11 (P12/MOL-154)**

> Frozen models deleted from `models.py`. Legacy `account_selections` / `selection_facts` ledger maps are
> dropped on load via `_migrate_v11_drop_selection_maps` (`ledger.py:179`). Crosspost routing now reads
> `Moment.affinities` only (`casting.affinity_admits`). v8→v9 lift migration (`_migrate_v8_account_selections`)
> remains for old ledgers upgrading through v9 but the maps do not survive v11.

*(Pre-v11 field inventory retained in git history.)*

### `StitchPlan` (models.py:459-472)
Operator-approval spine for structural-hook formats (impact-cut, intro-tease). `clip_id` → Clip.id.
- `id` (content-addressed), `clip_id`, `strategy_key: str`
- `asset_ids: list[str]`, `plan_params: dict`
- `state: StitchState = suggested`
- `base_fingerprint: Optional[str]` (pinned at approval; stale → dismiss)
- `error_reason`, `rank_score: Optional[float]`, `rationale: Optional[str]`
- `render_attempts: int = 0` (caps flaky in-lock commit retries; parks to error at cap)

### `Batch` (models.py:487-495)
Named, account-targeted ingest group.
- `id` (content-addressed), `name: str` (required non-blank)
- `target_accounts: list[str]` ([] = all-active sentinel)
- `state: BatchState = open`, `created_at`, `error_reason`
- `burn_subs: Optional[bool]` (per-batch override of global cfg.burn_subs)

### `ImportedMedia` (models.py:505-528)
A live IG post probed from the platform with NO clip lineage (ledger-rebuild "Instagram is the source of truth"). Keyed by the platform's own `media_id` (natural key, NOT content-addressed — no parent to hash off).
- `media_id: str` (the key), `permalink`, `product_type`, `timestamp`, `caption`, `account`
- `metrics: dict`, `metrics_series: list[dict]` (mirrors Post's two fields)
- `error_reason`, `imported_at`

### Agent-step request/response contracts (models.py:531-657)
Not persisted units — LLM I/O DTOs, all carry `request_id` for correlation:
`MomentRequest`, `MomentPick` (with `@field_validator` `_finite` rejecting NaN/Infinity timestamps), `MomentDecision`, `MomentHookRequest`, `MomentHookDecision`, `CaptionRequest`, `CaptionItem`, `CaptionSet`, `IntroMatchItem`, `IntroMatchDecision`. (`MomentCastingRequest`/`MomentCastingDecision` removed P11.)

### Module-level constants
- `LIFT_SCORE = "lift_score"` — the single canonical Post.metrics key every scorer ranks by.
- `PLATFORM_ASPECT` — Platform → Fmt mapping (all 9:16 except facebook 1:1, twitter 16:9).
- `PLATFORM_MAX_SECONDS` — per-platform hard duration cap (instagram 90, tiktok 600, youtube 180, twitter 140, facebook 90), fail-open on unknown duration (never silently drops a post).

### Relationships summary
```
Source 1──* Moment 1──* Clip 1──* Post
                          Clip 1──* Render (per-account child)
Clip    1──* StitchPlan
Batch   1──* Source, Post (denormalized batch_id)
ImportedMedia — standalone, no lineage
```
(Moment.affinities is the crosspost gate input — no separate AccountSelection table post-v11.)

## Per-file breakdown

### `models.py` — purpose
Defines every persisted unit (Source→Moment→Clip→Post + Render/StitchPlan/Batch/ImportedMedia), all state enums, and every LLM agent-step request/response contract. Pure data + validators; no I/O.

**Enums** (10 total): `SourceState`, `MomentState`, `ClipState`, `RenderState`, `PostState`, `Platform`, `Fmt`, `HookSource`, `StitchState`, `BatchState`. (`SelectionMethod` removed v11 with `AccountSelection`.)

**Functions:**
- `is_real_submission_id(sid) -> bool` — returns False if `sid` is falsy or starts with `"fanops_"`. Called by `track.pull_metrics` / status logic.
- `normalize_account_handle(handle) -> str` — strips whitespace and leading `@`. Pure. Called by ledger migration/dedup helpers.
- `stitch_plan_id(clip_id, asset_ids, strategy_key, plan_params) -> str` — content-addressed id. Pure.
- `batch_id(name, created_at) -> str` — `content_id("batch", name, created_at)`. Pure.

**Model methods (validators):**
- `Post._enforce_published_url_invariant` (models.py:279-299) — raises `ValueError` if state ∈ terminal-positive set and `public_url` empty.
- `MomentPick._finite` (models.py:557-562) — raises `ValueError` on non-finite float.

Callers (from call_graph.json / grep): every pipeline module (`ledger.py`, `crosspost.py`, `casting.py`, `moments.py`, `clip.py`, `caption.py`, `intro_match.py`, `stitch_render.py`) plus the entire Studio surface imports these models directly. This is the most widely-imported module in the repo.

### `ledger.py` — purpose
Single source of truth persistence layer: one JSON document holding id→unit maps, atomic file-lock-protected writes, and the schema-migration chain. Owns all mutation primitives (typed state setters, cascade-delete, reconcile-upsert).

**Migration/versioning:**
- `_migrate_v3_created_at(raw) -> dict` (ledger.py:25-55) — v2→v3 pure-dict transform, backfills `created_at` on Source/Post rows. Never raises.
- `_migrate_v4_metrics_series(raw) -> dict` (ledger.py:58-77) — v3→v4, back-fills one `"legacy"`-tagged metrics_series row. Never raises.
- `_migrate_v8_account_selections(raw) -> dict` (ledger.py:148-175) — v8→v9 hop (lifts legacy affinities into transient `account_selections`; dropped again at v11).
- `_migrate_v10_drop_selections(raw) -> dict` (ledger.py:178-183) — v10→v11, drops `account_selections` + `selection_facts`.
- `_migrate(raw, from_version) -> dict` (ledger.py:209-221) — hop-chains through `_MIGRATIONS` dict; raises `ControlFileError` on a chain gap.
- `_MIGRATIONS` dict (ledger.py:182-191) — version N ← transform table, versions 1-10.
- `SCHEMA_VERSION = 11` (ledger.py) — v11 drops retired selection maps (P12/MOL-154).
- `_SID_RE` (ledger.py:196) — regex `^src_[0-9a-f]{12}$`.

**Locking:**
- `_file_lock(lock_path, timeout=None)` (ledger.py:224-256) — `@contextmanager`; `fcntl.flock(fd, LOCK_EX|LOCK_NB)` in a poll loop, bounded by `timeout` (default `_DEFAULT_LOCK_TIMEOUT=30.0`). Raises typed `LockBusyError` on timeout. Kernel releases lock on process death (self-healing).

**Errors:**
- `_NewerSchema(ControlFileError)` (ledger.py:199-206) — raised when on-disk schema is newer than this binary.
- `_fallback_iso(suggested_iso, now_iso) -> str` (ledger.py:259-272) — pure time helper for `approve_post`'s fallback logic.

**`Ledger` class:**
- `__init__(cfg)` (ledger.py:276-307) — initializes empty dict maps.
- `Ledger.load(cfg) -> Ledger` (classmethod, ledger.py:309-350) — reads, checks schema_version, migrates, dedupes, constructs models. Wraps any exception as `ControlFileError`. Called by 60+ sites.
- `Ledger.transaction(cfg, timeout=None)` (classmethod contextmanager, ledger.py:352-370) — holds `_file_lock` across load-mutate-save. Called by ~40 sites.
- `_save_unlocked()` (ledger.py:372-405) — writes whole ledger dict to `.json.tmp`, chmod 0o600 best-effort, `os.replace` atomic.
- `save()` (ledger.py:407-414) — standalone save, acquires lock itself.
- `Ledger.snapshot(cfg, now=None) -> Path` (classmethod, ledger.py:421-434) — timestamped byte-copy under lock.
- `Ledger.restore_snapshot(cfg, snapshot_path)` (classmethod, ledger.py:436-447) — atomic restore under lock.
- Idempotent adds (ledger.py:485-492): `add_source`, `add_moment`, `add_clip`, `add_post`, `add_render`, `get_render`, `add_imported_media`, `get_imported_media`. (`add_selection_fact`/`add_account_selection` and their query helpers **removed v11** — crosspost reads `Moment.affinities` only.)
- Typed state setters (ledger.py:497-500): `set_source_state`, `set_moment_state`, `set_clip_state`, `set_post_state` — immutable `model_copy`.
- `approve_post(uid, *, now_iso, suggested_iso=None)` (ledger.py:503-519) — the human-approval gate.
- `reject_post(uid)` (ledger.py:520-523) — no-op unless awaiting_approval.
- `unapprove_post(uid)` (ledger.py:524-527) — no-op unless queued.
- Queries (ledger.py:531-547): `already_seen`, `sources_in_state`, `clips_in_state`, `posts_in_state`, `moments_of`, `clips_of`, `posts_of`, `posts_of_account` — O(n) scans. (`selection_facts_of_*` query helpers **removed v11**.)
- `reconcile_moments(source_id, keep)` (ledger.py:555-576) — upsert+cascade-delete core.
- `_delete_moment_cascade(moment_id)` (ledger.py:614-636) — cascade delete/retire logic.
- `retire_clip`, `is_retired_clip`, `is_retired_moment` (ledger.py:639-647).
- `retire_source(source_id)` (ledger.py:650-657) — cascades via empty-keep reconcile; leaves file on disk deliberately.
- `is_retired_source(source_id)` (ledger.py:658-660).
- `rebuild_catalog(cfg)` (ledger.py:662-679) — reconciles disk vs ledger, orphan files become `discovered` sources.
- `add_stitch_plan`, `approve_stitch_plan`, `dismiss_stitch_plan` (ledger.py:682-691) — guarded state-check no-ops.
- `add_batch`, `get_batch`, `batches_for_account` (ledger.py:694-699).

Class attributes: `_LIVE_CLIP_STATES`, `_LIVE_POST_STATES`, `_PROTECTED_POST_STATES` (ledger.py:601-612).

### `ledger_wipe.py` — purpose
The "fall-away" (M4 wipe) — removes ledger rows whose entire descendant closure carries no kept post. Snapshot-first, code-enforced gates.

- `SnapshotRequired`, `WipeNotConfirmed` (ledger_wipe.py:46-51) — typed refusal errors.
- `WipePlan` (dataclass, ledger_wipe.py:54-68) — would-remove id-sets.
- `_is_kept_post(post) -> bool` (ledger_wipe.py:71-77) — kept iff analyzed OR has metrics. Pure.
- `compute_wipe_set(led) -> WipePlan` (ledger_wipe.py:80-160) — the core pure function computing removal sets transitively.
- `wipe_preview(led) -> dict` (ledger_wipe.py:163-173) — read-only summary for Studio pre-confirm.
- `snapshot_is_restorable(snapshot_path) -> bool` (ledger_wipe.py:176-189) — verifies parseable JSON with "posts" key. Fail-closed False.
- `execute_wipe(cfg, *, confirmed, snapshot_path) -> dict` (ledger_wipe.py:192-224) — raises on unconfirmed/unsnapshotted; else transaction-scoped removal.

### `ids.py` — purpose
Deterministic, content-addressed id generation (never `hash()`).

- `_hash(*parts) -> str` (ids.py:7-8) — sha1-based. Private.
- `make_id(kind, source) -> str` (ids.py:10-12) — Called by `ingest._catalogue_file`.
- `child_id(kind, parent_id, content_token) -> str` (ids.py:14-17) — ~10 call sites.
- `content_id(kind, parent_id, content_token) -> str` (ids.py:19-21) — alias for child_id.
- `surface_key(account, platform) -> str` (ids.py:23-26). All 5 functions pure.

### `config.py` — purpose
Filesystem layout + entire environment-variable contract. Never stores secrets in code — reads `.env` via `load_dotenv`.

- `_sanitize_tuning(raw) -> dict` (config.py:17-42) — drops invalid tuning.json entries with per-drop warning.
- `_STAGE` dict, `PosterBackend`, `_VALID_BACKENDS`, `_LIVE_BACKENDS`, `FRAMING_NAMES`, `_BACKEND_PLATFORMS`, `_GATE_MODEL_DEFAULTS`, `_ASR_SHORT_SOURCE_SECONDS` — module constants.
- `Config.__init__(root=None)` (config.py:91-114) — sets root, loads .env, builds ~18 stage-dir/control-file path attributes.
- `render_path(...)` — the one method with disk side effect (mkdir).
- `tuning()` — reads+parses tuning.json, fail-open to `{}`.
- ~55 properties covering every env var (see full enumeration table below) — each documented individually in the full trace with default/validation logic.
- `account_window(handle)` — reads accounts.json directly, bypassing Config-level caching.

### `controlio.py` — purpose
Shared atomic read/write primitives for hand-editable multi-writer JSON files.

- `write_json_atomic(p, raw)` (controlio.py:16-29) — mkstemp + write + os.replace; re-raises on failure after cleanup attempt. ~15 call sites across accounts.py/persona_store.py/cutover.py/learn_doctor.py.
- `load_raw_list(p, key) -> tuple[dict, list]` (controlio.py:32-40) — raises `ControlFileError` on wrong shape.

### `control.py` — purpose
The single validated reader for `context.md` (brand brief).

- `load_guidance(cfg) -> str` (control.py:20-45) — fail-open but LOUD (warns on missing/unreadable/empty/oversize); truncates oversize on UTF-8 boundary.
- `guidance_sha(cfg) -> str` (control.py:47-55) — fingerprint or "absent".
- `_MAX_GUIDANCE_BYTES = 32_768`.

### `errors.py` — purpose
Typed exceptions + secret-redaction/error-summarization helpers.

Classes: `ControlFileError`, `LockBusyError`, `StageBusyError`, `AuthError` (base), `PostizAuthError`, `ZernioAuthError`, `ToolchainMissingError`, `DownloadError`, `CutoverError`, `MetaInsightsScopeError`.
- `redact(text, *secrets, limit=200) -> str` (errors.py:90-99) — pure.
- `reason(exc) -> str` (errors.py:102-112) — condenses pydantic ValidationError. Called by `ledger.Ledger.load`/`restore_snapshot`.

### `log.py` — purpose
Minimal structured run-logger to `07_reports/run.log`.

- `get_logger(cfg) -> Callable` (log.py:10-30) — best-effort file creation at mode 0600; returns closure `log(stage, unit_id, outcome, **fields)`.
  - `_san(v)` nested — collapses control chars (injection-hardening).
  - `log(...)` nested — TAB-delimited append, atomic-at-EOF write.

60+ call sites spanning nearly every pipeline stage module.

### `stage_lock.py` — purpose
Per-stage producer lock (mutex) keyed by `(stage, source_id)`.

- `_lock_path_for(cfg, *, stage, key) -> Path` (stage_lock.py:36-39) — pure path resolver.
- `stage_lock(cfg, *, stage, key, timeout=None)` (@contextmanager, stage_lock.py:42-73) — same flock+poll pattern as ledger, default 7200s timeout, `StageBusyError` on contention.

## Cross-cutting: locking/atomicity/persistence contract

**File format:** one JSON document at `00_control/ledger.json` with `{"schema_version": int, "sources": {}, "moments": {}, "clips": {}, "posts": {}, "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {}, "renders": {}, "imported_media": {}}`. Legacy `account_selections`/`selection_facts` keys are dropped on load at v11 (`_migrate_v10_drop_selections`).

**Locking strategy — confirmed `fcntl.flock`-based, exactly per CLAUDE.md:**
- `ledger._file_lock` (ledger.py:224-256): `fcntl.flock(fd, LOCK_EX|LOCK_NB)` poll loop, 30s default timeout, `LockBusyError` on timeout. Kernel releases lock on process death — self-healing, unlike an `O_EXCL` sentinel file.
- `stage_lock.stage_lock` mirrors this exactly with `StageBusyError` and 7200s default timeout.

**Atomicity guarantee:** every write follows temp-file + `os.replace`:
- `Ledger._save_unlocked` — `.json.tmp` + chmod 0600 best-effort + `os.replace`.
- `Ledger.snapshot`/`restore_snapshot` — `shutil.copy2` under lock / tmp+replace.
- `controlio.write_json_atomic` — `tempfile.mkstemp` with a UNIQUE temp name (multi-writer safe, unlike ledger's fixed `.tmp` suffix which relies on single-writer-under-flock).

**Load/save contract:**
1. `Ledger.load(cfg)` — no lock held (only `transaction()` holds across load+mutate+save).
2. `Ledger.transaction(cfg)` — the correct read-modify-write pattern, closes the "AUDIT B4" lost-update race.
3. `save()` — standalone, must never be called from inside an active `transaction()`.

**Migration is additive and non-destructive** — every registered step injects new maps or performs pure, never-raising backfills. The ledger is never wiped by ordinary operation; the only wipe path is the separate, gated `ledger_wipe.execute_wipe`.

## State machine (Post/Clip/Moment lifecycle, with evidence)

### SourceState
```
catalogued → transcribed → signalled → moments_requested → picks_decided → moments_decided
                                                                          ↘ moments_empty (non-terminal)
retired (Ledger.retire_source) | discovered (Ledger.rebuild_catalog) | error
```

### MomentState
```
picked (birth, NOT renderable) → decided → clipped
retired (via _delete_moment_cascade — never resurrected per reconcile_moments's retired-skip check)
error
```
Evidence: `Ledger.reconcile_moments` (ledger.py:566-574) explicitly refuses to overwrite a `retired` prior — "AUDIT M1: never resurrect a retired moment."

### ClipState
```
rendered → captions_requested → captioned → queued → published → analyzed
                                           ↘ held
stitch_draft (structurally unpostable until operator promotes to captioned)
retired | error
```

### RenderState
```
rendered (born here; documented "RESERVE, not wire" — nothing advances a Render past rendered)
queued, published, analyzed, retired — members KEPT for future wiring, currently inert
```

### PostState — the richest state machine
```
awaiting_approval (BORN here — RF1 no-auto-publish invariant)
    → queued (Ledger.approve_post — the human gate)
    → rejected (Ledger.reject_post — terminal discard)
queued
    → submitting → submitted → published → analyzed (requires public_url per R1 validator)
    → awaiting_approval (Ledger.unapprove_post)
    → failed (re-queueable)
    → needs_reconcile (ambiguous 5xx/timeout — never blindly re-POSTed)
    → retired (superseded by an approved M4 stitch — publish_due only iterates queued)
error
```
Evidence for the terminal-URL invariant: `Post._enforce_published_url_invariant` (models.py:279-299) raises `ValueError` at CONSTRUCTION TIME if state ∈ {published, analyzed, retired} and public_url is empty — closes every door at the type level, not just at a call site.

`approve_post`/`reject_post`/`unapprove_post` are all guarded no-ops on the wrong current state (safe under the transaction lock).

### StitchState
```
suggested → approved → in_use
suggested|approved → dismissed (terminal)
error
```

### BatchState
```
open (this codebase version only ever sets open) — closed/error exist, no writer found
```

### AccountSelection.method sum-type — **REMOVED v11** (historical; was a validated discriminator on the deleted `AccountSelection` model)

## config.py: env vars and control-file fields (full enumeration)

~55 environment variables covering: publish backend selection (`FANOPS_POSTER`, `FANOPS_LIVE`), Postiz/Zernio/Meta Graph credentials, hashtag trends, LLM responder mode + model tiering, clip profile/framing/visual-start/smart-framing toggles, ASR model/language/vocal-isolation, subtitle burning, account-casting toggle, structural-hooks flags (hook_router/impact_cut/intro_tease), the full variant-learning family (v2 best-hooks, v3 amplify+UCB1+transfer, all with min-posts/min-gap/min-streak thresholds), P4 dim-bias/timing-bias (validation-frozen; `casting_bias`/`FANOPS_CASTING_BIAS` removed P11), GC retention, upload size cap, operator timezone, realistic cadence, publish lead time, Zernio upload cap, Postiz rate throttle, concurrent-sources toggle+worker count.

Two control-file fields read directly by config.py: `tuning.json` (offbrand regex overrides + lift_weights) and `accounts.json` (`account_window` reads `daily_window` per handle). Every other control file's path is defined here but read by other modules.

## Anomalies found

1. **No TODO/FIXME/XXX comments** anywhere in the 10 files (grep confirmed zero matches).
2. **No bare `except:`** anywhere in the 10 files (grep confirmed zero matches).
3. **`log.py:15`** — `except OSError: pass` in `get_logger`'s best-effort file-creation/chmod. Documented intent, inconsequential (logging still proceeds via subsequent `open(..., "a")`).
4. **`ledger.py:404`** — `except OSError: pass` wrapping `os.chmod(tmp, 0o600)` in `_save_unlocked`. Documented intent; the atomic `os.replace` that follows is unaffected.
5. **`ledger_wipe.py:188`** — `except Exception: return False` in `snapshot_is_restorable`. Broad but correct fail-closed behavior for a destructive-wipe gate; swallows the specific error reason with no logging — a diagnosability gap, not a correctness bug.
6. **Stale docstring in `ledger.py` module header (line 2)**: `"one JSON doc, four id->unit maps"` — inaccurate; the schema has grown to 10+ maps since v1. Documentation drift, not a functional defect.
7. **`RenderState` enum members `queued`/`published`/`analyzed`/`retired`** (models.py:42-52) — explicitly self-documented as unreachable dead code by design ("RESERVE, not wire"). Deliberate reservation.
8. **`BatchState.closed`/`BatchState.error`** (models.py:484-485) — same reservation pattern, no writer found.
9. **`Ledger.posts_of_account`** docstring notes a REMOVED method (`renders_of_account`) — positive example of dead code correctly identified and removed after audit, not left to rot.

No other anomalies found in this cluster after full-file reads of all 10 files.
