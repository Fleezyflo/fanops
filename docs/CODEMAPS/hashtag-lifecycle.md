# Codemap — hashtag lifecycle (persona corpus → vet → post → reach → surfaced)

The end-to-end path that decides every posted hashtag. Per-persona, evidence-backed, closed-loop.

## Entities

- `Persona` ([personas.py](../../src/fanops/personas.py)) — first-class record in
  `00_control/personas.json`: `id`, `name`, `voice`, **`hashtag_corpus`**, and the lever
  fields (`content_focus`, `selection_scope`, `hook_angle`). Atomic flock-serialized writers mirror `accounts.py`.
  (`tag_lean` retired M3 — folded into `hashtag_corpus`.)
- `Account.persona_id` ([accounts.py](../../src/fanops/accounts.py)) — links an account to a persona
  (one persona → many accounts). `_hydrate_from_personas` overrides the account's
  `persona`/**`hashtag_corpus`** (+ lever fields) in memory at load (fail-open; byte-identical when unlinked).

## Where corpus tags come from

| Source | Function | Notes |
|---|---|---|
| **Live co-occurrence discovery** | `personas.discover_corpus` → `meta_graph.discover_candidates` | harvest tags currently-winning posts use; finds tags never named; FAIL-OPEN to the re-rank below |
| Offline bootstrap re-rank | `personas.research_corpus(cfg, pid)` | reach-best tags the persona lacks (store + lean), instant + budget-free; the discovery fallback |
| Operator recommend | `meta_graph.tag_metrics(cfg, tag)` | live IG reach for ONE tag, spends 1 `ig_hashtag_search` slot (30/7-day cap), token never echoed |
| Frozen reach-vetted set | `hashtags.VETTED` / `vetted_menu` | the cold-start FLOOR only (Part 2 of the skill) |

Every source PROPOSES; the operator ACCEPTS into the corpus (the curation gate). Discovery never auto-writes a tag into a caption.

## Selection (the deterministic gate)

`hashtags.vet_hashtags(tags, platform, language, max_tags=4, *, store, lean, corpus, content)`:
- `corpus` joins the vetted membership (a curated tag the frozen set/store doesn't know SURVIVES)
  and is the PRIORITY pool — seeded first, floated ahead of the lean, capped at 4.
- **`content`** (per-clip content tags, see below) ALSO joins the membership (so a clip-specific tag the
  model picked SURVIVES) and floats just behind the corpus.
- **Reserved floors** take the TAIL slots so the corpus/lean/reach LEAD is preserved: a region (Arabic)
  tag first (non-negotiable under a lean), then ONE content tag — each guarantees its signal reaches the
  ≤4 line even when the model filled every slot.
- `corpus=None/empty` AND `content=None/empty` → byte-identical to the pre-corpus behavior.
- Wired at [caption.py](../../src/fanops/caption.py): `request_captions` carries each surface's `corpus`
  + the clip-level `content_tags` in the payload; `ingest_captions` passes both to `vet_hashtags_traced`
  (normal + seed-fallback paths).
- [prompts.py](../../src/fanops/prompts.py) `caption_prompt` shows the corpus + the content tags + a
  "prefer them" rule (byte-identical menu-only rule when no content).

## Per-clip CONTENT tags — tags based off the clip's own information

The reach/corpus/lean signals are persona/account-level constants — two different clips of one persona
used to get IDENTICAL tags. `content_tag_candidates(transcript)` ([hashtags.py](../../src/fanops/hashtags.py))
adds the per-CLIP signal: a deterministic, pure extractor (latin word tokens 3–20 chars, stopwords dropped,
frequency-then-first-seen, ≤6, normalized) over the clip's `Moment.transcript_excerpt`. Blank / instrumental
/ Arabic-only / numbers → `[]` → byte-identical. These are CANDIDATES the model may pick + that survive the
membership gate; the model still SELECTS (never invents outside menu ∪ content), and `vet_hashtags` still
enforces ≤4. Result: two different-content clips of one persona ship DIFFERENT tags.

## Provenance — every shipped tag traces to a real signal

`vet_hashtags_traced(...)` returns `(tags, {tag: source})` where `source ∈ {content, corpus, region,
graph-reach, discovery, genre-floor}` (priority content > corpus > region > graph-reach > discovery >
genre-floor; `graph-reach` = the tag traces to the live Meta Graph reach store). `ingest_captions` stores it
as `meta_captions[surface]["tag_sources"]`; the Studio surface
editor ([_surface_edit.html](../../src/fanops/studio/templates/_surface_edit.html)) renders a read-only
"Why these tags" chip row. A sourceless tag — pure theatre — cannot ship (genre-floor is the catch-all, never
empty). This is the hashtag-axis instance of the "every knob real, no theatre" rule.

## Live discovery (co-occurrence) — finding tags we have never named

IG Graph has no "trending tags by topic" endpoint — `ig_hashtag_search` only *measures* a tag you already
name. The Graph-native way to DISCOVER is to harvest the hashtags that currently-winning posts use.

- [meta_graph.py](../../src/fanops/meta_graph.py) `harvest_cooccurring(cfg, seed_tags)`: resolves each
  category seed, reads its live `top_media` captions, and tallies the co-occurring hashtags
  (`{tag: {count, host_engagement}}`, seeds excluded). Same fail-soft/fail-closed discipline as
  `sample_trends`; the seed RESOLUTION spends one budget slot, the caption read is FREE. Arabic-aware regex.
- `discover_candidates(cfg, seeds, *, known, measure_k)`: ranks by (count, host_engagement), drops `known`
  (VETTED ∪ store ∪ corpus), optionally measures the top-K reach within budget. Returns evidence dicts.
- [personas.py](../../src/fanops/personas.py) `discover_corpus(cfg, pid)`: seeds = persona corpus +
  intake.genre; live proposals, FAIL-OPEN to `research_corpus` (offline re-rank) without creds.
- Studio **Research corpus** → `studio/personas.py research_corpus` → `discover_corpus`; proposals render
  with co-occurrence evidence (`· N posts`). The operator ACCEPTS into the corpus (the curation gate).
- `fanops hashtags discover` (`cmd_hashtags_discover`): the periodic per-persona REPORT (launchd/cron).
  READ-ONLY w.r.t. the caption menu. Auto-absorbing unvetted discoveries into the global menu was
  deliberately NOT built — an engagement floor admits generic spam and bypasses the operator gate; the
  Graph-reach refresh below (measure → rank → store) is the safe refresh path.

## Graph-reach store (harvest → measure → rank → surfaced)

The ONLY judge of a hashtag is its LIVE Meta Graph reach (operator 2026-06-27) — never a post that used it
(post insights attribute to the hook/clip/account-in-stitch; pinned by `tests/test_hashtag_attribution_severance.py`).

- `fanops_hashtags.refresh_store(cfg)` (no `led`, no doctor gate): harvest co-occurring candidates from the
  niche seeds (every persona's corpus + intake.genre), measure their Graph reach within the 30/7-day budget,
  rank by reach, write `{tags, reach}` to `00_control/hashtags.json`. FAIL-OPEN to the frozen reach floor
  without Meta creds. `load_store`/`vetted_menu(store)` feed selection; `load_store_reach` feeds the surface.
- `refresh_store_if_due(cfg)` re-runs it on a 12h throttle inside `fanops run` (each daemon tick), ungated on
  the publish backend (a hashtag's worth is its platform reach, not whether we publish) — Meta creds only.
- Studio **Personas** tab ([studio/personas.py](../../src/fanops/studio/personas.py),
  [studio/views.py](../../src/fanops/studio/views.py) `personas_page`): corpus rendered REACH-RANKED,
  currently-most-active (store-present) tags flagged ★, each curated tag annotated with its LIVE Graph reach.

## Config

- `FANOPS_HASHTAG_TRENDS` — Graph trend sampling; **default ON**, fail-open without creds.
- `META_GRAPH_TOKEN` + `META_IG_USER_ID` — the IG Business Graph creds (absent → frozen reach floor).
- `Account.persona_id` / `personas.json` — the per-persona corpus link.

## Tests

- `tests/test_personas.py`, `tests/test_studio_personas.py` — persona entity + Studio page.
- `tests/test_persona_corpus.py` — corpus joins/leads vet_hashtags + caption wiring.
- `tests/test_graph_tag_metrics.py` — Graph lookup + default-ON.
- `tests/test_graph_cooccurrence.py` — `harvest_cooccurring` + `discover_candidates` (live discovery primitives).
- `tests/test_corpus_research.py` — offline bootstrap re-rank + reach-ranked surfacing.
- `tests/test_corpus_discovery.py` — `discover_corpus` live + fail-open fallback + Studio action.
- `tests/test_hashtag_lifecycle_e2e.py` — the whole loop end-to-end.
