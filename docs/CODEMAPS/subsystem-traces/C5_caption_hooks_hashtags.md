> Refreshed 2026-09-03 (Plan B SA-B6) — caption/hook/LLM half only. Live ship path:
> `docs/CODEMAPS/hashtag-lifecycle.md` (`ship_from_lock`, source lock). Vendor `content` wire:
> `src/fanops/post/CLAUDE.md` (`posted_text_for` / `compose_posted_caption`).
> **Out of scope here:** scrape/measurement stack (`fanops_hashtags.py`, `ig_*_scrape.py`) — Plan A codemap.

# C5: Caption, Hooks & Hashtags (Plan B — caption/hook/LLM half)

## Scope boundary

This trace covers the **caption gate, hook floors, hashtag curation/ship, prompts, and LLM transport**.
The **hashtag scrape/measurement tick** (`fanops_hashtags.py`, `ig_web_scrape.py`, `ig_hashtag_scrape.py`)
is a separate Plan A codemap — see `docs/CODEMAPS/hashtag-lifecycle.md` for the measurement → lock → ship chain.

## Files covered (13 product modules — `git ls-files`, line counts via `wc -l`)

| # | Module | Lines | Role |
|---|--------|------:|------|
| 1 | `src/fanops/caption.py` | 219 | Caption gate orchestration: `request_captions`, `ingest_captions`, staleness |
| 2 | `src/fanops/caption_compose.py` | 98 | Source-lock reads, `compose_posted_caption`, `posted_text_for` |
| 3 | `src/fanops/caption_ingest.py` | 146 | Brand-risk, request parsing, `_caption_entry`, tags-only hold |
| 4 | `src/fanops/hashtags.py` | 297 | Tag normalization, curation gate, lock builders, `ship_from_lock`, cache read |
| 5 | `src/fanops/tagging.py` | 43 | Probabilistic `@mohflowmusic` artist tag de-clustering |
| 6 | `src/fanops/hookcheck.py` | 49 | Mechanical hook-purity floor (`is_weak_hook`) |
| 7 | `src/fanops/hookscore.py` | 66 | Read-only hook-quality scoreboard (never a gate) |
| 8 | `src/fanops/text.py` | 42 | `safe_public_url`, `sanitize_generated_text` |
| 9 | `src/fanops/prompts.py` | 267 | Moment-pick/hook prompt builders; lazy `caption_prompt` re-export |
| 10 | `src/fanops/prompts_caption.py` | 110 | `caption_prompt` (caption-only template) |
| 11 | `src/fanops/llm.py` | 417 | `claude_json_meta` / `claude_json` CLI transports (+ Cursor path) |
| 12 | `src/fanops/llm_errors.py` | 53 | Typed LLM errors + stderr classifiers |
| 13 | `src/fanops/llm_json.py` | 58 | Fence/brace JSON extraction helpers |

**Connective tissue (read for call graph, not exhaustively catalogued):** `responder.py` dispatches
`prompts.caption_prompt` via `_PROMPT["captions"]`; `pipeline.py` calls `request_captions` / `ingest_captions`;
`post/postiz.py`, `post/zernio.py`, `post/dryrun.py`, `studio/views_review.py` call `posted_text_for`.

**Deleted since 2026-07 freeze (do not resurrect):** `hashtag_hygiene.py` (SA-B1 — logic lives in `hashtags.norm_tag` /
`tag_defect` / `is_curatable`); Layer B `vet_hashtags` / `content_tag_candidates` (ship is `ship_from_lock` only).

## Test mapping (`git ls-files`)

| Test file | Lines | Primary symbols exercised |
|-----------|------:|---------------------------|
| `tests/test_caption.py` | 741 | `request_captions`, `ingest_captions`, lock gating, holds |
| `tests/test_caption_scoping.py` | 176 | Surface scoping, staleness |
| `tests/test_hashtags.py` | 138 | `ship_from_lock`, `lock_from_pile`, ranking keys |
| `tests/test_hashtag_hygiene.py` | 67 | `norm_tag`, `tag_defect`, `is_curatable` (module name legacy) |
| `tests/test_hashtag_attribution_severance.py` | 40 | Caption path does not write measurement cache |
| `tests/test_hashtag_layer_b_tombstone.py` | 30 | Layer B symbols stay dead |
| `tests/test_hashtag_page.py` | 219 | Studio hashtag views (cross-cluster) |
| `tests/test_hashtag_platform_truth.py` | 5 | Request-platform truth |
| `tests/test_hookcheck.py` | 85 | `is_weak_hook` |
| `tests/test_hookscore.py` | 88 | `narration_signature`, `hook_quality` |
| `tests/test_hook_authorship.py` | 110 | Moment-gate hook authorship |
| `tests/test_hook_cascade_removed.py` | 55 | Removed cascade tombstone |
| `tests/test_hook_language_gate.py` | 129 | Language gate on hooks |
| `tests/test_llm.py` | 779 | `claude_json_meta`, errors, JSON extraction |
| `tests/test_prompts.py` | 784 | `moment_pick_prompt`, `moment_hook_prompt`, `caption_prompt` |
| `tests/test_tagging.py` | 85 | `should_tag`, `decide_tag` |
| `tests/test_text.py` | 127 | `safe_public_url`, `sanitize_generated_text` (SA-B3) |

