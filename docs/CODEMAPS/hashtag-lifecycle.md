# Codemap — hashtag lifecycle (persona corpus → vet → post → reach → surfaced)

The end-to-end path that decides every posted hashtag. Per-persona, evidence-backed, closed-loop.

## Entities

- `Persona` ([personas.py](../../src/fanops/personas.py)) — first-class record in
  `00_control/personas.json`: `id`, `name`, `voice`, `tag_lean`, **`hashtag_corpus`**, `intake`
  (genre/language/reference_accounts/notes). Atomic flock-serialized writers mirror `accounts.py`.
- `Account.persona_id` ([accounts.py](../../src/fanops/accounts.py)) — links an account to a persona
  (one persona → many accounts). `_hydrate_from_personas` overrides the account's
  `persona`/`tag_lean`/**`hashtag_corpus`** in memory at load (fail-open; byte-identical when unlinked).

## Where corpus tags come from

| Source | Function | Notes |
|---|---|---|
| **Live co-occurrence discovery** | `personas.discover_corpus` → `meta_graph.discover_candidates` | harvest tags currently-winning posts use; finds tags never named; FAIL-OPEN to the re-rank below |
| Offline bootstrap re-rank | `personas.research_corpus(cfg, pid)` | reach-best tags the persona lacks (store + lean), instant + budget-free; the discovery fallback |
| Operator recommend | `meta_graph.tag_metrics(cfg, tag)` | live IG reach for ONE tag, spends 1 `ig_hashtag_search` slot (30/7-day cap), token never echoed |
| Frozen reach-vetted set | `hashtags.VETTED` / `vetted_menu` | the cold-start FLOOR only (Part 2 of the skill) |

Every source PROPOSES; the operator ACCEPTS into the corpus (the curation gate). Discovery never auto-writes a tag into a caption.

## Selection (the deterministic gate)

`hashtags.vet_hashtags(tags, platform, language, max_tags=4, *, store, lean, corpus)`:
- `corpus` joins the vetted membership (a curated tag the frozen set/store doesn't know SURVIVES)
  and is the PRIORITY pool — seeded first, floated ahead of the lean, capped at 4.
- `corpus=None/empty` → byte-identical to the pre-corpus behavior.
- Wired at [caption.py](../../src/fanops/caption.py): `request_captions` carries each surface's `corpus`
  in the payload; `ingest_captions` passes it to both `vet_hashtags` calls (normal + seed-fallback).
- [prompts.py](../../src/fanops/prompts.py) `caption_prompt` shows the corpus + a "prefer it" rule.

## Live discovery (co-occurrence) — finding tags we have never named

IG Graph has no "trending tags by topic" endpoint — `ig_hashtag_search` only *measures* a tag you already
name. The Graph-native way to DISCOVER is to harvest the hashtags that currently-winning posts use.

- [meta_graph.py](../../src/fanops/meta_graph.py) `harvest_cooccurring(cfg, seed_tags)`: resolves each
  category seed, reads its live `top_media` captions, and tallies the co-occurring hashtags
  (`{tag: {count, host_engagement}}`, seeds excluded). Same fail-soft/fail-closed discipline as
  `sample_trends`; the seed RESOLUTION spends one budget slot, the caption read is FREE. Arabic-aware regex.
- `discover_candidates(cfg, seeds, *, known, measure_k)`: ranks by (count, host_engagement), drops `known`
  (VETTED ∪ store ∪ corpus), optionally measures the top-K reach within budget. Returns evidence dicts.
- [personas.py](../../src/fanops/personas.py) `discover_corpus(cfg, pid)`: seeds = persona corpus + lean
  pool + intake genre; live proposals, FAIL-OPEN to `research_corpus` (offline re-rank) without creds.
- Studio **Research corpus** → `studio/personas.py research_corpus` → `discover_corpus`; proposals render
  with co-occurrence evidence (`· N posts`). The operator ACCEPTS into the corpus (the curation gate).
- `fanops hashtags discover` (`cmd_hashtags_discover`): the periodic per-persona REPORT (launchd/cron).
  READ-ONLY w.r.t. the caption menu. Auto-absorbing unvetted discoveries into the global menu was
  deliberately NOT built — an engagement floor admits generic spam and bypasses the operator gate; the
  closed loop below (accept → own-reach feedback) is the safe refresh path.

## Closed loop (reach → store → surfaced)

- `fanops_hashtags.tag_reach_means(led)` → `{tag: mean reach}` over analyzed posts (attribution on ONE
  Post — no join). `rank_tags_by_reach` sorts on it.
- `fanops_hashtags.refresh_store` writes the reach-ranked `00_control/hashtags.json` store = own-reach
  + (opt) live Graph trends (`FANOPS_HASHTAG_TRENDS` **defaults ON**, fail-open without a Meta token),
  doctor-gated. `load_store`/`vetted_menu(store)` feed selection + research.
- Studio **Personas** tab ([studio/personas.py](../../src/fanops/studio/personas.py),
  [studio/views.py](../../src/fanops/studio/views.py) `personas_page`): corpus rendered REACH-RANKED,
  high-reach (store-present) tags flagged ★, each curated tag annotated with its MEASURED mean reach.

## Config

- `FANOPS_HASHTAG_TRENDS` — Graph trend sampling; **default ON**, fail-open without creds.
- `META_GRAPH_TOKEN` + `META_IG_USER_ID` — the IG Business Graph creds (absent → own-reach-only).
- `Account.persona_id` / `personas.json` — the per-persona corpus link.

## Tests

- `tests/test_personas.py`, `tests/test_studio_personas.py` — persona entity + Studio page.
- `tests/test_persona_corpus.py` — corpus joins/leads vet_hashtags + caption wiring.
- `tests/test_graph_tag_metrics.py` — Graph lookup + default-ON.
- `tests/test_graph_cooccurrence.py` — `harvest_cooccurring` + `discover_candidates` (live discovery primitives).
- `tests/test_corpus_research.py` — offline bootstrap re-rank + reach-ranked surfacing.
- `tests/test_corpus_discovery.py` — `discover_corpus` live + fail-open fallback + Studio action.
- `tests/test_hashtag_lifecycle_e2e.py` — the whole loop end-to-end.
