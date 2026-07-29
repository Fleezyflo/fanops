> Rewritten 2026-07-26 — invariants map, not auto-synced. When prose and code disagree, the code is right.

# Codemap — hashtag lifecycle (description → measure → derive → select → post)

The end-to-end path that decides every posted hashtag. Two layers, one authority: **the platform**.

## The single rule everything else follows

Discovery direction (MOL-637/MOL-644): Layer A search seeds are operator **niche** plus durable LLM **vocab** (`persona_terms`). Voice / content_focus / hook_angle / intensity stay on captions+hooks — they are not Instagram search roots. Vocab expands territory; it never writes the corpus.

Metric honesty: visibility is **only** Instagram fields Layer A stores — never an invented blended `reach`, never "most visibility" from a single top-post like.

`hashtags.RANK_FIELDS = ("play_count", "like_count")` with preferred `METRIC_FIELD = "play_count"`.
`ig_hashtag_scrape.measure_and_harvest_scrape` takes ONE `hashtag_medias_top`
(`hashtags.TOP_SAMPLE_N` = 27 — MOL-691 raised it from Instagram's 9-row default grid page, which is far
too thin to take a maximum over; 27 is instagrapi's own default for its v1/recent/reels reads, so this is
the same endpoint one page deeper, and the co-tag harvest rides the same rows) and stores:

- `like_count` = **median** of like_count across Top medias that carry one
- `play_count` = **median** of play_count across Top medias that carry one (Reels/views when present)
- `current_top_reel_play_max_7d` = **max** play_count among rows Instagram marks `product_type=clips`
  and dates within 7 days, with `top_reel_sample_n` as that maximum's honest denominator (MOL-691).
  A Reel-less sample never zeroes this — prior evidence is carried, since a transiently photo-only grid
  is not proof a tag has no Reels.

`resolve_hashtag_scrape` also persists `media_count` from `hashtag_info` when the private API serves it
(Graph Hashtag node still refuses `media_count` — probed 2026-07-26 — but scrape can). Volume ages on its
OWN `media_count_at` stamp (`_VOLUME_MAX_AGE_DAYS` = 7): before MOL-691 a cached `graph_id` skipped the
only call that serves volume, so 131 of 300 live records carried no `media_count` and could never get one.
Ranking uses `play_count` when present, else `like_count`. Neither ⇒ UNMEASURED ⇒ inadmissible.

The persisted record shape is ONE contract — `hashtags.RECORD_NUM_FIELDS` / `RECORD_STR_FIELDS`. It must
stay in sync with the writer: `refresh_store` seeds its working cache from `load_measurements` and then
rewrites `hashtags.json` **whole**, so a field the reader does not retain is written in pass N and silently
stripped in pass N+1. Widen the reader before the writer.
(Graph `measure_and_harvest` remains in `meta_graph` for a deferred path — Layer A refresh does not call it.)

The pre-2026-07-26 metric — likes **plus** comments summed under a key we named `reach` — was invented.
The 2026-07-26 "first like_count wins" proxy was honest-as-far-as-it-went but under-used the Top grid and
discarded plays/volume Instagram already returned on the same fetches.

## Layer A — measurement (instagrapi; Graph hashtag deferred)

`fanops_hashtags.refresh_store(cfg)` — driven by `refresh_store_if_due` on the 12h tick in `cli.py`'s run
loop. Network source is **instagrapi** (`ig_hashtag_scrape`). Missing scrape session aborts loudly
(`written:False`, `aborted:no_scrape`) — there is **no silent Graph fallback**. Graph hashtag helpers
(`meta_graph.resolve_hashtag` / `measure_and_harvest`) stay in tree for later. Per persona linked to an
**active** account (`_posting_persona_ids`; dormant personas cannot steer discovery):