## Bloat signals

| Signal | Status |
|--------|--------|
| Monolithic `caption.py` (request + ingest + brand-risk + compose) | **Resolved** SA-B2 → `caption_ingest.py`, `caption_compose.py` |
| Monolithic `llm.py` (errors + JSON parse + transport) | **Resolved** SA-B3 → `llm_errors.py`, `llm_json.py` |
| Monolithic `prompts.py` (moments + caption) | **Resolved** SA-B5 → `prompts_caption.py`; `prompts.__getattr__` lazy re-export |
| Duplicate tag hygiene (`hashtag_hygiene.py`) | **Resolved** SA-B1 → `hashtags.norm_tag` / `tag_defect` / `is_curatable` |
| Layer B vetting (`vet_hashtags`, frozen pools, corpus floors) | **Removed** — ship is `ship_from_lock` ∩ source lock |
| `llm.py` still hosts Claude + Cursor transports (~417 lines) | **Remaining** — further split deferred |
| `prompts.py` moment builders still share file (~267 lines) | **Remaining** — acceptable; caption half extracted |
| `fanops_hashtags.py` scrape tick size | **Out of scope** (Plan A) |

## Pipeline / data-flow overview

### Caption gate — request → LLM → ingest → ship

```
caption.request_captions(led, cfg, clip_id, surfaces, accounts)
   │  gates on caption_compose._source_lock_completed (HV1: no gate until lock row exists)
   │  builds payload: transcript, guidance, per-surface persona + hashtag_store (source lock),
   │  hashtag_metrics, learned_hooks / learned_hooks_transferred (variant learning, fail-open)
   ▼
agentstep.write_request(cfg, kind="captions", key=clip_id, payload)
   ▼
responder._default_claude_model(kind="captions", ...)
   │  prompt = prompts.caption_prompt(payload)   # lazy import from prompts_caption
   ▼
llm.claude_json_meta(prompt, CaptionSet schema, ...)
   ▼
agentstep.write_response → caption.ingest_captions(led, cfg, clip_id)
   │  brand_risk_flag (caption_ingest) on raw caption
   │  ship_from_lock(model picks, source lock) — NO backfill, NO Layer B vetting
   │  is_tags_only_caption → hold "caption_tags_only"
   │  missing surface / language mismatch → hold
   ▼
ClipState.captioned or ClipState.held
```

### Vendor publish wire (IG/TikTok)

```
postiz / zernio / dryrun / views_review
   ▼
caption_compose.posted_text_for(cfg, led, post)
   │  ship_from_lock(post.hashtags, _source_lock_tags(cfg, src))
   ▼
compose_posted_caption(post.caption, tags)  → vendor `content`
```

YouTube bypasses `posted_text_for` — sends `Post.caption` raw (`post/CLAUDE.md`).

### Hashtag ship (`hashtags.ship_from_lock`)

```
ingest_captions / posted_text_for
   │  picks = item.hashtags or caption_ingest._tags_in(caption)
   │  lock = caption_compose._source_lock_tags(cfg, src)   # sidecar menu, not 80-pile
   ▼
hashtags.ship_from_lock(picks, lock, n=4)
   → picks ∩ lock, pick order preserved, hard cap 4, empty lock → []
```

Curation helpers (`norm_tag`, `tag_defect`, `is_curatable`) gate tags entering derived corpora /
lock builders — not the caption ingest intersection (that is pure set membership on the lock).

### Hook-purity gate (mechanical floor)

```
moments.ingest_moment_hooks (C4)
   │  hookcheck.is_weak_hook(hook, used, cluster_scope=...)
   │  caption_ingest.brand_risk_flag(hook, cfg)   # function-local import
   │  text.sanitize_generated_text(hook)
   ▼
hookscore.narration_signature / hook_quality — read-only meter, never a gate
```

## Per-file entry symbols

### `caption.py` — gate orchestration

