> Rewritten 2026-08-26 (HT6); scrape module table refreshed 2026-09-03 (SA-A5) — invariants map,
> not auto-synced. When prose and code disagree, the code is right.

# Codemap — hashtag lifecycle (lock produce → remesure → ship)

The end-to-end path that decides every **posted** hashtag. Posted tags come from
one place only: the **source lock**, shipped by `hashtags.ship_from_lock`.

## The single rule everything else follows

**Ship path = `ship_from_lock`.** Caption ingest / compose takes model picks ∩
that source's lock, pick order, hard cap 4. Empty / missing lock → empty tag
line. A tags-only stored `Post.caption` **survives** on the IG/TT wire via
`compose_posted_caption` — `src/fanops/post/CLAUDE.md`. No AR floor, no mega
slot, no store ∪ corpus, no 80-pile, no `vet_hashtags` on the posted line.

Lock membership is catalog keep ∩ positive `play_count`, order is keep order,
cap 12 (`hashtags.lock_from_shortlist`). Caption picks up to 4 from that lock
by CLIP FIT. `play_count` / `current_top_reel_play_max_7d` are meters on the
row, not the choose-key. `lock_from_pile` remains for hydrate / used-tags fill
only.

Studio `/hashtags` shows source locks (full cap-12 list) and a play-ranked
measurement cache. It does not show persona corpora as a caption menu.
`fanops hashtags discover` reports source locks.

Metric honesty for measurement rows: visibility is **only** Instagram fields
stored on the tag — never an invented blended `reach`. Persisted record shape is
`hashtags.RECORD_NUM_FIELDS` / `RECORD_STR_FIELDS`.

## Produce — per-source lock (Safari)

`source_tags` owns `00_control/source_tag_locks.json`. `shortlist_source_tags`
is the produce LLM pass (live: `research_fn` on `lock_ready_sources`). Safari
scrape (`ig_web_scrape`) admits those names
(`researched_at` + `lock`). Graph may cache/confirm a `graph_metric`; Graph
never vetoes membership, never reorders the lock, and never withholds
`researched_at`. Empty `lock: []` means scrape finished with zero admits.

Caption surfaces read that lock as `hashtag_store` (same list every surface of
that source). Never the persona store ∪ corpus.

## Tick — sidecar remesure (not persona discovery)

`cli._cmd_run_pass` → `fanops_hashtags.refresh_store_if_due` (facade →
`hashtag_refresh.refresh_store_if_due`):

- Remesure **sidecar pile∪lock names** only (`hashtag_refresh._remesure_sidecar` /
  `_refresh_pass(..., known_names=...)`).
- Queue is **never** `persona_terms`. Vocab expand is **not** called from the
  run loop (HV1-PR4).
- Cadence gated on `last_complete_pass` (default 12h), exact-name quota ≤30 /
  7 days.
- One Safari opener per tick: `ig_safari_shell` tick slot (`lock` OR `remesure`).
- Cooldown / UTC day budget / peer LRU: `hashtag_scrape_policy` (`.hashtag_scrape_cooldown.json`).
- Same Safari `ig_web_scrape.open_web_session` plane as lock produce.

Manual `fanops hashtags refresh` is the same sidecar remesure
(`hashtag_refresh.cmd_hashtags_refresh` → `_remesure_sidecar`). Operator session
recovery: `hashtag_refresh.cmd_hashtags_scrape_login` (clears auth-death freeze per
user). Live `hashtag_refresh.refresh_store()` without an injected client aborts
`safari_only` — Layer A persona harvest is not a live operator path.

## Ship — `hashtags.ship_from_lock`

Called from `caption` (ingest + compose helpers):

```text
tags = ship_from_lock(picks, _source_lock_tags(cfg, src), n=4)
```

- Membership = lock only
- Order = pick order among lock members
- Cap = 4
- No floors, no backfill, no mega / AR composition

## Layer B — deleted

`vet_hashtags`, `derive_corpus`, `hashtag_vocab.py`, and `tag_outcomes.py` are
gone. Tombstone: `tests/test_hashtag_layer_b_tombstone.py`.

## Attribution severance

A tag's worth is its live platform number, **never** a post that used it.
Pinned by `tests/test_hashtag_attribution_severance.py`.

## Scrape/measurement module split (SA-A1–A4)

Public imports stay on `fanops_hashtags` (thin facade); implementation:

| Module | Responsibility |
|---|---|
| `hashtag_scrape_policy.py` | Cooldown ladder (30m→6h), UTC day budget (~40 req/day/account), auth-death freeze, `scrape_user_blocked`, peer LRU (`_healthy_scrape_users`) |
| `ig_safari_shell.py` | In-tab Safari XHR (`safari_xhr` / `safari_fetch`), `pace_since_last`, per-tick slot (`mark_safari_tick_slot`), platform stop exceptions, `safari_profile_auth` |
| `hashtag_refresh.py` | `refresh_store` / `refresh_store_if_due`, `_remesure_sidecar`, `_refresh_pass`, `cmd_hashtags_refresh`, `cmd_hashtags_scrape_login`, pass lease + sidecar quota |
| `fanops_hashtags.py` | Re-exports above + `cmd_hashtags_discover` only |
| `ig_web_scrape.py` | `IgWebSession`, `open_web_session` — lock scrape + remesure API duck-type |
| `ig_hashtag_scrape.py` | AppleScript Safari window/tab, scrape session envelope, `scrape-login` Chrome/Safari bootstrap |

## Files

| Concern | File |
|---|---|
| caption ship (`ship_from_lock`) + lock menu helpers | `src/fanops/hashtags.py` |
| caption ingest / compose | `src/fanops/caption.py` |
| per-source lock producer | `src/fanops/source_tags.py` |
| Safari session + tag API (`open_web_session`, `IgWebSession`) | `src/fanops/ig_web_scrape.py` |
| in-tab XHR, pacing, tick slot, stop exceptions | `src/fanops/ig_safari_shell.py` |
| remesure orchestration + CLI refresh/scrape-login | `src/fanops/hashtag_refresh.py` |
| cooldown, budget, freeze, peer selection | `src/fanops/hashtag_scrape_policy.py` |
| facade re-exports + `cmd_hashtags_discover` | `src/fanops/fanops_hashtags.py` |
| run-loop tick wiring | `src/fanops/cli.py` (`_cmd_run_pass`) |
| Layer B observatory derivation | `src/fanops/persona_research.py` |
| scrape session envelope + Safari window bootstrap | `src/fanops/ig_hashtag_scrape.py` |
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