1. **terms** — `persona_research.persona_terms(per, cfg)` → niche ∪ `hashtag_vocab.json` seeds. Deterministic given durable vocab, **corpus-blind**. Vocab is refreshed by `hashtag_vocab.expand_vocab_if_due` (LLM, 12h, `FANOPS_RESPONDER=llm`).
2. **anchors** — each term resolves via `ig_hashtag_scrape.resolve_hashtag_scrape` (`hashtag_info`).
3. **measure + harvest, one fetch** — `measure_and_harvest_scrape(client, tag)` returns Top medians
   (`play_count`/`like_count`) AND every hashtag those same captions carry (`CAPTION_TAG_RE`). Co-occurrence discovers tags nobody named
   and is where **versatility** comes from: posts winning in a niche carry broad tags alongside narrow ones.
4. **queue** — anchors, then everything already measured stalest-`measured_at` first, then this pass's novel
   co-tags. Co-tags are harvested from ANCHORS only; a co-tag's own co-tags drift off-niche within two hops.

Writes `00_control/hashtags.json` — a flat cache, **measured tags only**:

```json
{"#hiphop": {"graph_id": "17841563854111824", "play_count": 120000.0, "like_count": 8772.0,
             "media_count": 1500000.0, "measured_at": "2026-07-26T12:00:00+00:00", "from": {"#rap": 3}}}
```

