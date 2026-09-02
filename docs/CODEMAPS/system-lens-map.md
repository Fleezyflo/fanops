> Frozen 2026-07-11 — invariants map, not auto-synced. When prose and code disagree, the code is right.

<!-- Generated: 2026-07-03 | Method: source read (large-chunk reads of ~40 src/fanops modules + studio routes + live control files), every claim carries a personally-verified file:line; no prior-conclusion inputs, navigation aids treated as maps not truth | Files read: 41 | Token estimate: ~7800 -->
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
| 7 | Crosspost | `crosspost.crosspost_clips` → `_mint_surface_post` (`crosspost.py:299,168`); pipeline (`pipeline.py:243`) | captioned clips, surfaces, selections, batch target | `Post` born `awaiting_approval` (`crosspost.py:269-273`), clip state→`queued` | none | Wrapped so a raise doesn't cost the pass; a FATAL `AuthError` deliberately escapes (`pipeline.py:249-255`) |
| 8 | Reconcile | `reconcile.reconcile_due` (`reconcile.py:339`); pipeline `_reconcile_safe` (`pipeline.py:258`) | stranded posts, backend status | back-fills `public_url`, `publish_hour`/`publish_dow` (`reconcile.py:452-453`) | backend status GETs (out of lock) | Gated `cfg.is_live_backend`; `AuthError` halts, else logged (`pipeline.py:265-271`) |
| 9 | Publish | `post.run.publish_due` → `_publish_one` (`run.py:337,213`); pipeline `_publish_safe` (`pipeline.py:274`) | queued+due posts | claim→`submitting`→`published`/`needs_reconcile`/`failed`; `published_at`+`publish_hour`/`dow` stamped (`run.py:266-270`); `06_published/<day>/<pid>.json` archive (`run.py:25`) | media upload + `poster.publish` (out of lock); Postiz throttle `postiz_publish_per_min` (`run.py:95`) | `AuthError` halts the run (`run.py:277-279`); other errors → per-post `failed` (re-queueable) except `needs_reconcile` (not downgraded, `run.py:280`) |
| 10 | Summary/digest | `pipeline._build_summary` (`pipeline.py:339`) | post-publish reload | `write_digest` (read-only, out of lock) | none | Read-only |

**Framing detail** (`clip._resolve_framing` `clip.py:527`): classifies the window
(`multi-speaker-talk | single | music | silent | no-people`), routes to active-speaker TRACK (segment-concat,
locked-off static crop per shot) / subject FOCUS / motion SALIENCY / centered. Zoom is face-size-adaptive
(`_adaptive_zoom_max` `clip.py:427`). Entirely gated by `cfg.smart_framing` (default ON) and FAIL-OPEN at
every step to the centered crop (`clip.py:533,550`).

### 1.2 Configuration layer — environment surface

The env surface is machine-derived. Every `os.getenv` / `os.environ` read under `src/fanops/` is censused
in [`.reports/architecture/derived/configuration.json`](../../.reports/architecture/derived/configuration.json)
(regenerated by `python -m tools.arch regen`). Operator-facing names, defaults, and meanings live in
[`docs/CONFIG.md`](../CONFIG.md). This map does not hand-copy either — a prose per-var table and any hard
env count rot the day after they are written.

### 1.3 Cross-reference — Studio-settable vs .env-only

The ONLY Studio setter of environment variables is the Go-Live tab via `golive._dual_write`
(`studio/golive`), which writes BOTH `.env` and `os.environ`. Membership of the Studio-settable set is
`fanops.settings.STUDIO_SETTABLE` (a projection of the `EnvVar` marker on each `Settings` field).
Everything else in the derived census is `.env`/shell-only from Studio's perspective — including trust-gate
numerics and Phase-2 bias kill switches. Census:
[`.reports/architecture/derived/configuration.json`](../../.reports/architecture/derived/configuration.json).
Operator surface: [`docs/CONFIG.md`](../CONFIG.md).

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

> Live ship is `docs/CODEMAPS/hashtag-lifecycle.md` (`ship_from_lock`). Live vendor
> `content` is `src/fanops/post/CLAUDE.md` (`posted_text_for` / `compose_posted_caption`).
> `vet_hashtags` / `derive_corpus` are deleted (`tests/test_hashtag_layer_b_tombstone.py`).
> §§2.1–2.5 below are the 2026-07 snapshot of that deleted path, not current send.

