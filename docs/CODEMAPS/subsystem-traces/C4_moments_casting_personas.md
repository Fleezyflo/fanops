# C4: Moments, Casting & Personas

> **POST-REBUILD (P15 / MOL-156).** Single-owner per-persona picking is live. One `moments` gate attributes
> picks per owner (`Moment.affinities` len==1). One `moment_hooks` gate authors `m.hook` for the owner only.
> Captions + crosspost scope via `affinity_admits` (owner × platform). The LLM casting stage, durable
> selection table, `hooks_by_persona`, `scoped_caption_surfaces`, and `casting_bias` are **gone**. Operator
> `cast_add`/`cast_remove` writes `Moment.affinities` directly. Proofs: `tests/test_per_persona_e2e.py`,
> `tests/test_archetype_differentiation.py`, `tests/test_no_ghosts.py`.

## Files covered (9 modules — `casting_bias.py` removed P11)

1. `src/fanops/moments.py` (486 lines) — read
2. `src/fanops/casting.py` (22 lines) — read — **post-P11: `affinity_admits` only**
3. `src/fanops/personas.py` (102 lines) — read
4. `src/fanops/persona_directives.py` (314 lines) — read
5. `src/fanops/persona_levers.py` (217 lines) — read
6. `src/fanops/persona_research.py` (60 lines) — read
7. `src/fanops/persona_store.py` (228 lines) — read
8. `src/fanops/accounts.py` (629 lines) — read
9. `src/fanops/batches.py` (56 lines) — read

Cross-checked against `.reports/structural_index.json` and `.reports/call_graph.json`. Note: there are look-alike files elsewhere (`src/fanops/studio/actions_casting.py`, `src/fanops/studio/app_routes_personas.py`, `src/fanops/studio/personas.py`) which are Studio-layer callers, NOT part of this cluster — excluded per the exact file list given.

## Persona/account data flow (definition → hydration → use) — single-owner lineage

```
personas.json (disk)
   │  Personas.load()
   ▼
Persona (BaseModel: id, name, voice, hashtag_corpus, intake,
         content_focus[], selection_scope, hook_angle)
   │
   │  accounts.py: Accounts.load() → _hydrate_from_personas(accts, cfg)
   │    - OVERWRITES in-memory: persona voice, hashtag_corpus, content_focus,
   │      selection_scope, hook_angle, derived clip_profile/framing
   ▼
Account (hydrated, in memory only)
   │
   ├─► moments.py PASS 1: _pick_personas → ONE moments gate, per-persona lenses
   │      → ingest stamps pick.personas[0] as Moment.affinities (single owner)
   │
   ├─► moments.py PASS 2: request_moment_hooks sends ONLY the owner persona
   │      → ingest writes m.hook (one hook per owner-moment)
   │
   └─► caption.py: owner × platform via pipeline._owner_caption_surfaces
          (affinity_admits — same gate as crosspost)
```

Legacy note: the pre-P11 LLM casting stage (casting LLM request/ingest gates, durable selection table,
`hooks_by_persona`, `scoped_caption_surfaces`) is removed. `casting.py` now holds `affinity_admits` only.
Operator `cast_add`/`cast_remove` (Studio) writes `Moment.affinities` directly.

## Post-P15 per-file snapshot (current — trust this over the archived breakdown below)

### `moments.py` — one source gate, owner-attributed picks, owner-only hooks

- `request_moments` — ONE `moments` gate per source; `_pick_personas` builds per-persona lenses
  (`selection_scope` + `content_focus` via `persona_directives`) in the payload. Sets `moments_requested`.
- `ingest_moments` — validates picks, `_drop_overlaps` is **within-owner** (cross-owner overlap is allowed),
  stamps `pick.personas[0]` → `Moment.affinities` (single owner), content-addressed via `_owned_moment_id`.
  No durable selection table, no casting-gate discard.
- `request_moment_hooks` — ONE gate per `picked` moment; `_hook_personas_for_moment` sends **only the owner**
  account (P6). Persona-blind moments (`affinities==[]`) get an empty personas list → shared hook path.
- `ingest_moment_hooks` — **atomic-per-source**: waits until every pick's hook gate has landed, then promotes
  all moments to `decided` in one deterministic pass. Writes `m.hook` only (no `hooks_by_persona`). Rejected
  hooks nulled with `hook_removed` preserved for operator restore.

**Design note (bounded-skip condemnation):** hook ingest defers via `if dec is None: return led` on the
whole source — atomic-per-source promotion, NOT independent per-persona gates and NOT a bounded-skip /
wait-cycle machine (`moments_wait_cycles` stays absent; `test_no_ghosts.py` guards).

### `casting.py` — the crosspost affinity gate (22 lines)