`from` is **inbound** membership only (niche/vocab appears on THAT tag's Top — MOL-643). Outbound co-tags enqueue for measure but do not write `from`. Layer B reads `from` offline.
Records older than 90 days are pruned on write. Reader: `hashtags.load_measurements` — a record missing the
metric, the id or the stamp is dropped rather than repaired, which is also how every legacy `reach` record
becomes inadmissible without a migration.

## Layer A has no local budget — Instagram throttle is the only governor

Deleted 2026-07-26: the local search meter (`_BUDGET_LIMIT` / `_BUDGET_WINDOW_DAYS` / `_read_queries` /
`budget_remaining` / `record_query` / `00_control/hashtag_budget.json`). That construct logged every search
and then **skipped every tag it had logged**, capping each pass.

What replaces it:
- **`graph_id` cached** on every record — a known tag skips `hashtag_info` and still re-measures via
  `hashtag_medias_top`, so resolve funds novel discovery only.
- **Throttle** (please_wait / rate / feedback_required) ⇒ `ScrapeThrottled` ends the pass; evidence accrued
  so far is written.
- **Any other scrape error** ⇒ `ScrapeRefused` (truncated message, optional code) recorded in `unresolved`;
  a later pass retries. Nothing predicts or meters an allowance.

## Layer B — derivation (ZERO network)

`persona_research.derive_corpus(cfg, pid)` — driven by `refresh_corpora_if_due` on its own 12h tick.

1. **cutover** — `persona_store.deprecate_legacy_corpus` moves every corpus tag lacking derivation meta into
   the visible `hashtag_corpus_deprecated` field and empties `hashtag_corpus`. Idempotent. Hydration and
   selection read only `hashtag_corpus`, so pre-derivation tags stop shipping the moment the code runs.
2. **pool** — `_aligned_pool` (MOL-665 + MOL-685): evidence + **relatedness candidate** gate, then rank by metric.
   Anchors always. Non-anchors need inbound_hits≥2 or n_roots≥2 from live roots; magnets may also
   candidate on any inbound when metric ≥ `MAGNET_METRIC_FLOOR` (not a ban — soft lane). One-hit
   non-magnets never enter even at huge plays. Non-anchor category-scale tags (`media_count` at
   platform volume) need multi-root relatedness and rank behind niche peers. `derive_corpus` cuts
   top `corpus_target` by metric.
3. **admit** — `_is_evidence` (metric present + positive, parseable `measured_at`, fresher than 90 days) and
   `hashtag_hygiene.is_curatable` (structural only).
4. **write** — top `cfg.corpus_target` by the platform metric via `apply_auto_corpus`, which REPLACES the
   corpus wholesale. Meta per tag: `{play_count?, like_count?, media_count?, measured_at, from}`.

Never padded: thin evidence ⇒ a short corpus. Empty pool (cold cache / outage) ⇒ `{changed: False}` and the
previous derived corpus stands.

## What is NOT here any more

| Removed | Why |
|---|---|
| Pins, `add_corpus_tag`/`remove_corpus_tag`, `_partition_corpus` | a derived value cannot also be hand-tended; the reconciliation froze rotation and preserved unmeasured tags (live: ~90% of corpus entries carried `reach: null`) |
| `research_corpus`, `discover_corpus`, `tag_metrics`, the Studio Research/Check-reach buttons | proposals are a curation step in a system that no longer curates |
| `_MEGA`/`_RELEVANCE`/`_GOSSIP_*`/`_NICHE_POOLS`/`niche_floor`/`_RANK`/`VETTED`/`_composition` | hand-researched reach claims from June 2026 — the exact manufactured assessment the rule forbids |
| `FANOPS_HASHTAG_TRENDS` | with the cache as the sole reach source, an off-switch is a broken system, not an option |
| `hashtag_migrate.py` + `fanops hashtags migrate` | a one-time migration whose job the cutover now does structurally |

**Operator lever:** declared niche via `/personas/niche` (Studio Personas). No ban list.

## Selection — `hashtags.vet_hashtags` (unchanged in shape, re-sourced)

Membership = persona-scoped measured pool ∪ corpus (content may join); no bans. An invented tag dies here; so
does an unmeasured one. What survives from before is COMPOSITION, which is format rather than a reach claim:

- hard cap of 4; corpus leads but `_CORPUS_LEAD_MAX = 3` keeps ≥1 slot reachable by the clip's own picks
- graded LRU (`recent`, oldest-first) so a line rotates instead of locking
- one `_ARABIC` region tag reserved on Arabic-language clips
- cold cache + no corpus → an **empty** line (no discovery pad — honest beats padded)

`vet_hashtags_traced` labels every shipped tag `content | corpus | region | graph-reach` and
`caption._caption_entry` persists it as `tag_sources`; the Review tab renders it. The label set is TOTAL by
construction — `genre-floor` and `discovery` retired with the pools / discovery floor.

## Attribution severance (unchanged, still pinned)

A tag's worth is its live platform reach, **never** a post that used it: post insights attribute to the
hook/clip/account, not the hashtag. Pinned by `tests/test_hashtag_attribution_severance.py`.

## Files

| Concern | File |
|---|---|
| metric constant, cache reader, selection | `src/fanops/hashtags.py` |
| Layer A scrape (resolve, measure+harvest, throttle) | `src/fanops/ig_hashtag_scrape.py` |
| Graph hashtag helpers (deferred) | `src/fanops/meta_graph.py` |
| Layer A driver + CLI verbs | `src/fanops/fanops_hashtags.py` |
| terms, alignment, Layer B derivation | `src/fanops/persona_research.py` |
| corpus writer + deprecation cutover | `src/fanops/persona_store.py` |
| structural gates | `src/fanops/hashtag_hygiene.py` |
| Studio read-models / panels | `src/fanops/studio/views_hashtags.py`, `views.py`, `templates/_hashtags_panel.html`, `_personas_panel.html` |

## Config

- `FANOPS_IG_SCRAPE_USER` + session (`ig_scrape_session.json`) or `FANOPS_IG_SCRAPE_PASSWORD` — instagrapi scrape. Absent ⇒ refresh aborts (`no_scrape`); the cache stands as-is (selection ships whatever is already measured, or short). Graph token is NOT used for Layer A refresh.
- `FANOPS_CORPUS_TARGET` (default 80) — how many measured tags a derived corpus aims to hold. A ceiling, not
  a quota: derivation never pads to reach it.
- `Account.persona_id` / `personas.json` — the per-persona link; the persona's niche is the lever.

## Tests

`tests/test_hashtag_platform_truth.py` (the contract: verbatim field, no local cap, id cache, evidence-only
corpus, deprecation cutover, cache-sourced selection, refusal handling), `test_hashtags.py` (selection),
`test_fanops_hashtags.py` (Layer A), `test_meta_graph.py` (transport), `test_persona_corpus.py` (corpus →
caption wiring), `test_hashtag_lifecycle_e2e.py` (end to end, `@pytest.mark.slow`),
`test_hashtag_attribution_severance.py` (the severance invariant).