### 2.1 Sources a tag can enter from (end-to-end)

1. **Composition floors** — `_ARABIC` only (`hashtags.py`). FORMAT, not a reach claim: one region tag on
   Arabic clips. The frozen reach-ranked pools (`_MEGA`/`_RELEVANCE`/`_RANK`/`VETTED`) and `_DISCOVERY` were
   DELETED 2026-07-26 — they asserted reach from desk research.
2. **The platform measurement cache** — `fanops_hashtags.refresh_store` derives search terms from each
   posting persona's declared niche (`persona_research.persona_terms`), resolves them via
   `meta_graph.resolve_hashtag`, and one `meta_graph.measure_and_harvest` call per tag returns both the
   verbatim `like_count` and the co-occurring tags. Writes `00_control/hashtags.json` as
   `{tag: {graph_id, like_count, measured_at, from}}` — measured tags only. Read side:
   `hashtags.load_measurements` + `ranked_tags` (`hashtags.py:~101`). 12h throttle inside `run` via
   `refresh_store_if_due`. No local budget: Meta's own throttle codes end a pass, and a cached `graph_id`
   means a known tag never spends another search.
3. **Per-persona DERIVED corpus** — `Persona.hashtag_corpus`, recomputed every tick by
   `persona_research.derive_corpus` (zero network) as the top `corpus_target` of the persona's aligned,
   measured pool; hydrated onto the account (`accounts._hydrate_from_personas`). Pre-derivation tags live
   in `hashtag_corpus_deprecated` — visible, never shipped.
4. **Per-clip content derivation** — `hashtags.content_tag_candidates` (`hashtags.py:~117`): deterministic,
   pure, NO NLP — latin word tokens 3-20 chars, stopword-filtered, frequency-then-first-seen ordered,
   capped at 6. Blank/Arabic/numbers → `[]` (byte-identical).
5. **Operator actions** — Studio Personas niche edit (`/personas/niche`) roots Layer A via `persona_terms`.
   Corpus add/remove / `research_corpus` / discovery auto-write paths are gone — the corpus is derived.

### 2.2 Persistence locations + schemas (from code)

- `00_control/hashtags.json` — `{tag: {graph_id, like_count, measured_at, from}}` (measured cache;
  read `hashtags.load_measurements`). Provenance source label `graph-reach` (`hashtags._tag_source`
  `hashtags.py:~236-245`).
- `00_control/personas.json` — `Persona.hashtag_corpus: list[str]` (`personas.py:41`).
- `Clip.meta_captions[surface]` — `_caption_entry` (`caption.py:266`) writes
  `{"caption": " ".join(tags), "hashtags": tags, "hashtags_raw": [...verbatim model picks...],
  "hook": None, "axis": None, "rationale": None, "tag_sources": {tag: source}}` (`caption.py:276-277`);
  `fallback: True` on seed synthesis (`caption.py:278`).
- `Post.hashtags: list[str]` (`models.py:227`) — copied from `cap.get("hashtags")` at mint
  (`crosspost.py:275`).

### 2.4 How tags reach the LLM prompt (exact text)

`caption_prompt` (`prompts.py:348`). The menu IS the measurement cache — `menu` is read straight off
`payload["hashtag_store"]` (`prompts.py:376`), already metric-ranked; there is no frozen pool to union in and
no niche floor to pick, because a tag with no platform measurement is not a candidate. An absent cache yields
an EMPTY menu and the surface corpus carries the line (`prompts.py:373-375`). ONE pick-rule, not two
(`prompts.py:378-380`): *"Pick up to 4 tags by how well each fits THIS clip — the menu is already ordered by
live platform reach, so prefer earlier entries when the fit is equal. Choose ONLY from the menu UNION each
surface's `corpus`; do NOT invent tags outside them: {menu_json}."*