- `affinity_admits(cfg, moment, account)` — THE crosspost + caption-scope predicate. Casting OFF → admit all.
  `affinities==[]` → fan-to-all. Non-empty affinities → admit iff `account in affinities` (single-owner default:
  exactly one handle). No ledger read, no LLM stage, no `casting_bias`.

### `persona_directives.py` / `persona_levers.py` — archetype levers

- `selection_scope`, `content_focus`, `hook_angle` compile into pick/hook/caption prompts (`persona_directives`).
  `credibility_first` vs `controversy_seeking` diverge at the scope lens (`test_archetype_differentiation.py`).
  Operator catalog + crosswalk: `docs/LEVERS.md` (generated by `fanops lever docs`, MOL-162).

## Persona/account data flow (definition → hydration → use) — ARCHIVED pre-P15 diagram

The lever engine (`persona_levers.py`) is the single upstream declaration. `personas.py`'s `CONTENT_FOCUS`/`ENERGY_LEVELS`/`HOOK_ANGLES` (validation vocabularies), `persona_directives.py`'s `_FOCUS_CLAUSE`/`_ENERGY_CLAUSE`/`_ANGLE_CLAUSE`/`_FOCUS_PROFILE`/`_ENERGY_FRAMING` (compile clause maps), and `lever_catalog()` (operator-facing catalog) are all **projections** derived from `LEVER_REGISTRY` at import/call time — one edit to the registry propagates to validation, compilation, and the UI catalog simultaneously (no manual-parity risk).

## Per-file breakdown — ARCHIVED pre-P15 (audit trail; symbols below may be removed)

### `moments.py` — the clip DECISION stage (2-pass: pick windows, then author hooks)

- `_source_frames(cfg, src)` — PASS-1 stills: extracts up to 6 keyframes evenly across the whole source for the pick-author's eyes. Calls `extract_keyframes`. Fail-open `[]` if no real source file/duration. Called by `request_moments`.
- `_window_frames(cfg, src, start, end)` — PASS-2 stills: up to 3 keyframes over the picked+fitted window for the hook-author's eyes. Logs `hook_window_frames_empty` (warn) if extraction yields nothing. Called by `request_moment_hooks`.
- `_token(pick)` — formats `"{start:.2f}-{end:.2f}"` as the content-address token for a pick. Called by `ingest_moments`.
- `_peak_in_window(p, cs, ce)` — pure predicate, True iff a signal-peak dict's `t` falls in `[cs,ce]`; fail-open per-peak (malformed peak → excluded, never raises). Called by `request_moment_hooks`.
- `_drop_overlaps(picks)` — **within-owner** near-duplicate filter: keeps start-ordered picks, drops same-owner overlaps >50% of the shorter span; cross-owner overlap is allowed (single-owner rebuild). Pure. Called by `ingest_moments`.
- `validate_pick(pick, *, duration)` — pure validator returning a reason string or `None`; rejects non-finite timestamps, `end<=start`, negative start, overrun past EOF (0.5s tolerance), sub-0.5s duration, blank reason. Called by `ingest_moments`.
- `_is_num(v)` — pure try/except float-coercion probe. Called by `_bounded_transcript`.
- `_bounded_transcript(transcript, peaks)` — bounds a transcript to a 60,000-char budget by keeping segments nearest a signal peak (deterministic tie-break on index), preserving chronological order in output; returns `(kept_segments, dropped_count)`. Pure. Called by `request_moments`.
- `request_moments(led, cfg, source_id, accounts=None)` — **writes a gate request** (`write_request(kind="moments", ...)`) carrying transcript, duration, signal peaks, language, guidance, clip_profile, and PASS-1 frames. Sets `SourceState.moments_requested`. `accounts` param unused (signature stability only — personas belong to the hook pass). Mutates `led` in place and returns it. Called by `pipeline._stage_source_to_moments`.
- `ingest_moments(led, cfg, source_id)` — **reads a gate response**, validates + within-owner dedups picks, stamps `pick.personas[0]` → `Moment.affinities`, and reconciles into `picked` moments. On all-invalid picks → `SourceState.error`; on `[]` → non-terminal `moments_empty` (no reconcile). On reconcile: discards stale `moment_hooks` gates only (`discard_gates_for`); **no** casting-gate or durable-selection discard (removed P11/MOL-152). Then `led.reconcile_moments` + `picks_decided`. Called by `pipeline._stage_ingest_moments`.
- `request_moment_hooks(led, cfg, source_id, accounts=None)` — **writes one gate request per `picked` moment** (write-once). `_hook_personas_for_moment` sends **only the owner** account (P6). Window frames + fit window + signal peaks. Called by `pipeline._stage_moment_hooks`.
- `ingest_moment_hooks(led, cfg, source_id, accounts=None)` — **atomic-per-source**: waits for every pick's hook gate, then promotes all to `decided`. Writes `m.hook` only (no `hooks_by_persona` — removed P11). Weak/off-brand hooks nulled with `hook_removed` preserved. Called by `pipeline._stage_moment_hooks`.

