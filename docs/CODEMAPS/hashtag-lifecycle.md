> Rewritten 2026-08-26 (HT6) — invariants map, not auto-synced. When prose and code disagree, the code is right.

# Codemap — hashtag lifecycle (lock produce → remesure → ship)

The end-to-end path that decides every **posted** hashtag. Posted tags come from
one place only: the **source lock**, shipped by `hashtags.ship_from_lock`.

## The single rule everything else follows

**Ship path = `ship_from_lock`.** Caption ingest / compose takes model picks ∩
that source's lock, pick order, hard cap 4. Empty / missing lock → empty tag
line (sentence still ships). No AR floor, no mega slot, no store ∪ corpus, no
80-pile, no `vet_hashtags` on the posted line.

Lock menu order (what the sidecar advertises as `hashtag_store`) is
`play_count` DESC then `current_top_reel_play_max_7d`. Lock membership itself is
produced by `source_tags.lock_from_pile` (positive `play_count` only,
`play_rank_key`, cap 12).

Studio `/hashtags` shows source locks (full cap-12 list) and a play-ranked
measurement cache. It does not show persona corpora as a caption menu.
`fanops hashtags discover` reports source locks.

Metric honesty for measurement rows: visibility is **only** Instagram fields
stored on the tag — never an invented blended `reach`. Persisted record shape is
`hashtags.RECORD_NUM_FIELDS` / `RECORD_STR_FIELDS`.

## Produce — per-source lock (Safari)

`source_tags` owns `00_control/source_tag_locks.json`. Safari scrape
(`ig_web_scrape`) completes the per-source lock (`researched_at` + `lock`).
Graph may cache/confirm/rank; Graph never vetoes membership or withholds
`researched_at`. Empty `lock: []` means scrape finished with zero admits.

Caption surfaces read that lock as `hashtag_store` (same list every surface of
that source). Never the persona store ∪ corpus.

## Tick — sidecar remesure (not persona discovery)

`cli._cmd_run_pass` → `fanops_hashtags.refresh_store_if_due`:

- Remesure **sidecar pile∪lock names** only (`_remesure_sidecar` /
  `known_names`).
- Queue is **never** `persona_terms`. Vocab expand is **not** called from the
  run loop (HV1-PR4).
- Cadence gated on `last_complete_pass` (default 12h), exact-name quota ≤30 /
  7 days.
- Same Safari `open_web_session` plane as lock produce.

Manual `fanops hashtags refresh` is the same sidecar remesure
(`cmd_hashtags_refresh` → `_remesure_sidecar`). Live `refresh_store()` without
an injected client aborts `safari_only` — harvest is not a live operator path.

## Ship — `hashtags.ship_from_lock`

Called from `caption` (ingest + compose helpers):

```text
tags = ship_from_lock(picks, _source_lock_tags(cfg, src), n=4)
```

- Membership = lock only
- Order = pick order among lock members
- Cap = 4
- No floors, no backfill, no mega / AR composition

## Layer B — still compiles, not on the post

`persona_research.derive_corpus` + `hashtags.vet_hashtags` still exist in tree
(deletion is a follow-up). They do not write the posted tag array, are not on
the run loop, and are not shown as a caption menu. `refresh_corpora_if_due` is
not called from `_cmd_run_pass`.

## Attribution severance

A tag's worth is its live platform number, **never** a post that used it.
Pinned by `tests/test_hashtag_attribution_severance.py`.

## Files

| Concern | File |
|---|---|
| caption ship (`ship_from_lock`) + lock menu helpers | `src/fanops/hashtags.py` |
| caption ingest / compose | `src/fanops/caption.py` |
| per-source lock producer | `src/fanops/source_tags.py` |
| Safari lock + remesure XHR | `src/fanops/ig_web_scrape.py` |
| tick remesure + Layer A driver | `src/fanops/fanops_hashtags.py` |
| run-loop tick wiring | `src/fanops/cli.py` (`_cmd_run_pass`) |
| Layer B observatory derivation | `src/fanops/persona_research.py` |
| Layer A scrape helpers (manual refresh) | `src/fanops/ig_hashtag_scrape.py` |
| Graph hashtag helpers (rank/cache; never lock veto) | `src/fanops/meta_graph.py` |

## Config

- `FANOPS_IG_SCRAPE_USER` + Safari profiles — remesure / lock scrape plane.
- Sidecar: `00_control/source_tag_locks.json`.
- Measurement cache: `00_control/hashtags.json` (remesure writes; not the
  caption membership set).
- `FANOPS_CORPUS_TARGET` — Layer B observatory ceiling only.

## Tests

`tests/test_source_tag_lock.py` / `test_source_tags.py` (lock produce),
`test_caption.py` (ship_from_lock wiring), `test_hashtags.py` (helpers + legacy
`vet_hashtags`), `test_skill_drift.py` (skill honesty),
`test_fanops_hashtags.py` (tick remesure), `test_ig_web_scrape.py` (Safari XHR),
`test_hashtag_attribution_severance.py` (severance invariant).