The HARD RULE (`prompts.py:402-405`): *"Each `caption` is HASHTAGS ONLY: a single line of AT MOST 4 hashtags
(MAX 4 — fewer is fine) separated by spaces and NOTHING ELSE — no sentences, no prose, no @mentions, no
emoji. Put the SAME tags in the `hashtags` array. ... Anything beyond 4 or off-menu is dropped by the system,
so pick well."* NO composition template is prescribed — the "balanced 4" (mega genre + relevance + region +
discovery) went out with the frozen pools on 2026-07-26 (§2.2). Corpus instruction (`prompts.py:408-410`):
*"When a surface carries a `corpus` (its curated, reach-vetted tag pool), PREFER the tags in that surface's
`corpus` for that surface — they are its hand-picked, account-specific tags; fill any remaining slots (up to
4) from the menu above."*

### 2.5 Enforcement on LLM output (the full vet algorithm, step by step)

`vet_hashtags` (`hashtags.py:~153-230`), traced variant `vet_hashtags_traced`. Called from
`ingest_captions` — **it never trusts the model**:
1. Normalize+dedupe corpus, content and store. Content is brand-risk screened (`_screen_content`).
2. Membership set = store ∪ corpus ∪ content — corpus and content JOIN the gate so a derived or
   clip-specific tag the measured cache doesn't know SURVIVES. No bans; no frozen pool in the union: a
   tag in none of the three dies here.
3. Rank base = the measured store's OWN order (`ranked_tags`, `hashtags.py:~101`) — there is NO frozen
   fallback, so a cold cache yields an empty base rank; preference float: corpus > content ahead of the
   metric rank (negative-indexed).
4. Seed the WHOLE corpus first, then honor the model's picks but ONLY vetted ones.
5. Sort on FOUR keys: tier (corpus 0 > content 1 > cache 2), then model-picked-first, then a GRADED LRU
   (`recent` arrives oldest-first, last write wins, never-used tags lead), then the rank from step 3.
6. **Corpus LEAD cap** `_CORPUS_LEAD_MAX = 3` (`hashtags.py:~39`): a corpus of ≥ `max_tags` would
   monopolise the line; surplus corpus tags keep their order BEHIND the picks and still backfill.
7. **Reserved floors** take TAIL slots so the corpus/metric lead is preserved: one `_ARABIC` tag on an
   Arabic-language clip (`hashtags.py:~28`), spliced in as `head + reserved`.
8. **Backfill**, corpus-first: corpus → the measured store → content. No discovery pad.
9. **HARD cap** `kept[:max_tags]` (`max_tags=4`) — no `_strip_banned`.

Provenance (`vet_hashtags_traced` → `_tag_source` `hashtags.py:~236-245`): each shipped tag labelled
`content > corpus > region > graph-reach` (four labels) — TOTAL by construction: membership is cache ∪
corpus ∪ content and the only tag added outside that is the AR region floor. Cold cache + no corpus →
empty line. Recorded in `meta_captions[surface].tag_sources`.

### 2.6 What determines rank; feedback; caption composition

- **Rank everywhere**: in the store, by the platform metric desc (`ranked_tags`, `hashtags.py:~101`); in
  `vet_hashtags`, corpus > content > the measured cache's own order (`hashtags.py:231-265`). The frozen
  `_MEGA`/`_RELEVANCE`/`_RANK`/`VETTED` pools were DELETED 2026-07-26 (§2.2) — NO static class-ranking
  remains anywhere in the path.
- **Post-performance feedback into tag selection: NONE.** The `lift_score` weight map `_W` (`track.py:30`)
  carries NO hashtag dimension; the store is ranked by the tag's OWN live Graph reach, never a post that used
  it (`fanops_hashtags.py:2-4`). Pinned by `tests/test_hashtag_attribution_severance.py`
  (`test_lift_weights_carry_no_hashtag_dimension`, `test_no_learning_module_attributes_a_post_outcome_to_hashtags`).
- **IG/TT vendor `content` is `posted_text_for` → `compose_posted_caption`.** Empty lock keeps stored
  `Post.caption` (hashes included); lock tags append when present. YouTube sends `Post.caption` raw.
  On-screen hook stays the moment gate. `_caption_entry` / `vet_hashtags` (`caption = " ".join(tags)`)
  are the deleted ingest snapshot, not the send path.

### 2.7 Tests pinning hashtag behavior

