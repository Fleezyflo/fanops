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
| Bootstrap research | `personas.research_corpus(cfg, pid)` | reach-best tags the persona lacks (store + lean), instant + budget-free |
| Operator recommend | `meta_graph.tag_metrics(cfg, tag)` | live IG reach for ONE tag, spends 1 `ig_hashtag_search` slot (30/7-day cap), token never echoed |
| Frozen reach-vetted set | `hashtags.VETTED` / `vetted_menu` | the always-present floor (Part 2 of the skill) |

## Selection (the deterministic gate)

`hashtags.vet_hashtags(tags, platform, language, max_tags=4, *, store, lean, corpus)`:
- `corpus` joins the vetted membership (a curated tag the frozen set/store doesn't know SURVIVES)
  and is the PRIORITY pool — seeded first, floated ahead of the lean, capped at 4.
- `corpus=None/empty` → byte-identical to the pre-corpus behavior.
- Wired at [caption.py](../../src/fanops/caption.py): `request_captions` carries each surface's `corpus`
  in the payload; `ingest_captions` passes it to both `vet_hashtags` calls (normal + seed-fallback).
- [prompts.py](../../src/fanops/prompts.py) `caption_prompt` shows the corpus + a "prefer it" rule.

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
- `tests/test_corpus_research.py` — bootstrap research + reach-ranked surfacing.
- `tests/test_hashtag_lifecycle_e2e.py` — the whole loop end-to-end.
