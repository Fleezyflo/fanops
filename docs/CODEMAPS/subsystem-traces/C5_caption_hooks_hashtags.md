# C5: Caption, Hooks & Hashtags

## Files covered (all 9 read in full)

1. `src/fanops/caption.py` (357 lines) — read
2. `src/fanops/hashtags.py` (237 lines) — read
3. `src/fanops/fanops_hashtags.py` (145 lines) — read
4. `src/fanops/tagging.py` (42 lines) — read
5. `src/fanops/hookcheck.py` (50 lines) — read
6. `src/fanops/hookscore.py` (67 lines) — read
7. `src/fanops/text.py` (42 lines) — read
8. `src/fanops/prompts.py` (443 lines) — read
9. `src/fanops/llm.py` (185 lines) — read

Cross-checked against `.reports/structural_index.json` (function/class/module-var lists match exactly for all 9 files) and `.reports/call_graph.json`. One connective-tissue file outside this cluster's exact list, `src/fanops/responder.py`, was also read (not exhaustively catalogued) because it is the actual LLM-gate dispatcher that wires `prompts.py`'s prompt-builders and `llm.py`'s `claude_json_meta` together — without it the call graph's zero-caller readings for `moment_pick_prompt`/`moment_hook_prompt`/`caption_prompt` would misread as dead code.

## Pipeline/data-flow overview

### LLM call sites — the agent-gate dispatch pattern

This cluster does **not** call an LLM function directly from `caption.py`. It follows the same write_request/read_response agent-gate pattern used elsewhere in the codebase (`agentstep.py`, outside this cluster):

```
caption.request_captions(led, cfg, clip_id, surfaces, accounts)
   │  builds payload (transcript_excerpt, guidance, per-surface persona/corpus,
   │  content_tags, learned_hooks, learned_hooks_transferred)
   ▼
agentstep.write_request(cfg, kind="captions", key=clip_id, payload=payload)
   │  (writes 00_control/agent_io/captions/<clip_id>.request.json; sets ClipState.captions_requested)
   ▼
   ... asynchronously, on a later `fanops run` tick ...
   ▼
responder.py: LlmResponder.answer_pending(cfg)
   │  pairs = [(kind, model_cls, key) for kind in _SCHEMA for key in pending(cfg, kind)]
   │  _answer_one(cfg, "captions", CaptionSet, clip_id, log)
   │    payload = json.loads(request_path(...).read_text())
   │    out = self._model("captions", payload)   # self._model = _default_claude_model bound to cfg
   ▼
responder._default_claude_model(kind="captions", payload, cfg)
   │  schema = CaptionSet.model_json_schema()
   │  images = None            # captions is NOT in _VISION_GATES — text-only
   │  prompt = _PROMPT["captions"](payload)   # = prompts.caption_prompt(payload)  <-- THE PROMPT BUILDER
   ▼
llm.claude_json_meta(prompt, schema, images=None, model=cfg.llm_model_for("captions"))
   │  <-- THE ACTUAL API-CALLING PRIMITIVE: shells `claude -p --json-schema ... --allowedTools "" `
   │      (subprocess.run, stdin-piped prompt, rate-limit backoff, timeout handling)
   ▼
   writes CaptionSet-shaped JSON -> agentstep.write_response(cfg, "captions", clip_id, json)
   ▼
caption.ingest_captions(led, cfg, clip_id)
   │  read_response(cfg, "captions", clip_id, CaptionSet)  # None if pending/stale
   │  vets hashtags via hashtags.vet_hashtags_traced, brand-risk-checks, language-checks
   ▼
   clip.meta_captions[surface] = _caption_entry(...); ClipState.captioned
```

The SAME dispatch shape is used by `moments.py` (outside this cluster, in C4) for the `moments`/`moment_hooks` gates, whose prompt builders (`moment_pick_prompt`, `moment_hook_prompt`) also live in `prompts.py` — this is why `call_graph.json` shows `called_by_in_repo: []` for those functions and for `caption_prompt`: the AST-based call graph cannot trace `_PROMPT[kind](payload)` (a dict-value indirect call in `responder.py`) back to the literal function name. Confirmed real callers via grep: `responder.py:46-47` binds `_PROMPT = {"moments": moment_pick_prompt, "moment_hooks": moment_hook_prompt, "captions": caption_prompt}` and calls `_PROMPT[kind](payload)` at `responder.py:66`. (The P11-removed `moment_casting` gate and its prompt-builder are gone.) This is **not dead code** — see Anomalies for how this is distinguished from genuine dead code in this cluster.

