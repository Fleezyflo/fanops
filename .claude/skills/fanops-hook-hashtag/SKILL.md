---
name: fanops-hook-hashtag
description: Use when writing or reviewing on-screen HOOKS or HASHTAGS for FanOps clips. The hook is one short line: what would you say to keep the viewer watching? The posted caption is one hook sentence; 3–4 tags live in the hashtags array (hard cap 4), chosen via ship_from_lock from the source lock only — never words the model invents, never the 80-pile / store∪corpus, never AR floor / mega composition. Evidence-backed; sources cited inline.
---

# FanOps Hooks & Hashtags — researched, platform-measured

> **Source of truth = code.** Hook patterns live in `prompts._hook_spec`. Caption
> ship is `hashtags.ship_from_lock` (picks ∩ source lock, cap 4) — no AR floor,
> no mega slot, no store ∪ corpus. `tests/test_skill_drift.py` keeps hook patterns
> and ship rules honest. Caption membership is the source lock only. Lock order
> for the menu is `play_count` then 7-day reel max.

The knowledge that drives two things the engine generates: the **on-screen hook**
(big text in a clip's first ~2s) and the **posted caption** — one hook sentence
plus 3–4 tags in the `hashtags` array. The hook author is `prompts._hook_spec`
(what would you say to keep the viewer watching). Caption tags are
lock membership.

## Drift guards (machine-readable; mirror-tested against the code)

<!-- DRIFT-GUARD:patterns — load-bearing phrases of prompts._hook_spec; each must appear lowercased in prompts._hook_spec -->
```text
keep the viewer watching
```

<!-- DRIFT-GUARD:composition — size-band constants on the measurement cache (hashtags.py). Integers only; equals MEGA_MEDIA_FLOOR / MID_MEDIA_FLOOR / INT32_MEDIA_COUNT. NOT applied by ship_from_lock. -->
```text
MEGA_MEDIA_FLOOR=2_000_000
MID_MEDIA_FLOOR=10_000
INT32_MEDIA_COUNT=2_147_483_647
```

## Operator hard rules (override any generic advice below)

1. **Hooks: what would you say to keep the viewer watching?** One short
   line. Do not teach formulas or banned-example lists to the
   author — Review sees the raw draft. The renderer ships the string unchanged.
2. **Max 4 hashtags. Hard.** More than 4 is forbidden. Enforced in code
   ([hashtags.py](../../../src/fanops/hashtags.py) `ship_from_lock`), not by asking
   the model nicely. General guides say "use 20–30" — ignored; the operator rule wins.
   The posted `caption` is one hook sentence; the 3–4 tags live in `hashtags`.
3. **Hashtags come from the source lock via `ship_from_lock`** — never words the
   model invents, never the 80-pile / store ∪ corpus. Membership is the lock
   (`hashtag_store` on every surface of that source). Rank for the lock menu is
   `play_count` then 7-day reel max (`current_top_reel_play_max_7d`). Empty lock
   → empty tag line. Invented / off-lock tags die. No AR floor, no mega slot,
   no `VETTED` pool, no semantic ban-list on the ship path.

---

## Part 1 — On-screen hook

Source of truth: `prompts._hook_spec`. The author sees the clip's frames and writes
**one short line**: what would you say to keep the viewer watching?

No mechanism taxonomy, no formula list, no downstream strip. Review sees what
the LLM drafted.

HOOK placement is Alignment 5 (middle-centre), MarginV 0, `\fad(0,200)` in
`overlay.build_ass`. The renderer ships the string unchanged.

---

## Part 2 — Caption is language; tags are lock membership

### Posted caption

The posted `caption` is **one non-hashtag hook sentence**. The same 3–4 tags live
in the `hashtags` array (hard cap 4). The caption is not the tag line. Tags-only
or missing language HOLDs (`caption_tags_only` / `caption_missing_language`) —
the engine does not manufacture `caption = " ".join(tags)`.

On-screen hook stays the moment gate (`m.hook`). Do not add a caption-item `hook`
field.

### Membership and rank

Membership = **the source lock**. Same list on every surface of that source.
Invented tags die. Off-lock tags die. Empty lock / missing sidecar → empty tag
line (sentence still ships). **Never** the persona 80-pile
(`_per_account_hashtag_stores` / `_aligned_pool`) or store ∪ corpus — those are
not the caption menu.

Rank / choose-key for the lock menu: `play_count` DESC, then
`current_top_reel_play_max_7d` (7-day reel max). `media_count` may appear as a
number on the metrics row; it is **not** the caption menu order.
`size_band` / `size_rank_key` may order cache chips; they are not the caption menu.

### Shipped line (`ship_from_lock`)

- `ship_from_lock(picks, lock, n=4)` — picks ∩ lock, pick order, hard cap 4
- invented / off-lock tags die (not in the intersection)
- empty lock → empty tag line; tags-only HOLD still holds
- **no** AR region floor, **no** mega/untrusted slot cap, **no** backfill
- no `VETTED` / `_MEGA` pool, no semantic ban-list
- no mega / relevance / discovery-slot recipe

`vet_hashtags` is deleted. Posted tags are lock ∩ picks only.

---

## Part 3 — The hashtag LIFECYCLE (where every posted tag comes from)

Authority: `docs/CODEMAPS/hashtag-lifecycle.md`. Summary:

1. **Source lock produce** (`source_tags` + Safari scrape) — per-source sidecar
   `source_tag_locks.json`: pile → lock (positive `play_count`, `play_rank_key`,
   cap 12). Caption surfaces read that lock as `hashtag_store`.
2. **Tick remesure** (`fanops_hashtags.refresh_store_if_due` on the run loop) —
   remesure sidecar pile∪lock names only. **Not** persona discovery, **not**
   `persona_terms`, **not** vocab expand on the loop (HV1-PR4).
3. **Ship** (`caption` → `hashtags.ship_from_lock`) — picks ∩ lock, cap 4. That
   is every posted tag.
4. **Attribution severance** — a tag's worth is Instagram's own number for the
   tag, never a post that used it (`tests/test_hashtag_attribution_severance.py`).

## Wiring (where this lives in the engine)

- [source_tags.py](../../../src/fanops/source_tags.py) — per-source lock produce / sidecar.
- [fanops_hashtags.py](../../../src/fanops/fanops_hashtags.py) — tick remesure
  (`refresh_store_if_due` / `_remesure_sidecar`); manual `refresh_store` for Layer A.
- [hashtags.py](../../../src/fanops/hashtags.py) — `ship_from_lock` (caption ship).
- [caption.py](../../../src/fanops/caption.py) — ingest/compose call `ship_from_lock`
  against the source lock.
- [prompts.py](../../../src/fanops/prompts.py) `caption_prompt` — per-surface
  `hashtag_store` is the source lock.
- [persona_research.py](../../../src/fanops/persona_research.py) — niche terms only; not the posted line.
- [ig_web_scrape.py](../../../src/fanops/ig_web_scrape.py) — Safari XHR for lock + remesure.
- [ig_hashtag_scrape.py](../../../src/fanops/ig_hashtag_scrape.py) — Layer A network helpers
  (manual refresh / harvest).

## Sources

- [vexub] https://vexub.com/blog/viral-short-form-video-hooks
- [OpusClip] https://www.opus.pro/blog/tiktok-hook-formulas
- [Terra Market Group] https://www.terramarketgroup.com/digital-marketing-2/short-form-video-hooks-7-formulas-for-70-retention/
- [go-viral.app] https://www.go-viral.app/blog/hook-first-3-seconds/
- [iqhashtags] https://iqhashtags.com/hashtags/hashtag/hiphop · /rap · /hiphopmusic
- [best-hashtags] https://best-hashtags.com/hashtag/hiphop/ · /rapper/
- [displaypurposes] https://displaypurposes.com/hashtags/hashtag/arabicmusic
- [Buffer] https://buffer.com/resources/tiktok-hashtags/