### `casting.py` — **REMOVED pre-P11 LLM casting (archived audit trail only; live = `affinity_admits` only)**

> The casting LLM request/ingest gates, `account_selection_admits`,
> `repair_casting_selections`, `casting_bias`, and the durable selection maps were **deleted in
> P11/MOL-152**. `casting.py` is now 22 lines with one pure predicate. Operator `cast_add`/`cast_remove`
> writes `Moment.affinities` directly. See [fresh-ingestion-trace.md](../fresh-ingestion-trace.md) §3.

*(Pre-P11 function inventory retained in git history; not repeated here.)*

### `casting_bias.py` — **REMOVED P11** (module deleted with LLM casting teardown)

### `personas.py` — the Persona entity + facade re-export hub

- `CONTENT_FOCUS`, `ENERGY_LEVELS`, `HOOK_ANGLES` — module-level constants, `frozenset` projections of `persona_levers.vocab(...)` (via aliased import `_lever_vocab`).
- `Persona` (pydantic `BaseModel`) — fields: `id`, `name`, `voice`, `hashtag_corpus`, `intake`, `content_focus`, `energy`, `hook_angle`. Note the docstring records that per-persona `clip_profile`/`framing` pins and the 3 freeform directive overrides (`casting_directive`/`hook_directive`/`caption_directive` as persona fields) were **retired in M3/M3e** — they no longer exist on the model; cut length now derives from `content_focus`, framing from `energy`.
- `Personas.__init__(cfg)` — trivial init, `self.personas = []`.
- `Personas.load(cfg)` (classmethod) — reads `cfg.personas_path`, parses JSON, builds `Persona` list; raises `ControlFileError` (chained from the original exception) on a corrupt file rather than a raw traceback. Called by `accounts._hydrate_from_personas`, `persona_research.research_corpus`/`discover_corpus`, `persona_store.link_personas_by_voice`/`migrate_from_accounts`, CLI (`cli._check_accounts`, `_dispatch`, `_learn_pass`, `cmd_adjust`).
- `Personas.get(pid)` — linear lookup by id, `None` if `pid` falsy or not found.
- `Personas.all()` — returns a copy of the list.
- `_slug(s)` — pure: lowercase, strip leading `@`, collapse non-alphanumerics to `-`. Called by `persona_store.add_persona`/`migrate_from_accounts`.
- The tail of the file (lines 94-103) is a deliberate **facade re-export block**, importing every public name from `persona_directives`, `persona_store`, `persona_research`, and `persona_levers` back into the `personas` namespace so `from fanops.personas import X` keeps resolving — documented as load-order-safe (no cycle) because the siblings import the (already-partially-initialized) `personas` module back for `Persona`/`Personas`.

### `persona_directives.py` — the DIRECTIVE / COMPOSE / PREVIEW engine