`llm.claude_json` (the bare-dict wrapper) has zero repo callers per call graph EXCEPT `fanops.responder._default_claude_model` calls `claude_json_meta` (not `claude_json`) directly — `claude_json` itself is used by `studio/actions.py:139` (`model = claude_json`, for the Studio's manual "regenerate caption" action), confirmed by grep, outside this cluster.

### Hashtag vetting pipeline (`hashtags.vet_hashtags` / `vet_hashtags_traced`)

```
ingest_captions (caption.py:328,344)
   │  tags = item.hashtags or _tags_in(item.caption)     # model's raw picks (fallback: regex-extract #tags from caption text)
   │  plat = _platform_for_surface(...)                  # AGENT-6: the REQUESTED platform, not a re-parse
   │  store = load_store(cfg)                            # M4 live Graph-reach store, or None -> frozen pools
   │  corpus = surface_corpus.get(item.surface)           # B1: per-persona curated pool (the account differentiator)
   │  content = content_tags                              # per-clip transcript-derived candidates
   ▼
hashtags.vet_hashtags_traced(tags, plat, language, store=, corpus=, content=)
   │  delegates to vet_hashtags(...) then labels each kept tag's provenance
   ▼
hashtags.vet_hashtags(tags, platform, language, max_tags=4, store=, corpus=, content=)
   1. vetted membership = (store or VETTED) | corpus_norm | content_norm   # corpus+content JOIN the gate
   2. rank = store-rank-or-frozen-_RANK, with corpus/content tags FLOATED ahead (preference order: corpus > content)
   3. kept = ALL of corpus_norm first (uncapped at this stage)
      + every model tag that is in `vetted` (dedup)
   4. kept.sort(key=rank)                                  # reach order
   5. RESERVED FLOORS (evaluated against kept[:max_tags], the cap window):
        - region floor: if corpus_norm and language is Arabic and no #arabic* tag survives the cap -> force one in
        - content floor: if content_norm and no content tag survives the cap -> force one in
      (each reserved tag displaces the lowest-ranked non-reserved tag from the tail)
   6. BACKFILL (only runs while len(kept) < max_tags): corpus_norm, then one platform discovery tag
      (#fyp/#reels/#foryou, ONLY if corpus_norm present), then store (if present), then the frozen
      `_composition(platform, language)` balanced-4 sequence, then content_norm — first-not-seen wins
   7. return kept[:4]                                      # HARD CAP, cited hashtags.py:206
   ▼
hashtags.vet_hashtags_traced adds provenance: content > corpus > region > graph-reach > discovery > genre-floor
   (hashtags.py:211-222, `_tag_source`)
```

### Hook-purity gate (mechanical floor + read-only quality meter)

```
moments.ingest_moment_hooks (C4 cluster, outside this file list) calls:
   hookcheck.is_weak_hook(hook_text, used, cluster_scope=...)     # the ONLY gate — MECHANICAL only
       rejects: empty hook | exact case/space-insensitive duplicate | opening-3-word-template cluster (>=4th match)
   caption.brand_risk_flag(hook_text, cfg)                        # off-brand/begging/bravado-guardrail regex
       (function-local import from fanops.caption inside moments.py, per C4's notes)
   text.sanitize_generated_text(hook_text)                        # strips em-dashes/curly quotes/zero-width chars

hookscore.narration_signature / hook_quality / log_hook_quality  — READ-ONLY SCOREBOARD, never a gate.
   Measures third-person-narration rate on ALREADY-SHIPPED hooks (decided moments) for observability;
   changes nothing about what ships.
```

## Per-file breakdown

### `caption.py` — the caption/hashtag agent gate (request → LLM-answered → vet → store)

- `logger` (module var) — `logging.getLogger(__name__)`.
- `_TAG_RE` (module var, compiled regex `#\S+`) — used by `_tags_in`.
- `VARIATION_AXES = ("hook_string", "caption_angle", "hook_placement")` (module var) — the P2 "cheap-text" axes a variant may legitimately move; render-expensive frame/length axes are out of scope for this dormant machinery.
- `normalize_variation_axis(value) -> str | None` — pure: canonicalizes a free-text axis label (case/space/dash-insensitive) to one of `VARIATION_AXES`, or `None` if unrecognized (never crashes on a bad label). **No callers found anywhere in `src/`** (dead code candidate — part of the dormant creative-variation A/B machinery).
- `coherent_variation(hook, rationale, *, siblings=frozenset()) -> bool` — pure: T2 coherence gate for the dormant variation A/B loop — requires a non-empty rationale AND a non-empty hook that clears `is_weak_hook` against `siblings`. Docstring notes `is_weak_hook` no longer judges QUALITY (moved to a reasoning critic that doesn't run on caption siblings), so a quality-weak variant can now pass. **No callers found anywhere in `src/`** (dead code candidate — dormant variation machinery, per module comments at lines 432-436 this is a documented `/ecc:prp-plan` follow-up, not yet wired).
- `_tags_in(caption: str | None) -> list[str]` — pure: regex-extracts `#tag` tokens from a caption string; the fallback source of hashtags when the model's structured `hashtags` array is empty. Called by `ingest_captions`.
- `_platform_of(surface: str, *, cfg: Config | None = None) -> Platform` — pure-ish (logs via `get_logger` when `cfg` given): parses the platform tail of an `account/platform` surface key; unknown/malformed tail coerces to `Platform.instagram`, LOUDLY logged (`platform_coerced` breadcrumb) when `cfg` is threaded in — never crashes on a typo'd key. Called by `_platform_for_surface`.
- `_risk_re(cfg: Config | None) -> re.Pattern` — pure: resolves the effective brand-risk regex — the precompiled default `_RE` with no cfg/no tuning override, else compiles from `cfg.tuning()["offbrand_en"/"offbrand_ar"]` (operator override REPLACES, not appends, the corresponding language's list); a malformed override regex falls back to the default; both lists emptied -> a never-matching pattern. Called by `brand_risk_flag`.
- `brand_risk_flag(caption: str, cfg: Config | None = None) -> str | None` — pure: returns a human-readable reason string if the caption/hook matches the (possibly operator-overridden) off-brand regex, else `None`. Called by `caption.ingest_captions`, `moments.ingest_moment_hooks` (C4, hook-purity gate — function-local import to avoid a module cycle), `studio.actions.regenerate_caption`.
- `_surface_str(account: str, platform: Platform) -> str` — pure: builds the documented `"account/platform"` lookup key. Called by `caption_request_stale`, `request_captions`.
- `caption_request_stale(cfg: Config, clip_id: str, want_surfaces: list[tuple[str, Platform]]) -> bool` — pure read: True if the on-disk caption gate must be reopened — no request file yet, or the requested surface set no longer matches `want_surfaces` (e.g. new IG surfaces added after a TikTok-only request); a currently-pending-answer request is NOT stale. Reads the request JSON via `_request_surfaces`; any read error -> stale=True (fail toward re-asking). Called by `pipeline._stage_refresh_caption_requests`.
- `_lang_base(tag: str | None) -> str | None` — pure: normalizes an IETF-ish language tag to its base subtag (lowercase, strip, split on `-`/`_`) for comparison — AUDIT H5 hardening so `en-US`/`EN`/`en-GB` are not false-flagged as a language mismatch against a plain `en` source. Called by `ingest_captions`.
- `_learned_hooks(led: Ledger, cfg: Config, surfaces) -> list[str]` — creative-variation v2 read: when `cfg.variant_learning` is on, asks the gated scorer (`ucb_rank` if `cfg.variant_ucb` else `best_hooks`, both from `fanops.variant_learning`) for each surface's trustworthy winning hook, returns the de-duplicated union in insertion order. Gated OFF by default -> `[]`. **Fail-open**: any exception is logged once (`logger.warning(..., exc_info=True)`) and yields `[]`. Called by `request_captions`.
- `_transferred_hooks(led: Ledger, cfg: Config, accounts, surfaces) -> list[str]` — cross-surface transfer read (v2 follow-up): when `cfg.variant_transfer` is on AND `accounts` is not `None`, gated further by `learning_validated(cfg)` (VALIDATION-FROZEN — never biases on an unconfirmed lift), asks `fanops.variant_transfer.transferred_hooks` per surface for a borrowed STYLE, returns the de-duplicated union. **Fail-open**: any exception logged once, yields `[]`. Called by `request_captions`.
- `request_captions(led: Ledger, cfg: Config, clip_id: str, surfaces, accounts=None) -> Ledger` — **writes the caption gate request** (`write_request(cfg, kind="captions", ...)`). Builds: `learned`/`transferred` hook hints; per-surface `personas` dict (`caption_directive(a)` from `fanops.personas`); per-surface `corpora` dict (B1 — the per-account hashtag differentiator, `a.hashtag_corpus`); per-clip `content_tags` (`hashtags.content_tag_candidates(moment.transcript_excerpt)`). Assembles the full payload (clip_id, transcript_excerpt, language, guidance, content_tags, surfaces w/ persona+corpus, learned_hooks, learned_hooks_transferred — each key omitted when empty so old on-disk contracts stay byte-identical). Sets `ClipState.captions_requested`. **Side effect**: writes a request control file; mutates `led` clip state. Called by `pipeline._stage_refresh_caption_requests`, `pipeline._stage_render_and_caption`.
- `_request_surfaces(cfg: Config, clip_id: str) -> tuple[set, dict, dict, list]` — pure read: reparses the on-disk caption request JSON as the SOURCE OF TRUTH for which surfaces were asked, their per-surface corpus, their per-surface REQUESTED platform (AGENT-6 — not a re-parse of the model's echoed string), and the clip-level `content_tags`. Called by `caption_request_stale`, `ingest_captions`.
- `_platform_for_surface(surface: str, surface_platform: dict, *, cfg: Config | None = None) -> Platform` — pure: AGENT-6 — prefers the REQUESTED platform recorded in the request JSON; falls back to `_platform_of` (legacy tail-parse) only when the request predates this field. Called by `ingest_captions`.
- `_caption_entry(tags, hashtags_raw, *, fallback=False, tag_sources=None) -> dict` — pure: builds one `meta_captions` entry — `caption` (space-joined vetted tags), `hashtags` (the vetted list), `hashtags_raw` (the model's raw picks, display-only), `hook`/`axis`/`rationale` always `None` (the caption gate no longer authors hooks — the moment gate does), `tag_sources` (per-tag provenance dict), and a `fallback: True` marker for seed-tag-synthesized entries. Called by `ingest_captions`.
- `ingest_captions(led: Ledger, cfg: Config, clip_id: str) -> Ledger` — **reads the gate response** (`read_response(cfg, "captions", clip_id, CaptionSet)`; `None` -> pending/stale, no-op). Validates: (1) AUDIT H6 — any caption naming an unrequested surface holds the WHOLE clip with a specific reason; (2) AUDIT H5 — a declared-language mismatch (base-subtag compare) holds the clip; (3) `brand_risk_flag` on the raw caption, held (first match wins, but hashtag-vetting for every item still runs); (4) vets hashtags per item via `vet_hashtags_traced` under the REQUESTED platform, storing `meta_captions[surface]`; (5) for any surface the model left unanswered (SEED-TAG FALLBACK — replaces the old hold-on-missing behavior that silently buried ~83% of a rap catalogue on soft LLM refusals), synthesizes a reach-vetted seed-tag-only entry with no hook and logs `caption_fallback_seed`. Ends in `ClipState.held` (with reason) or `ClipState.captioned` (clearing any stale hold). **Side effects**: mutates clip state/meta_captions/held flags; logs. Called by `pipeline._stage_ingest_captions`.

### `hashtags.py` — reach-vetted hashtag selection (the vetting engine)

- `_MEGA`, `_RELEVANCE`, `_ARABIC`, `_DISCOVERY`, `_DISCOVERY_DEFAULT` (module vars) — the frozen reach-ranked seed pools: mega genre tags (`#hiphop`/`#hiphopmusic`/`#rap`, ~504M/113M/113M posts per the June 2026 research cited in the header), relevance tags, Arabic-language tags, per-platform discovery tags (tiktok/instagram), and a platform-neutral discovery fallback.
- `_RANK` (module var, dict) — canonical reach rank across all frozen pools (mega tags rank first), used to order kept tags when no live `store` is supplied.
- `VETTED` (module var, `set`) — the membership set: union of `_MEGA`/`_RELEVANCE`/`_ARABIC`/all `_DISCOVERY` values. A model-returned tag survives only if it's in this set (or the live store, or corpus/content).
- `_STOPWORDS`, `_WORD` (module vars) — the stopword set and word-token regex (`[a-z][a-z0-9]{2,19}`) used by `content_tag_candidates`; stopwords explicitly include URL/tech tokens (http/mp3/png/etc.) so a transcript-frequent non-hashtag word can't force its way into the posted line.
- `_ARABIC_SET`, `_DISCOVERY_SET` (module vars) — set views of `_ARABIC`/all-discovery-tags, used by `_tag_source`.
- `load_store(cfg) -> list[str] | None` — reads `00_control/hashtags.json` (`{"tags": [...]}`), normalizes; absent/corrupt/empty -> `None` (fail-open to frozen pools, never raises). Written by `fanops_hashtags.refresh_store` from live Meta Graph reach. Called by `caption.ingest_captions`, `persona_directives.persona_facts`, `persona_research.discover_corpus`/`research_corpus`, `studio.views.personas_page`.
- `load_store_reach(cfg) -> dict[str, float]` — reads the `{"reach": {tag: score}}` half of the same store file — the per-tag LIVE Graph reach number the Studio surfaces next to each curated tag. Absent/corrupt/no-`reach`-key -> `{}`, never raises. Called by `studio.views.personas_page`.
- `vetted_menu(store: list[str] | None = None) -> list[str]` — pure: the flat, reach-ordered, deduped tag list shown to the model as its pick MENU. With a `store`, the store IS the menu; else the frozen pools in rank order. Note: this is a GUIDE for the prompt, not the enforcement — `vet_hashtags` still hard-caps/filters regardless of what the model picks. Called by `fanops_hashtags.refresh_store` (as the cold-start floor seed), `persona_research.research_corpus`, `prompts.caption_prompt` (the literal menu text), `studio.views.personas_page`.
- `_norm(tag: str) -> str` — pure: canonicalizes one tag (strip, lowercase, exactly one leading `#`); `""` in -> `""` out. Called throughout the file plus `fanops_hashtags._seed_tags`, `meta_graph.discover_candidates`/`harvest_cooccurring`, `persona_research.discover_corpus`/`research_corpus`, `persona_store.add_corpus_tag`/`remove_corpus_tag`, `studio.views.personas_page`.
- `_dedupe_norm(seq) -> list[str]` — pure: normalizes + dedupes a tag sequence (corpus/content input), preserving first-seen order; non-str entries silently skipped. Called by `vet_hashtags`, `vet_hashtags_traced`.
- `content_tag_candidates(text: str | None, *, max_n: int = 6) -> list[str]` — pure, deterministic, NO NLP model: lowercase Latin word tokens (3-20 chars) from `text`, minus stopwords, ordered by frequency-desc-then-first-seen, normalized to `#tag`, deduped, capped at `max_n`. Blank/non-str/non-Latin (e.g. Arabic)/numbers-only text -> `[]` (byte-identical to pre-feature behavior for those cases). Called by `caption.request_captions`.
- `_composition(platform: Platform, language: str | None) -> list[str]` — pure: the balanced-default-4 backfill sequence — one mega tag, one relevance tag, one language/region-or-second-music tag (Arabic if the clip's language starts with `ar`, else `#newmusic`), one platform discovery tag, then the remaining mega/relevance/discovery tags as further backfill. Called by `vet_hashtags`.
- `vet_hashtags(tags, platform, language=None, max_tags=4, *, store=None, corpus=None, content=None) -> list[str]` — **the selection algorithm** (traced in detail below). Pure, deterministic, never empty (the frozen composition always fills). Called by `hashtags.vet_hashtags_traced`, `persona_directives.persona_facts`.
- `_tag_source(tag, *, content_set, corpus_set, store_set) -> str` — pure: the provenance label for one shipped tag, priority order content > corpus > region > graph-reach > discovery > genre-floor (never empty — genre-floor is the catch-all). Called by `vet_hashtags_traced`.
- `vet_hashtags_traced(tags, platform, language=None, max_tags=4, *, store=None, corpus=None, content=None) -> tuple[list[str], dict[str, str]]` — same selection as `vet_hashtags` (calls it, DRY) plus a per-tag `source` provenance map. Called by `caption.ingest_captions`.

### `fanops_hashtags.py` — the periodic reach-store builder (CLI-invoked, not part of the caption request/response path)

- `_seed_tags(cfg: Config) -> list[str]` — reads every `Persona.hashtag_corpus` + `intake["genre"]` word, normalizes+dedupes. Absent/empty `personas.json` -> `[]` (legitimate no-seeds, frozen floor still stands). A CORRUPT `personas.json` is NOT swallowed here — `Personas.load`'s `ControlFileError` propagates uncaught, deliberately, so the caller (`refresh_store`) can distinguish "no personas" from "corrupt control file" and abort rather than silently rebuilding a generic store over a curated one. Called by `refresh_store`.
- `refresh_store(cfg: Config, *, get=None, now=None) -> dict` — **the store-rebuild entry point**. If `cfg.hashtag_trends` is off, writes just the frozen `vetted_menu()` seed and returns early (operator escape hatch). Else: reads seeds via `_seed_tags` (catches `ControlFileError` specifically -> **ABORTS without writing**, returns `{"written": False, "aborted": "corrupt_personas", "reason": ...}`, preserving the existing curated store); harvests co-occurring candidate tags via `meta_graph.harvest_cooccurring(cfg, seeds, ...)`; builds a `universe` (harvested-by-count, then seeds, then frozen seed, deduped); measures LIVE Graph reach for the universe via `meta_graph.sample_trends(cfg, universe, ...)` (30/7-day budget, fail-open — empty dict on no creds/fetch miss); ranks measured tags by reach descending, then appends unmeasured universe tags in relevance order; writes `00_control/hashtags.json` (`{"tags": [...], "reach": {...}}`). **Side effect**: disk write (unless aborted). Called by `cmd_hashtags_refresh`, `refresh_store_if_due`.
- `refresh_store_if_due(cfg: Config, *, max_age_s: int = 43200, get=None, now=None) -> dict` — the constant-update hook the autonomous run loop calls each tick. No-ops (`{"refreshed": False, "reason": "no Meta creds"}`) without `cfg.meta_graph_token`/`cfg.meta_ig_user_id`. Throttles via the store file's mtime vs `max_age_s` (default 12h = 43200s). **Fail-open**: wraps the whole body in `try/except Exception as exc: return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}` — must never crash the unattended run loop. Called by (per project CLAUDE.md) `fanops run`'s per-tick daemon path — no direct in-repo caller found by grep beyond its own module (confirms it is invoked from the CLI/daemon loop, consistent with the "periodic discover/refresh_store CLI-invoked path" the task description references).
- `cmd_hashtags_refresh(cfg: Config) -> int` — `fanops hashtags refresh` CLI verb: calls `refresh_store`, prints a summary, returns exit 0 on success. The ONE non-zero exit (2) is the corrupt-personas abort case — prints the abort reason loudly to stderr and leaves the curated store untouched. Called by `cli._dispatch`.
- `cmd_hashtags_discover(cfg: Config) -> int` — `fanops hashtags discover` CLI verb: for EVERY persona, calls `personas.discover_corpus(cfg, per.id)` (live Meta Graph co-occurrence harvest) and PRINTS the fresh tags found — this is the periodic "what's new in our niches" REPORT the CLAUDE.md describes. **Read-only w.r.t. the caption menu**: never writes anything; curation stays operator-gated in the Studio Personas tab. Personas-unreadable/no-personas/per-persona discovery errors are each caught and reported individually (fail-open per persona, never aborts the whole command); always exits 0. Called by `cli._dispatch`.

Confirms the task's framing: `fanops_hashtags.py` is NOT part of the request/response caption gate at all — it's an independent, periodically-invoked (CLI verb or daemon-tick) store-refresh/report utility that the caption path passively consumes via `hashtags.load_store(cfg)`.

### `tagging.py` — subtle, non-synchronized `@mohflowmusic` artist tagging (adjacent to, not part of, the caption/hook/hashtag core)

- `ARTIST_HANDLE = "@mohflowmusic"` (module var).
- `should_tag(clip_id: str, account: str, *, rate: float = 0.25) -> bool` — pure, deterministic probabilistic gate: hashes `f"{clip_id}|{account}"` via SHA-1, maps to `[0,1)`, compares against `rate`. Same (clip, account) pair always yields the same decision (deterministic, not per-account-constant — varies by clip so no account is permanently un-tagged). Called by `decide_tag`.
- `decide_tag(led: Ledger, *, account: str, clip_id: str = "", when: datetime, rate: float = 0.25, min_gap_minutes: int = 120, force: bool = False) -> bool` — the stateful decision: short-circuits `False` unless `force` or `should_tag` passes; then checks EVERY recorded tag time in `led.tag_log` (keyed per `(account, clip_id)` — AUDIT H3 fix, so a re-tag never overwrites/erases another account's de-clustering timestamp) for a gap under `min_gap_minutes`; if none found within the gap, records `led.tag_log[f"{account}|{clip_id}"] = when.isoformat()` and returns `True`. Explicitly does NOT prune old entries by `when` (crosspost evaluates surfaces out of chronological order, so pruning would break a still-needed de-cluster comparison — documented as a real bug found in testing). **Side effect**: mutates `led.tag_log` on acceptance. Called by `crosspost._mint_surface_post` (outside this cluster).

### `hookcheck.py` — the mechanical hook-purity floor (the sole gate after authorship)

- `_TEMPLATE_PREFIX_TOKENS = 3`, `_TEMPLATE_CLUSTER_MAX = 3` (module vars) — the opening-template clustering constants. Docstring records a v2.1 tuning history: 2 tokens/max 2 over-fired (killed distinct hooks sharing only a 2-word opener); 3 tokens/max 3 catches real ×6 templates while letting genuine variation through.
- `_prefix_key(text: str) -> tuple` — pure: the first `_TEMPLATE_PREFIX_TOKENS` (3) word tokens of a lowercased hook, as a tuple key. Called by `is_weak_hook`.
- `is_weak_hook(text: str | None, used: set[str] = frozenset(), *, cluster_scope: set[str] | None = None) -> bool` — **THE hook-purity gate**, MECHANICAL ONLY (traced in detail below). Called by `caption.coherent_variation`, `moments.ingest_moment_hooks` (C4 — the real hook-authoring gate).

### `hookscore.py` — the read-only hook-quality scoreboard (measurement, never a gate)

- `_VIEWER` (module var, compiled regex) — matches viewer-address markers: `you|your|youre|u|ur|pov|imagine`, a contraction (`'re`/`'ll`), or a `?`.
- `_IMPERATIVE_OPEN` (module var, compiled regex) — matches a viewer-directed imperative opener: `wait|watch|listen|stop|don't|play|tell me|name|find` at the start of the string.
- `_THIRD_PERSON` (module var, compiled regex) — matches third-person pronoun subjects/objects: `he|him|his|she|her|hers|they|them|their|theirs`.
- `narration_signature(text: str | None) -> bool` — pure: True iff `text` reads as third-person scene-narration with NO viewer address — i.e., no `_VIEWER`/`_IMPERATIVE_OPEN` match AND a `_THIRD_PERSON` match. High-precision by design (accepts misses over false-positives). Docstring is explicit: **NEVER a gate** — RF5 removed the earlier post-generation perspective strip; perspective is now owned at authorship (the generator prompt), and this function is only a read-only meter (`hook_quality`) plus a poisoned-winner filter for `moment_hook_learning.proven_hook_styles` (outside this cluster). Called by `hook_quality`, `moment_hook_learning.proven_hook_styles`.
- `hook_quality(led: Ledger) -> dict` — pure read, no LLM/network/ledger write: over all `decided` moments, computes `{decided, with_hook, null, viewer_pov_rate}` where `viewer_pov_rate = 1.0 - (narrated_count / with_hook_count)`, defaulting to `1.0` (vacuously full POV, no div-by-zero) when no hooks shipped. Called by `log_hook_quality`.
- `log_hook_quality(led: Ledger, cfg: Config) -> dict` — thin wrapper: computes `hook_quality`, emits one digest log line via `get_logger(cfg)`, returns the dict. Called by `pipeline.advance`.

### `text.py` — deterministic AI-tell sanitizer + safe-URL guard (shared utility, not caption/hook-specific logic)

- `_DASHES`, `_SQUO`, `_DQUO`, `_ZEROWIDTH` (module vars, compiled regexes) — em/en/figure/horizontal-bar dashes, curly single quotes, curly double quotes, zero-width/joiner/BOM characters.
- `safe_public_url(url: str | None) -> str | None` — pure: returns `url` unchanged iff it's a well-formed `https://` URL with a host and no internal whitespace, else `None`. Guards a malformed/non-https backend-captured permalink (Postiz `releaseURL` / hosted `publicUrl`) from being persisted and later surfaced as a dead "live URL." Explicitly NOT applied to operator-supplied URLs (`fanops resolve --url`, Studio mark-posted) — those are explicit operator intent. Called by `post.postiz.PostizPoster.publish`, `post.zernio.ZernioPoster.publish`, `reconcile._norm_permalink`, `reconcile.reconcile_posts` (all outside this cluster).
- `sanitize_generated_text(text: str | None, *, max_words: int | None = None) -> str | None` — pure, idempotent, None-safe: strips em/en-dashes to `", "`, straightens curly quotes, converts NBSP to regular space, drops zero-width characters, collapses whitespace runs, optionally trims to `max_words` AFTER cleanup (so the trim boundary can't leave a dangling comma). Applied to ALL LLM/transcript-derived text before storage/on-screen burn — the hard guarantee behind the prompt's "no em-dash" instruction, enforced regardless of model compliance. Called by `moments.ingest_moment_hooks`, `moments.ingest_moments` (both C4, outside this cluster).

### `prompts.py` — the committed prompt-builder catalog (one function per agent-gate kind)

- `_FENCE_TAG` (module var, compiled regex) — matches any `<brand_brief>`/`</brand_brief>` tag (case/space-tolerant) so a crafted `context.md` can't forge a fence-closing tag.
- `_brief_fence(guidance) -> str` — pure: wraps operator brand guidance (from `context.md`) in a delimited `<brand_brief>...</brand_brief>` fence. Called by the three live prompt builders (`caption_prompt`, `moment_hook_prompt`, `moment_pick_prompt`).
- `_inline(s) -> str` — pure whitespace collapse for prompt hardening. Called by `moment_hook_prompt`, `moment_pick_prompt`.
- `_MAX_TARGET_PICKS = 30` (module var) — the hard CEILING (never a quota) on how many clip picks a single source's moment-pick pass may request.
- `_target_pick_count(duration: float, band: Band = TALK) -> int` — pure: computes how many non-overlapping clips to aim for, by source length and content-type band. `<=0` duration -> `0` (no target, model decides); below the band floor -> `1` (one whole-source clip); else `round(duration / band.span)`, floored at 1, capped at `_MAX_TARGET_PICKS`. Called by `moment_pick_prompt`.
- `_hook_spec(max_words: int = 6) -> str` — pure: the SHARED on-screen-hook craft specification (retention psychology: ~70% watch muted, decide in <3s; the 4 proven triggers — curiosity gap, pattern interrupt, self-relevance, emotional arousal — plus 5 situational mechanisms, force multipliers, worked examples, banned anti-patterns, bilingual guidance, output constraints). Reused verbatim by both `moment_hook_prompt` (the vision hook author) and (per its own docstring reference) `caption_prompt`'s dormant variant machinery is NOT wired to it — confirmed `caption_prompt` does not call `_hook_spec` (grep of `prompts.py` shows only `moment_hook_prompt` calls it); the caption gate is hashtags-only now (see caption.py notes). Called by `moment_hook_prompt`.
- `_hook_decision(has_frames: bool = True) -> str` — pure: the moment-only hook SELECTION logic (read visual energy from frames / infer from transcript if no frames, read the audio transient from signal peaks, read register/dialect from the brand brief, then select ONE of 3 situational mechanisms). Deliberately kept separate from `_hook_spec` so the caption author (which has no frames/signal-peaks) is never instructed to read inputs it lacks. Called by `moment_hook_prompt`.
- `moment_pick_prompt(payload: dict) -> str` — **M1b PASS 1 prompt builder**: assembles the window-picking-only prompt — source duration, hard rules (in-bounds timestamps, target band length via `band_for(payload.get("clip_profile"))`, short-source exactly-one-pick rule, target-count ceiling via `_target_pick_count`, non-overlap, a `reason` requirement, frame-viewing instruction, signal-peak guidance, empty-list-only-for-dead-footage rule), the `_brief_fence`, language, and the JSON-serialized transcript + signal peaks (with a truncation note if the transcript was budget-bounded). Called (indirectly, via `responder._PROMPT["moments"]`) by `responder._default_claude_model`. Zero AST-traced callers per call graph (dict-dispatch, see Pipeline overview).
- `moment_hook_prompt(payload: dict) -> str` — **M1b PASS 2 prompt builder**: frame-grounded on-screen hook for ONE owner-moment (owner persona from payload). Called via `responder._PROMPT["moment_hooks"]`.
- `caption_prompt(payload: dict) -> str` — **the text-only caption/hashtag prompt builder**. Called via `responder._PROMPT["captions"]`, and by `studio.actions.regenerate_caption`.

> **Removed P11:** the casting prompt-builder, `_casting_moment_line`, `_data_fence` (casting LLM gate deleted).

### `llm.py` — the `claude -p` shell wrapper (the actual API-calling primitive)

- `logger` (module var) — `logging.getLogger("fanops.llm")`.
- `_sleep = time.sleep` (module var) — indirection so tests can stub the backoff wait.
- `LlmTimeoutError(RuntimeError)` — class; a distinct type so the responder can retry a timeout (usually transient) rather than treating it as a hard failure.
- `LlmRateLimitError(RuntimeError)` — class; raised when `claude -p` stays rate-limited (429/503/529) across all backoff retries — typed so a sustained rate limit fails LOUDLY instead of silently producing nothing.
- `LlmContextLimitError(RuntimeError)` — class; AGENT-2 — raised when `claude -p` rejects a request as too large for context, so the responder can turn a payload-too-big failure into a visible degraded gate state instead of an infinite-pending wedge.
- `_CONTEXT_LIMIT_MARKERS` (module var, tuple of strings) — substrings (`"prompt is too long"`, `"context length"`, `"exceeds the maximum"`, `"too many tokens"`, `"maximum context"`) that identify a context-limit failure in the CLI's stderr/stdout body.
- `_is_context_limit(text: str) -> bool` — pure: True iff any `_CONTEXT_LIMIT_MARKERS` substring appears (case-insensitive) in `text`. Called by `claude_json_meta`.
- `_RATELIMIT_STATUSES = {429, 503, 529}`, `_MAX_RL_RETRIES = 4`, `_RL_BASE_DELAY = 2.0` (module vars) — the retryable HTTP statuses, max retry count (total attempts = 5), and base jittered-exponential backoff delay in seconds.
- `_rate_limit_status(returncode: int, stdout: str) -> int | None` — pure: True iff a nonzero return code carries a JSON envelope on stdout with `api_error_status` in `_RATELIMIT_STATUSES`. A nonzero exit WITHOUT such an envelope is a hard failure (auth, bad args) — never retried. Called by `claude_json_meta`.
- `_frames_unread(env: dict) -> bool` — pure: HOOK-TRANSPORT check — True iff the response envelope's `num_turns` is an int `<= 1` (a pure single-shot text answer with no tool turn — Read is the only granted tool, so `num_turns<=1` proves the attached frames were never opened). Absent/non-int `num_turns` (older CLI / synthetic test envelope) -> `False` (unverifiable, treated as read, never falsely re-asks). Called by `claude_json_meta`.
- `claude_json_meta(prompt: str, schema: dict, *, timeout: float = 300.0, images: list[str] | None = None, model: str | None = None) -> tuple[dict, str | None, bool]` — **THE ACTUAL LLM-CALLING PRIMITIVE.** Builds the `claude -p --output-format json --json-schema <schema> --allowedTools <"Read" if images else ""> --strict-mcp-config [--model <model>]` argv; runs it via an inner `_run(stdin_prompt)` closure that shells `subprocess.run` with the prompt piped on **stdin** (not argv — ECC fix #11, avoiding `ps`/`/proc/<pid>/cmdline` leakage of the transcript/brand-guidance and avoiding `ARG_MAX`/E2BIG on a large transcript), wrapped in a jittered-exponential rate-limit retry loop (`_rate_limit_status`-gated, up to `_MAX_RL_RETRIES`=4 retries, raising `LlmRateLimitError` on exhaustion). Absent binary -> `ToolchainMissingError`; hang past `timeout` -> `LlmTimeoutError`; nonzero non-rate-limited exit -> `LlmContextLimitError` (if `_is_context_limit` matches the error body) or generic `RuntimeError`; unparseable stdout -> `RuntimeError`. When `images` is given: sends a first attempt instructing the model to read the frames via the Read tool, checks `_frames_unread`; if unread, re-asks ONCE more forcibly; if STILL unread after the re-ask, proceeds anyway but sets `frames_unread=True` (surfaced to the responder as a degraded, text-grounded-not-frame-grounded breadcrumb, AGENT-9) and logs a warning. Prefers `structured_output` from the response envelope, falls back to `json.loads(env["result"])`. Returns `(parsed_dict, model_that_answered_or_pinned_fallback, frames_unread)`. Called by `responder._default_claude_model`.
- `claude_json(prompt: str, schema: dict, *, timeout: float = 300.0, images: list[str] | None = None, model: str | None = None) -> dict` — thin wrapper: `claude_json_meta(...)[0]` — the bare-dict contract preserved for callers that don't need provenance, notably `studio/actions.py` which binds `model = claude_json` for the manual "regenerate caption" Studio action. **No repo callers found per call graph directly calling this by name inside `llm.py`'s own module scope** (the real caller `studio/actions.py:139` is outside this cluster, confirmed by grep — not dead code).

## Hashtag selection algorithm trace + hook-purity gate trace

### Hashtag algorithm (`hashtags.py:140-206`, `vet_hashtags`)

**Cap**: `max_tags: int = 4` (default parameter, `hashtags.py:141`) — the operator's HARD rule, enforced by the final `return kept[:max_tags]` at `hashtags.py:206`.

**Candidate sources, in the order they enter the algorithm:**
1. `corpus` (B1, per-persona curated pool) — `hashtags.py:170-172`: the ENTIRE corpus is seeded into `kept` FIRST, uncapped at this stage (may exceed 4), so a corpus tag ranked past the eventual cap remains eligible for the region-floor promotion.
2. `tags` (the model's raw picks, or `_tags_in(caption)` fallback from `caption.py`) — `hashtags.py:174-177`: only tags that are members of `vetted = (store or VETTED) | corpus_norm | content_norm` are kept; non-vetted words are silently dropped.
3. Sort by rank — `hashtags.py:178`: `rank` is `base_rank` (live `store` index if present, else the frozen `_RANK` dict) OVERLAID with `preferred` (corpus tags first, then content tags, each assigned negative indices so they float ahead of everything else — `hashtags.py:161-166`).
4. Reserved floors — `hashtags.py:179-193`: evaluated against `kept[:max_tags]` (the cap window, not the full `kept` list): (a) region floor — if `corpus_norm` is non-empty AND `language` starts with `"ar"` AND no Arabic tag survives the cap window, force one Arabic tag into the last reserved slot; (b) content floor — if `content_norm` is non-empty AND no content tag survives the cap window, force one content tag in. Each reserved tag displaces the lowest-ranked non-reserved tag from `kept`'s tail (`head = [...][:max_tags - len(reserved)]`).
5. Backfill (only fires while `len(kept) < max_tags`) — `hashtags.py:198-205`: `corpus_norm` again (any not-yet-kept) + `disc_floor` (one platform discovery tag, gated on `corpus_norm` non-empty) + `store` (if present) + `_composition(platform, language)` (the frozen balanced-4: mega, relevance, language-or-`#newmusic`, discovery, then remaining mega/relevance/discovery) + `content_norm` (content trails reach in the backfill order — the content FLOOR above already guarantees one slot).
6. Final hard cap — `hashtags.py:206`: `return kept[:4]`.

**Ranking/priority summary (highest to lowest precedence for WHICH tags survive and WHERE they land):** corpus (curation) > content (per-clip signal) > live Graph-reach store order (if present) > frozen `_RANK` (mega > relevance > Arabic > discovery) — with two hard-guaranteed reserved tail slots (region, content) that override pure rank when their source category is present but under-represented in the cap window.

**Provenance labeling** (`vet_hashtags_traced` + `_tag_source`, `hashtags.py:211-222,224-237`): every shipped tag gets exactly one label, priority content > corpus > region > graph-reach > discovery > genre-floor (the catch-all for a frozen-pool backfill tag) — so no tag ships without a traceable evidence source.

### Hook-purity gate (`hookcheck.py:30-48`, `is_weak_hook`)

**What makes a hook "weak" (mechanically rejected):**
1. Empty — `hookcheck.py:39-40`: `not text or not text.strip()` -> reject ("nothing to show").
2. Exact cross-clip duplicate — `hookcheck.py:41-43`: case/whitespace-insensitive exact match against `used` (the feed-wide set of already-taken hooks) -> reject ("burning the same line twice reads like a bot").
3. Opening-template cluster — `hookcheck.py:44-47`: the hook's first `_TEMPLATE_PREFIX_TOKENS` (**3**) word tokens (`_prefix_key`) match the same 3-token prefix of at least `_TEMPLATE_CLUSTER_MAX` (**3**) other hooks already in `cluster_scope` (defaults to `used` if not given, but callers narrow it to the current decision batch — one source's picks / one edit run — so the cross-batch/feed-wide opener monotony question is explicitly left to the vision-author prompt, not this floor) -> reject (the "before he was X" ×6 / "wait for the Y" ×6 "reads like a bot" tell).

Everything else passes — **quality is explicitly out of scope** (superlative/hype/narration judgments were deleted in v2, per the module docstring, because as regexes they both over-fired on legible hooks and under-fired on real third-person narration; quality now belongs to the always-on vision-author prompt, with `hookscore.narration_signature` as a read-only post-hoc meter, never a gate).

**What makes a hook "off-brand" (`caption.brand_risk_flag`, `caption.py:116-118`, gate logic in `_risk_re`, `caption.py:95-114`):** matches against `_RE` (`caption.py:93`, precompiled union of `_OFFBRAND_EN` — sorry/pls/please stream/🥺/beg(ging)/official (drop|release)/from the label/link in bio — and `_OFFBRAND_AR` — Arabic equivalents for please/please listen/link in bio/begging/sorry). Operator-overridable per-language via `00_control/tuning.json` keys `offbrand_en`/`offbrand_ar`, which REPLACE (not append to) the corresponding default list; a malformed override regex falls back silently to the default; both lists cleared -> a pattern that never matches (`re.compile(r"(?!)")`, `caption.py:110`). Applied to BOTH the on-screen hook (via `moments.ingest_moment_hooks`, C4) and the caption text (via `caption.ingest_captions`) — the module docstring calls this "FIX F33" for the Arabic-equivalents addition.

## Anomalies found

**Dead code candidates (zero call sites anywhere in `src/`, confirmed via call graph + grep):**
- `src/fanops/caption.py:45` `normalize_variation_axis` — no caller anywhere; part of the dormant P2 creative-variation-axis machinery.
- `src/fanops/caption.py:51` `coherent_variation` — no caller anywhere; the T2 coherence gate for the SAME dormant variation A/B loop. The module's own comment at `caption.py:432-436` (inside `caption_prompt`, actually — cross-referenced in `prompts.py`) documents this is a deliberate, tracked follow-up ("the dormant variation machinery ... is a `/ecc:prp-plan` deeper-fix follow-up next session"), not an oversight.
- `src/fanops/llm.py:180` `claude_json` — call graph shows `called_by_in_repo: []`, but this is a **false positive for "dead"**: grep confirms `src/fanops/studio/actions.py:138-139` does `from fanops.llm import claude_json; model = claude_json`, a real caller outside this cluster's exact file list that the call graph's cross-module resolution apparently missed (or the call-graph JSON's scope only covers callers within the same top-level clustering pass). Flagging as NOT dead, but noting the call-graph data was misleading here — verify against source, not the graph, for this one.

**Not dead code despite zero-caller call-graph entries (dispatch-table false positives, verified real via `responder.py`):**
- `src/fanops/prompts.py:166` `moment_pick_prompt`, `:242` `moment_hook_prompt`, `:361` `caption_prompt` — all show `called_by_in_repo: []` in `call_graph.json` because they are invoked via `responder.py`'s `_PROMPT[kind](payload)` dict-dispatch (`responder.py:46-47,66`), which the AST-based call graph cannot trace back to the literal function names. All three are genuinely load-bearing — this is the exact "check call_graph.json, but verify" trap the C2/C4 reports also flagged for other dispatch-table patterns in this codebase.

**Fail-open exception handlers (all intentional per surrounding comments, not silent-failure bugs — cited for completeness):**
- `src/fanops/caption.py:135` `except Exception:` in `caption_request_stale` — any read/parse error on the on-disk request treated as "stale, re-open the gate" (fails toward re-asking, not silently accepting staleness).
- `src/fanops/caption.py:171` `except Exception:` in `_learned_hooks` — logged via `logger.warning(..., exc_info=True)` before returning `[]`; documented as "a learning failure can never block a caption."
- `src/fanops/caption.py:196` `except Exception:` in `_transferred_hooks` — same pattern, logged then `[]`.
- `src/fanops/fanops_hashtags.py:100` `except Exception as exc:` in `refresh_store_if_due` — catches everything, returns a truncated reason string; explicitly must never crash the unattended daemon tick.
- `src/fanops/fanops_hashtags.py:130` `except Exception as exc:` in `cmd_hashtags_discover` — personas-unreadable case, prints and returns 0 (never a non-zero exit for a read-only report command).
- `src/fanops/fanops_hashtags.py:137` `except Exception as exc:` in `cmd_hashtags_discover`'s per-persona loop — one persona's discovery error is caught individually so it doesn't abort the report for the remaining personas.
- `src/fanops/llm.py:62` `except Exception:` in `_rate_limit_status` — any envelope-parse failure -> `None` (not a rate-limit signal), falls through to the hard-failure path, which is itself surfaced (not swallowed).
- `src/fanops/llm.py:141` `except Exception as e:` in `claude_json_meta`'s `_run` — stdout-JSON-parse failure re-raised as a typed `RuntimeError` with the raw output tail (not swallowed — converted to a louder, typed error).
- `src/fanops/llm.py:176` `except Exception as e:` in `claude_json_meta` — the `result`-string JSON-parse fallback, same pattern: re-raised as a typed `RuntimeError`, not swallowed.

None of these 9 `except Exception` blocks is a genuinely silent failure — every one either logs, returns a documented fail-open sentinel, or re-raises a more specific typed error. This matches the discipline C2/C4 observed elsewhere in the codebase.

**No bare `except:`** in any of the 9 files (grep confirmed zero hits).

**No TODO/FIXME/XXX markers** found in any of the 9 files (grep confirmed zero hits).

**Prompt-injection hardening present, not a gap**: `prompts.py`'s `_brief_fence`/`_data_fence`/`_inline` (AGENT-3, RF5-documented) are defense-in-depth against a crafted `context.md` brand brief or a prior gate's model-written free text forging a fence-closing tag or a new flush-left instruction line. The `_brief_fence` docstring is explicit that this is a *mitigation*, not a hard guarantee, for the one channel (the brand brief) that viewer-POV starvation cannot fully neutralize — this is an honestly-documented residual risk, not an unrecognized one.

**One real design note worth flagging (not a bug)**: `hashtags.vet_hashtags`'s reserved-floor logic (`hashtags.py:179-193`) evaluates against `kept[:max_tags]` (the cap WINDOW) rather than the full `kept` list — the code comment explicitly explains this is deliberate ("the model's own AR/content tag may be in `seen` but sorted PAST the cap"), so a region/content tag that technically made `kept` but got pushed past position 4 by higher-ranked corpus/store tags is still correctly detected as "not surviving" and gets force-promoted. Verified correct by re-reading the logic; flagged here only because it is subtle enough that a naive re-implementation could easily check `seen` instead of `kept[:max_tags]` and silently break the floor guarantee.