- `VARIATION_AXES` — dormant P2 constant (helpers removed).
- `caption_request_stale` — reopen gate when surface set or platform keys drift.
- `request_captions` — write caption gate request; requires completed source lock.
- `ingest_captions` — read response, brand-risk + tags-only holds, `ship_from_lock`, advance state.
- `_learned_hooks`, `_transferred_hooks` — variant-learning hints (fail-open).

### `caption_compose.py` — lock reads + posted text

- `_source_lock_record`, `_source_lock_completed`, `_source_lock_tags` — sidecar lock menu.
- `compose_posted_caption` — sentence + ≤4 tags; idempotent hash-strip.
- `posted_text_for` — vendor wire for IG/TikTok/dryrun/review.
- `_hashtag_metrics_for` — forward numeric fields for caption prompt annotation.

### `caption_ingest.py` — ingest helpers

- `brand_risk_flag`, `_risk_re` — off-brand/begging guardrail (EN+AR, tuning.json override).
- `_tags_in`, `is_tags_only_caption` — hashtag extraction + tags-only hold.
- `_lang_base`, `_request_surfaces`, `_platform_for_surface` — request JSON truth.
- `_caption_entry` — `meta_captions` entry shape (caption sentence ≠ joined tags).
- `_recent_tags` — ordered-dedup recent post hashtags (uses `hashtags.norm_tag`).

### `hashtags.py` — normalization, curation, lock, ship

- `norm_tag`, `tag_defect`, `is_curatable` — structural curation gate (SA-B1).
- `ship_from_lock` — caption ship: picks ∩ lock, cap 4.
- `lock_from_pile`, `lock_from_shortlist` — lock builders from measurement cache.
- `load_measurements`, `ranked_tags` — read `00_control/hashtags.json`.
- `play_rank_key`, `size_rank_key`, `size_band`, `has_evidence` — ranking helpers.
- `CAPTION_TAG_RE` — hashtag token regex (shared with compose/ingest).

### `tagging.py`

- `should_tag` — deterministic SHA-1 rate gate per (clip, account).
- `decide_tag` — gap-checked `@mohflowmusic` decision; mutates `led.tag_log`.

### `hookcheck.py`

- `is_weak_hook` — empty / exact-dup / 3-token template cluster floor.

### `hookscore.py`

- `narration_signature`, `hook_quality`, `log_hook_quality` — observability only.

### `text.py`

- `safe_public_url` — https permalink guard (publish/reconcile paths).
- `sanitize_generated_text` — em-dash/curly-quote/zero-width cleanup.

### `prompts.py` — moment prompts + lazy caption re-export

- `_brief_fence`, `_data_fence`, `_inline`, cue/energy helpers.
- `moment_pick_prompt`, `moment_hook_prompt` — M1b pass 1/2 builders.
- `__getattr__` — lazy `caption_prompt` → `prompts_caption.caption_prompt` (SA-B5).

### `prompts_caption.py`

- `caption_prompt` — text-only caption/hashtag prompt; menu = per-surface `hashtag_store`.

### `llm.py` — CLI transports

- `claude_json_meta`, `claude_json` — primary Claude Code CLI path.
- `_claude_json_meta`, `_cursor_json_meta` — implementation details.
- Re-exports error types from `llm_errors`; uses `llm_json._extract_json_object`.

### `llm_errors.py`

- `LlmTimeoutError`, `LlmRateLimitError`, `LlmContextLimitError`, `LlmSchemaError`,
  `LlmFramesUnreadError`, `LlmToolchainError`.
- `_is_context_limit`, `_is_toolchain_error`.

### `llm_json.py`

- `_json_candidates`, `_extract_json_object` — fence + balanced-brace JSON recovery.

## Anomalies / dispatch-table false positives

**Not dead despite zero AST callers:**

- `prompts.moment_pick_prompt`, `moment_hook_prompt`, `caption_prompt` — invoked via
  `responder._PROMPT[kind](payload)` dict-dispatch.
- `prompts.caption_prompt` — also reachable via `prompts.__getattr__` lazy import (SA-B5).
- `llm.claude_json` — `studio/actions.py` binds `model = claude_json` for manual regenerate.

**Removed / tombstoned:**

- `hashtag_hygiene.py` — deleted; tests renamed target `hashtags` symbols.
- `vet_hashtags`, `vet_hashtags_traced`, `content_tag_candidates` — Layer B removed.
- `caption.normalize_variation_axis`, `coherent_variation` — removed with dormant P2 machinery.

**Fail-open handlers (intentional):** `caption_request_stale`, `_learned_hooks`, `_transferred_hooks`
— log + safe default; see `src/fanops/CLAUDE.md` escalation spine rules for new sites.