- `_FOCUS_CLAUSE`, `_ENERGY_CLAUSE`, `_ANGLE_CLAUSE` (module-level) — projections of `persona_levers.clause_map(...)`.
- `_FOCUS_PROFILE`, `_ENERGY_FRAMING` (module-level) — projections of `persona_levers.focus_profile_map()`/`energy_framing_map()`.
- `derive_cut_spec(p)` — pure, duck-typed (Persona or hydrated Account): derives `(clip_profile|None, framing|None)` from `content_focus` (longest-tier-first match) and `energy`. Called by `_cut_fragments`, `resolved_cut_spec`.
- `resolved_cut_spec(p)` — pure: explicit pin OR derived OR `None`. THE single function both `accounts._hydrate_from_personas` and the operator UI (`compose_breakdown`) call — floor can't drift. Called by `accounts._hydrate_from_personas`, `compose_breakdown`, `persona_facts`, Studio views.
- `_base_voice(p)` — pure, duck-typed: reads `.voice` or the hydrated account's `.persona`. Called by `_caption_fragments`, `_casting_fragments`, `_hook_fragments`, `caption_directive`, `casting_directive`, `hook_author_slot`.
- `_join(voice, body)` — pure string join; either-empty → the other.
- `casting_directive(p)` — pure: compiles `content_focus` + `selection_scope` into the moment-pick lens clause, joined with the base voice (firewall: no levers → bare voice). Called by `moments._pick_personas`, `compose_breakdown`.
- `hook_directive(p)` — pure: compiles `hook_angle` into the hook-prompt clause + base voice. Called by `hook_author_slot`, `compose_breakdown`.
- `hook_author_slot(p)` — pure, ALWAYS non-empty: falls back `hook_directive` → inline voice → `tag_lean` hint → handle floor, so every active account gets a hook brief. Called by `moments.request_moment_hooks`.
- `caption_directive(p)` — pure: bare voice only (hashtags are deterministic elsewhere). Called by `caption.request_captions` (outside cluster).
- `compose_persona_instruction(p)` — alias for `casting_directive(p)` (back-compat + human-facing headline).
- `lever_catalog()` — pure, delegates to `persona_levers.build_catalog()`. Called by `manifest`.
- `_casting_fragments`, `_hook_fragments`, `_caption_fragments`, `_cut_fragments` — pure provenance-tagging helpers reconstructing which lever produced which text fragment. Called only by `compose_breakdown`.
- `compose_breakdown(cfg, p)` — pure read: the live composed translation (casting/hook/caption directive text + fragments, resolved cut band/framing, lead hashtags, no-op notes). Calls `band_for` (lazy import) and `persona_facts`. Called by `manifest`, Studio's `preview_compose`.
- `manifest(cfg, p)` — pure read: one row per editable lever (value, output channels, `produces`, `health`), derived from `persona_levers` + `compose_breakdown` so operator view and live output can't disagree. Called by Studio's `personas_page`.
- `produces_summary(breakdown)` — pure: distills an operator-facing clause list (e.g. `"~8-15s clips"`) from an already-built breakdown dict; each dimension is silent unless deliberately configured.
- `persona_facts(cfg, p)` — the transparency read: resolves length band, framing, and lead hashtags via `bands.band_for` and `hashtags.vet_hashtags`/`load_store` (lazy imports). Fail-open: `try/except Exception: store = None` if the hashtag store fails to load — this is the ONE bare `except Exception` in this file (line 287), swallowing any store-load error silently and falling through to a `None` store (which `vet_hashtags` handles as "no store"). Called by `compose_breakdown`, Studio's `personas_page`.

### `persona_levers.py` — the single lever REGISTRY (pure leaf, stdlib only)