`tests/test_hashtags.py` (hard cap at 4 `test_hard_caps_at_four`; drops non-vetted; reach ordering; Arabic
floors; corpus float/floors), `tests/test_content_aware_hashtags.py` (content extraction, floor reserves a
slot, every-kept-tag-has-a-source, byte-identical-without-content), `tests/test_hashtag_attribution_severance.py`
(no post→hashtag feedback), `tests/test_fanops_hashtags.py`, `tests/test_hashtag_lifecycle_e2e.py`,
`tests/test_persona_corpus.py`.

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
- **Niche** — declared niche via `/personas/niche` roots `persona_terms` (Layer A search + Layer B alignment).
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
| `niche` | Graph search roots + corpus alignment | `persona_terms` → Layer A anchors + Layer B `_aligned_pool` |

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

Posts are constructed at three mint sites, all birthing `PostState.awaiting_approval`:
(1) pipeline crosspost: `crosspost._mint_surface_post` → `led.add_post(Post(...))` (`crosspost.py:228`);
(2) Studio repost: `studio.actions.repost_post` (`actions.py:491`);
(3) Studio cross-account reuse: `studio.actions.crosspost_to_account` (`actions.py:570`; bulk
`crosspost_all_to_account` loops here). **Every birth is `awaiting_approval`** (model default
`models.py:220`) with `submission_id="fanops_<hash>"` (client idempotency token), `render_id=None`,
`media_urls=[]`, and P1/P3 attribution dims stamped on the pipeline path (`first_frame_kind`, `cut_seconds`,
`clip_profile`, `top_bias`, `batch_id`, `variation_axis`, `crosspost.py:228-246`). A `Post` model_validator
refuses `published`/`analyzed`/`retired` without a non-empty `public_url` (R1 terminal-URL invariant,
`models.py:279-299`).

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

**Validation gate (shared, but NOT universal):** `validation_gate.learning_validated(cfg)` =
`cutover.json metrics_confirmed` (`validation_gate.py:22`). Auto-stamped by
`track._auto_validate_metrics_shape` on the FIRST real non-degraded analyzed metric from a LIVE backend
(`track.py:343-370`) — NOT an operator step; dryrun never proves it (`track.py:352`); a degraded row never
stamps (`track.py:363-367`). It is ONE-WAY: nothing in the tree writes `metrics_confirmed` False, so once
stamped it never re-closes. That makes it a data-quality stamp on the metric FIELD-SHAPE, never operator
consent — consent is each actuator's own default-OFF flag. `p4_unlocked(led,cfg,dim)` =
`learning_validated` AND `enough_attributed_signal` (≥8 attributed posts across ≥2 distinct values,
`_MIN_ATTRIBUTED_N=8`/`_MIN_VALUES=2`, `validation_gate.py:18-19` / `enough_attributed_signal`
`validation_gate.py:32-39`). The two `learn_*` rows below carry NO validation gate: the freeze was removed
from those chains precisely because a condition that can never re-bind gates nothing.