- `PROFILE_TIERS`, `_CONTENT_FOCUS_OPTIONS`, `_ENERGY_OPTIONS`, `_HOOK_ANGLE_OPTIONS`, `_CLIP_PROFILE_BANDS`, `LEVER_REGISTRY` — module-level declarative data (the single source of truth).
- `PERSONA_FIELD_EXEMPT` — `frozenset({"id", "name", "intake"})`: identity/metadata fields NOT editable levers.
- `PERSONA_EDITABLE_CHANNELS` — dict: the 5 editable lever fields (`voice`, `content_focus`, `energy`, `hook_angle`, `hashtag_corpus`) → the output channel(s) each owns. Documents that 6 fields were deliberately quarantined/removed in M3 (tag_lean, clip_profile pin, framing pin, and 3 freeform directive overrides) as "incoherent" (no save-route control and/or duplicate channel ownership).
- `is_exempt(field)` — pure predicate. **No callers found anywhere in `src/`** (dead code candidate).
- `editable_fields()` — pure: `frozenset(PERSONA_EDITABLE_CHANNELS)`. Called by `persona_directives.manifest`.
- `channels_of(field)` — pure. Called by `persona_directives.manifest`.
- `all_channels()` — pure. Called by `channels()`.
- `channels()` — alias of `all_channels()`. **No callers found anywhere in `src/`** (dead code candidate — even its stated purpose, "the M4 manifest reads it," is inaccurate; `manifest` actually calls `channels_of`, not `channels`).
- `owner_of(channel)` — pure. Called by `persona_directives.manifest`.
- `lever(key)` — pure lookup. Called by `option_values`, `clause_map`, `focus_profile_map`, `energy_framing_map`, `build_catalog`.
- `option_values(key)` — pure. Called by `vocab`.
- `vocab(key)` — pure: `frozenset` of option values. **Confirmed used** — imported as `_lever_vocab` alias in `personas.py` (the call-graph's name-based matching missed this because of the alias).
- `clause_map(key)` — pure. Called by `persona_directives` module-level constants (`_FOCUS_CLAUSE` etc.) at import time — call-graph shows no function-level caller because these are module-level statements, but it IS exercised on every import.
- `focus_profile_map()` — pure, `OrderedDict` longest-tier-first. Called by `persona_directives._FOCUS_PROFILE` at import time (same module-level-call caveat).
- `energy_framing_map()` — pure. Called by `persona_directives._ENERGY_FRAMING` at import time (same caveat).
- `build_catalog()` — pure, lazy-imports `bands.band_for`. Called by `persona_directives.lever_catalog`.

### `persona_research.py` — per-persona hashtag corpus research + live discovery

- `research_corpus(cfg, pid, *, limit=8)` — budget-free offline re-rank: reach-ranked store minus the persona's current corpus, capped at `limit`. Raises `KeyError` on unknown `pid`. Reads `cfg.personas_path` via `Personas.load`; no writes. Called by `discover_corpus` (fallback), Studio's `research_corpus` route action.
- `discover_corpus(cfg, pid, *, limit=8, measure_k=0, get=None)` — live per-persona discovery via `meta_graph.discover_candidates` (Meta Graph co-occurrence harvest), seeded from the persona's corpus + `intake["genre"]`, excluding known tags (`VETTED ∪ store ∪ corpus`). **Fail-open**: `except Exception: cands = []` (line 56, catches "any Graph/transport error") then falls back to `research_corpus` wrapped as evidence-less dicts. Raises `KeyError` on unknown `pid`. No disk writes — read-only research. Called by `fanops_hashtags.cmd_hashtags_discover`, Studio's `research_corpus`.

### `persona_store.py` — persona WRITERS + account→persona migration

- `_enum_or_none(v, names, label)` — pure validator: lowercase-or-`None`; raises `ValueError` on an unknown non-empty value (write boundary). Called by `add_persona`, `update_persona`.
- `_norm_focus(content_focus)` — pure validator for the multi-select `content_focus` lever: lowercase, dedupe, raise `ValueError` on any unknown kind. Called by `add_persona`, `update_persona`.
- `_load_raw(p)` — reads `personas.json` as a raw dict + list (preserves unknown fields). Called by every mutator.
- `_personas_txn(cfg)` — context manager: serializes read-modify-write under `cfg.personas_lock_path` via `fanops.ledger._file_lock` (lazy import, avoids a load cycle). **Disk side effect**: `mkdir(parents=True, exist_ok=True)` on the lock dir. Called by `add_corpus_tag`, `add_persona`, `delete_persona`, `remove_corpus_tag`, `update_persona`.
- `add_persona(cfg, name, voice="", intake=None, id="", *, content_focus=None, energy="", hook_angle="")` — **writes** a new persona record atomically (`write_json_atomic`). Validates name non-blank, id uniqueness, and every lever against its vocabulary BEFORE acquiring the lock. Returns the id; raises `ValueError` on bad input. Called by `migrate_from_accounts`, Studio's `create_persona`.
- `update_persona(cfg, pid, *, name=_UNSET, voice=_UNSET, intake=_UNSET, content_focus=_UNSET, energy=_UNSET, hook_angle=_UNSET)` — **writes**; only passed fields change (sentinel pattern). Raises `KeyError` on unknown id, `ValueError` on bad lever value. Called by Studio's `edit_persona`/`research_corpus`.
- `add_corpus_tag(cfg, pid, tag)` — **writes** one normalized hashtag into a persona's corpus, deduped, capped at `_CORPUS_CAP=40`; refuses (raises) a NEW tag past the cap rather than silently dropping it. Raises `ValueError` on empty tag, `KeyError` on unknown id. Called by Studio's `add_corpus_tag`.
- `remove_corpus_tag(cfg, pid, tag)` — **writes**; removes one tag (normalization-insensitive); a tag not present is a silent no-op (intentional, not an error). Raises `KeyError` on unknown id. Called by Studio's `remove_corpus_tag`.
- `delete_persona(cfg, pid)` — **writes**; drops the matching record. Raises `KeyError` on unknown id. Note: doesn't cascade to accounts still linked — the docstring explicitly documents the dangling `persona_id` fails open at the next `Accounts.load` (falls back to inline persona). Called by Studio's `delete_persona`.
- `link_personas_by_voice(cfg)` — **reads accounts + writes account links**: idempotently links any unlinked account whose inline `.persona` string exactly matches a `Persona.voice`, via `accounts.link_persona`. Returns handles linked. Does NOT create personas. Called by `migrate_from_accounts`.
- `migrate_from_accounts(cfg)` — **the one-time lift**: for every account with no `persona_id` and a non-blank inline `.persona`, creates a first-class `Persona` (id = slug of handle) if one doesn't already exist, then links it via `accounts.link_persona`. Two SEQUENTIAL transactions (never nested locks): first `link_personas_by_voice`, then create+link. Idempotent. Returns `{created, linked, voice_linked}`. Called by Studio's `run_migration`.

### `accounts.py` — the flat active-account registry + lever hydration entrypoint

- `AccountStatus` (str Enum) — `planned`, `warming`, `active`, `retired`.
- `Account` (pydantic `BaseModel`) — the account record: `handle`, `account_id`, `platforms`, `status`, `access`, `persona`, `persona_id`, `clip_profile`, `framing`, `hashtag_corpus`, `content_focus`, `energy`, `hook_angle`, `persona_owns_profile` (hydration-only provenance flag, never persisted), `integrations` (per-platform poster id), `backends` (per-platform poster backend override), `ig_user_id` (per-account Meta Graph credential, non-secret).
- `Surface` (`NamedTuple`) — `(account, account_id, platform)`.
- `Accounts.__init__(cfg)` — trivial.
- `Accounts.load(cfg)` (classmethod) — reads `cfg.accounts_path`, parses JSON into `Account` list; raises `ControlFileError` (chained) on a corrupt file — deliberately distinct from a missing-file I/O error, which is allowed to raise raw ("a real problem, not 'invalid'"). **Always calls `_hydrate_from_personas(a, cfg)` before returning.** Called throughout the CLI/pipeline/studio.
- `Accounts.active()` — pure filter on `AccountStatus.active`. Called by `live_ready_channels`, `surfaces`, `validate`, `casting._persona_donor_moments`/`_upgrade_stale_fan_all_defaults`/`casting_gate_failed_to_open`.
- `Accounts.resolve_account_id(handle, platform=None)` — pure lookup: prefers `integrations[platform]`, falls back to `account_id`; raises `KeyError` (loud, never returns `""`) if the handle is known but has no id for the platform, or if the handle is entirely unknown. Called by `post.run._resolve_publish_account_id`.
- `Accounts.resolve_backend(handle, platform=None)` — pure lookup; returns `None` (never raises) when no override — the normal case. Called by `effective_provider`, Studio's `golive.go_live`.
- `Accounts.effective_provider(handle, platform=None)` — pure: explicit per-channel `backends` override, else a platform-aware bridge to the legacy global `FANOPS_POSTER` (only if it's a LIVE backend that actually serves the platform). `None` if neither. Called by `live_ready_channels`, `post.compress.publish_backend_for_post`, `post.run._post_provider`, `reconcile._reconcilable_routing`, Studio views.
- `Accounts.live_ready_channels()` — pure: active `(handle, platform, provider)` triples where the provider resolves AND has creds present (`cfg.backend_has_creds`). Called by `config.Config.effective_publish_mode`/`is_live_backend`/`live_route_exists`, `postiz_lifecycle._backend_is_postiz`, Studio's `golive.go_live`.
- `Accounts.validate()` — pure: returns a list of config-problem strings — missing per-platform ids, the R2/D5/D15 drift state (one side of `integrations`/`backends` set without the other), duplicate handles, and (when `creative_variation` is on) missing persona links / cut specs that match the global (no differentiation). Called by `cli._check_accounts`, `doctor.doctor_report`, `pipeline.advance`, Studio's `actions_run.run_advance`/`run_prepare`, `golive.go_live`.
- `Accounts.surfaces()` — pure: every active `(handle, platform)` as a `Surface`, each resolving its own poster id. Called by `crosspost.crosspost_clips`, `pipeline._aspects_for`/`_stage_refresh_caption_requests`/`_stage_render_and_caption`, Studio's `actions.crosspost_to_account`.
- `_persona_for_account(acc, reg)` — pure: resolves the `Persona` record for an account via `persona_id` first, else exact inline-voice match. Called by `_hydrate_from_personas`.
- `_hydrate_from_personas(accts, cfg)` — **THE hydration entrypoint** (traced above in data-flow). Wrapped in `try/except Exception: return` (line 250, the second bare-`except Exception` in this cluster) — any error loading `Personas` (corrupt/absent file) leaves every account's inline values untouched, never crashes a load. Called only by `Accounts.load`.
- `link_persona(cfg, handle, persona_id)` — **writes**: sets/clears `persona_id` atomically; a blank id clears the link. Does NOT validate the id exists (fails open at load time — Studio resolves against the live registry first). Raises `KeyError` on unknown handle. Called by `persona_store.link_personas_by_voice`/`migrate_from_accounts`.
- `load_accounts_safe(cfg)` — **never raises**: wraps `Accounts.load`, returns `(Accounts(cfg), None)` on success-equivalent, or `(empty Accounts(cfg), truncated_error_str)` on any exception — used by read paths that must degrade rather than crash. Called by `cli._cmd_doctor_fix_routing`, `config.Config.is_live_backend`/`live_route_exists`, `meta_graph.credentialed_ig_handles`/`resolve_meta_creds`, `reconcile._reconcilable_routing`.
- `_load_raw_accounts(p)` — reads `accounts.json` as raw dict + list. Called by every mutator.
- `_accounts_txn(cfg)` — context manager: serializes via `_file_lock(cfg.accounts_lock_path)` (lazy import from `ledger`). Called by `add_account`, `ensure_channel`, `link_persona`, `remove_account`, `set_backend`, `set_channel_routing`.
- `write_integration(cfg, handle, platform, integration_id)` — **writes**: maps one `(handle, platform)` to its poster id in `integrations`. Raises `ValueError` on unknown platform, `KeyError` on unknown handle. Called by Studio's `golive.adopt_channels`/`map_account`.
- `set_backend(cfg, handle, platform, backend)` — **writes**: sets/clears one channel's backend override in `backends`; blank/"default" clears. Validates platform + backend vocab. **CORRECTED: LIVE — called as `_accounts_set_backend` at `studio/golive.py:188`/`:506` (aliased import the call graph missed).**
- `set_channel_routing(cfg, handle, platform, *, backend, integration_id)` — **writes**: atomically sets BOTH `integrations[platform]` and `backends[platform]` together (the R2 replacement for the two-write `write_integration`+`set_backend` seam that caused "the cisumwolfhom incident" per the docstring). Refuses partial/clearing calls. **No callers found anywhere in `src/`** (dead code candidate — the fix for a documented incident appears never wired into any route or CLI command).
- `add_account(cfg, handle, platforms, persona="", status="active", access="postiz", clip_profile="", framing="")` — **writes**: onboards a brand-new account atomically; validates platforms/clip_profile/framing vocab; rejects duplicate handle. Called by Studio's `app_routes_golive.register_golive_routes`.
- `ensure_channel(cfg, handle, platform, persona="")` — **writes** idempotently: appends a platform to an existing account or creates a new inert one. Never raises on duplicate (by design). **CORRECTED: LIVE — called as `_accounts_ensure_channel` at `studio/golive.py:501` (the discover→adopt flow; aliased import the call graph missed).**
- `set_status(cfg, handle, status)` — **writes**: changes one account's status atomically. Validates against `AccountStatus`. **CORRECTED: LIVE — called as `_accounts_set_status` at `studio/golive.py:543`/`:558` (aliased import the call graph missed).**
- `set_clip_profile(cfg, handle, profile)` — **writes**: sets/clears the per-account clip length tier; validates against `bands.PROFILE_NAMES`. Called by Studio's `app_routes_golive.register_golive_routes`.
- `set_framing(cfg, handle, framing)` — **writes**: sets/clears the per-account crop bias; validates against `config.FRAMING_NAMES`. **No callers found anywhere in `src/`** (dead code candidate).
- `set_ig_user_id(cfg, handle, ig_user_id)` — **writes**: sets/clears the per-account Meta Graph IG business user id (non-secret; token itself lives in `.env`, not here). **CORRECTED: LIVE — called as `_accounts_set_ig_user_id` at `studio/golive.py:381` (aliased import the call graph missed).**
- `set_persona(cfg, handle, persona)` — **writes**: sets/clears the inline persona string. Called by Studio's `app_routes_golive.register_golive_routes`.
- `remove_account(cfg, handle)` — **writes**: deletes an account row atomically. Called by Studio's `app_routes_golive.register_golive_routes`.

### `batches.py` — Account-First Studio: named, account-targeted ingest groups

- `_resolver_now_utc()` — trivial clock wrapper (`datetime.now(timezone.utc)`), extracted purely so tests can pin the date deterministically. Called by `resolve_or_mint_drop_batch`.
- `resolve_or_mint_drop_batch(led)` — mint-once-per-UTC-day fallback batch named `drop-YYYY-MM-DD`, content-addressed on `(name, midnight_iso)` so repeated calls the same day are idempotent (`led.get_batch(bid)` short-circuits). `target_accounts=[]` (the all-active sentinel) preserves today's byte-identical fan-to-all default for unbatched ingest. Called by `ingest.ingest_drops`.
- `create_batch(led, *, name, target_accounts, now_iso, active_handles=None, burn_subs=None)` — pure on an already-loaded `Ledger` (caller holds the transaction). Validates `name` non-blank (`ValueError` otherwise), normalizes `target_accounts` to a stripped/deduped/order-preserving handle list (`[]` = all-active sentinel, never flagged). When `active_handles` is supplied and the target intersects NO active handle, sets an advisory `Batch.error_reason` (state stays open, batch still mints — never a hard error). Mints `Batch(id=batch_id(name, now_iso), ...)`, calls `led.add_batch(b)`, returns it. Called by `resolve_or_mint_drop_batch`, Studio's `actions_run.run_ingest`.

**Denormalization onto Source/Post** (traced via `crosspost.py`, outside this cluster but the consumer): `Batch.target_accounts` lands on `Source.batch_id` at ingest time; `crosspost_clips` (crosspost.py:325-335) reads `led.get_batch(src_batch).target_accounts` and — for a non-empty target — HARD-bounds the fan-out, emitting a `batch_target_skip` breadcrumb per excluded surface and a `batch_target_summary` for the pass. This is the enforcement point `batches.py` itself never touches (batches.py is pure minting; crosspost.py is the sole enforcer).

## Casting / routing decision logic (current — post-P11)

**There is no separate casting pipeline stage.** Routing is stamped at pick time and gated by one predicate.

- **Owner attribution (pick):** `ingest_moments` stamps `pick.personas[0]` → `Moment.affinities` (single owner by convention; `[]` = persona-blind fan-to-all). Per-owner overlap dedup only (`_drop_overlaps`).
- **THE crosspost + caption-scope gate:** `affinity_admits(cfg, moment, account)` (`casting.py:10-22`) — casting OFF → admit all; `moment is None` → DENY; `affinities==[]` → fan-to-all; else admit iff `account in affinities`. No ledger read, no LLM stage.
- **`cfg.account_casting`** (`config.py:593-600`, `FANOPS_ACCOUNT_CASTING`) — **DEFAULT ON** (`=0` restores legacy fan-to-all that ignores persisted affinities). This flag gates `affinity_admits`; it does NOT invoke a second LLM gate.
- **Operator override (P13):** `cast_add`/`cast_remove` (`studio/actions_casting.py`) mutate `Moment.affinities` directly (may deliberately co-own).
- **Removed (P11/MOL-152):** casting LLM request/ingest gates, `_stage_casting`, durable selection maps, `casting_bias`/`FANOPS_CASTING_BIAS`, `account_selection_admits`, `hooks_by_persona`.
- **Historical:** `cast_moments` token-overlap heuristic deleted WS-M1/MOM-7 (predates P11).

## Anomalies found

**Dead code candidates — CORRECTED ON VALIDATION.** The first pass trusted the name-based
call graph's zero-call-site result, but that graph cannot resolve **aliased imports**
(`from fanops.accounts import set_backend as _accounts_set_backend`). Four of the eight below are
actually LIVE via `_accounts_*` aliases in `studio/golive.py`. Re-verified by grepping for both the
bare name and every `<name> as <alias>` binding across `src/`:
- `src/fanops/accounts.py:347` `set_backend` — **NOT dead.** Called as `_accounts_set_backend` at `studio/golive.py:188` and `:506`.
- `src/fanops/accounts.py:383` `set_channel_routing` — **genuinely dead** (no bare or aliased call site). The documented fix for "the cisumwolfhom incident" (a real production drift bug per its own docstring) appears never wired into any route.
- `src/fanops/accounts.py:469` `ensure_channel` — **NOT dead.** Called as `_accounts_ensure_channel` at `studio/golive.py:501` (the discover→adopt flow it was built for).
- `src/fanops/accounts.py:509` `set_status` — **NOT dead.** Called as `_accounts_set_status` at `studio/golive.py:543` and `:558`.
- `src/fanops/accounts.py:553` `set_framing` — **genuinely dead** (no bare or aliased call site; note: `set_clip_profile` IS wired via Studio go-live routes; its sibling `set_framing` is not).
- `src/fanops/accounts.py:576` `set_ig_user_id` — **NOT dead.** Called as `_accounts_set_ig_user_id` at `studio/golive.py:381`.
- `src/fanops/persona_levers.py:87` `is_exempt` — **genuinely dead** (no caller).
- `src/fanops/persona_levers.py:107` `channels` — **genuinely dead** (no caller); its own docstring's claim ("the M4 manifest reads it") is inaccurate — `manifest` actually calls `channels_of`, a different function.

**Fail-open exception handlers (all intentional per surrounding comments, not silent-failure bugs — cited for completeness):**
- `src/fanops/persona_directives.py:287` `except Exception: store = None` in `persona_facts` — silently swallows any hashtag-store load error with no logging (the one handler in this cluster with zero trace on failure — worth flagging since every other fail-open path in this cluster logs via `get_logger` before swallowing).
- `src/fanops/persona_research.py:56` `except Exception: cands = []` in `discover_corpus` — documented fail-open to the offline `research_corpus` re-rank.
- `src/fanops/accounts.py:250` `except Exception: return` in `_hydrate_from_personas` — documented fail-open leaving inline account values untouched.

**No TODO/FIXME/XXX markers** found in any of the 10 files (grep confirmed zero hits).

**No bare `except:`** (all exception handlers are typed `except Exception` or narrower — e.g. `moments.py`'s `except (TypeError, ValueError)`).

**Retired-field trap**: `persona_directives.py` and `persona_levers.py` repeatedly reference 6 fields deliberately removed from the `Persona`/`Account` models in M3/M3e (`tag_lean`, per-persona `clip_profile`/`framing` pins, and 3 freeform directive overrides `casting_directive`/`hook_directive`/`caption_directive` as persona fields — not to be confused with the compiler *functions* of the same names in `persona_directives.py`, which are current and load-bearing). A reader tracing "casting_directive" by grep alone would conflate the retired persona-field override with the live compiler function; the distinction is only clear from reading the docstrings, which is why this cluster is worth reading in full rather than name-matching.