| Actuator (file:line) | Kill switch (default) | Validation gate | Thresholds |
|----------------------|------------------------|-----------------|-----------|
| `adjust.amplify` via `cli._learn_pass` (`cli.py:182-186`) — re-open a moment request on a metric winner's SOURCE, minting new moments → clips → posts | `FANOPS_LEARN_AMPLIFY` (OFF) | NONE — the flag is the whole gate | top `winner_pct` of scored posts (default `0.3`, `adjust.classify_outcomes`); at most `adjust.MAX_AMPLIFY_PER_SOURCE`=3 amplifies per source |
| `adjust.retire` via `cli._learn_pass` (`cli.py:189-193`) — suppress a metric loser's clip, its moment when no live sibling remains, and every unshipped post of that lineage | `FANOPS_LEARN_RETIRE` (OFF) | NONE — the flag is the whole gate | needs ≥`adjust._MIN_SCORED_N`=8 scored posts over ≥`_MIN_DISTINCT_SCORES`=2 distinct scores, else NO losers; the bottom `retire_pct` slice (default `0.2`) only CAPS the count — each loser must also score below `min(lift_floor=20.0, median × _RETIRE_LIFT_RATIO=0.25)`; winners and `lift_degraded` rows are excluded |
| `p4_dim_bias.apply_p4_dim_bias` (`p4_dim_bias.py:57`) — amplify a rep source per winning creative dim (`first_frame_kind`, `clip_profile`, `top_bias`) | `FANOPS_P4_DIM_BIAS` (OFF) | `p4_unlocked(dim)` (`p4_dim_bias.py:38`) | leader beats runner-up by ≥`p4_min_reach_gap` (default 0.0); ≥8 posts × ≥2 values |
| `timing_bias.apply_timing_bias` (`timing_bias.py:80`) — write reach-winning `publish_hour` prior consumed by `surface_time` | `FANOPS_TIMING_BIAS` (OFF) | `p4_unlocked('publish_hour')` (`timing_bias.py:36`) | ≥`p4_min_reach_gap` lead; window-clamped to `account_window` (`timing_bias.py:64`) |
| ~~`casting_bias.casting_reach_prior`~~ | — | — | **REMOVED P11** |
| `variant_amplify.apply_variant_amplify` (`variant_amplify.py:156`; flag `config.variant_amplify`) — re-mine a source off a sustained hook winner | `FANOPS_VARIANT_AMPLIFY` (OFF) | `learning_validated` (validation-frozen, `variant_amplify.py:166`) | ≥8 posts, ≥25.0 gap, ≥3 distinct windows |
| `variant_learning`/`ucb_rank` caption bias (`caption._learned_hooks`, `caption.py:122-141`) | `FANOPS_VARIANT_LEARNING` (OFF), `FANOPS_VARIANT_UCB` (OFF) | NOT validation-frozen (safe reversible read side, `config.variant_ucb`) | ≥3 posts, ≥10.0 gap (v2); UCB c=sqrt(2) |
| `variant_transfer` cold-start bias (`caption._transferred_hooks`, `caption.py:144-167`) | `FANOPS_VARIANT_TRANSFER` (OFF) | `learning_validated` (`caption.py:153`) | ≥2 donors, ≤2 borrowed |
| `moment_hook_learning` (`config.moment_hook_learning`) — feed winning hook STYLES to the moment author | `FANOPS_MOMENT_HOOK_LEARNING` (OFF) + `FANOPS_VARIANT_LEARNING` | (rides variant_learning) | STYLE cue only |

The `p4_dim_bias` / `timing_bias` / `variant_*` / `moment_hook_learning` family is AMPLIFY/BIAS-ONLY
(audit C1: never retire/cascade/track — `p4_dim_bias.py:8-11`), FAIL-SAFE (exception → logged once, ledger
byte-identical), and introduces NO new auto-publish path (biases GENERATION + SCHEDULE only). **The two
`learn_*` rows are the exception, and are the reason the amplify-only claim can no longer be made of the
whole table:** `learn_amplify` MINTS — new moments, clips and posts, from an autonomous loop — and
`learn_retire` DESTROYS and CASCADES — the clip, the moment when no live sibling remains, and every
unshipped post of that lineage. That is exactly why each carries a default-OFF flag as its whole gate;
both previously ran on `cfg.is_live_backend` alone, so going live to PUBLISH also switched them on.

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

2. **Studio does not expose every env var; trust-gate numerics and Phase-2 bias kill switches are
   .env/shell-only.** Membership is `settings.STUDIO_SETTABLE` against the derived census
   (`.reports/architecture/derived/configuration.json`); see §1.3. An operator-only deployment cannot
   turn on the reach-loop bias actuators or tune their thresholds without shell access.

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

5. **Hashtag ranking is fully severed from post performance — the tag's OWN live platform measurement is
   the sole judge, enforced by an invariant test.** `_W` carries no hashtag dimension and the store ranks
   by `METRIC_FIELD = "like_count"` — Meta's own field, read off the first `top_media` item that carries
   one by `measure_and_harvest` — never by a post that used the tag (`hashtags.py:27,107`,
   `meta_graph.py:282`, `track.py:30`), pinned by `tests/test_hashtag_attribution_severance.py`.
   Combined with `vet_hashtags`' hard `[:4]` cap and the never-empty provenance label
   (`hashtags.py:206,222`), no model-invented or performance-derived tag can ever ship. Evidence: the
   attribution-severance test names + the vet algorithm.

**UNRESOLVED:** None material to the mapped questions. One narrow item: the `origin="scan"` catalogue path is
declared in `_catalogue_file`'s contract (`ingest.py:174`) but I did not locate a production caller that
invokes `ingest_drops`/`_catalogue_file` with `origin="scan"` (only `scan_local` which merely enumerates,
`ingest.py:301`) — so whether the scan channel actually catalogues in the running system is not settled from
the files I read.
