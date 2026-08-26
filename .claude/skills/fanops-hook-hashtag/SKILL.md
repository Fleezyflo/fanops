---
name: fanops-hook-hashtag
description: Use when writing or reviewing on-screen HOOKS or HASHTAGS for FanOps clips. The hook's only job is RETENTION (stop the scroll, force watch-through) — never artist praise. The posted caption is one hook sentence; 3–4 tags live in the hashtags array (hard cap 4), chosen via ship_from_lock from the source lock only — never words the model invents, never the 80-pile / store∪corpus, never AR floor / mega composition. Evidence-backed; sources cited inline.
---

# FanOps Hooks & Hashtags — researched, platform-measured

> **Source of truth = code.** Hook patterns live in `prompts._hook_spec`. Caption
> ship is `hashtags.ship_from_lock` (picks ∩ source lock, cap 4) — no AR floor,
> no mega slot, no store ∪ corpus. The DRIFT-GUARD blocks below still mirror
> constants that exist for Layer B / legacy `vet_hashtags` (observatory only —
> not posted tags); `tests/test_skill_drift.py` keeps those lists honest. Edit
> code + doc together. Caption membership is the source lock only. Lock order
> for the menu is `play_count` then 7-day reel max, not `size_rank_key`.

The knowledge that drives two things the engine generates: the **on-screen hook**
(big text in a clip's first ~2s) and the **posted caption** — one hook sentence
plus 3–4 tags in the `hashtags` array. Both were freestyled by the model before
this skill existed — hooks paraphrased the lyric transcript ("shackled up, feels
like flying"), hashtags were 5–15 random words. Both are now grounded in what
actually works, with proof.

## Drift guards (machine-readable; mirror-tested against the code)

<!-- DRIFT-GUARD:hashtags — legacy Layer B / vet_hashtags AR region list (hashtags._ARABIC), sorted. NOT the caption ship path (ship_from_lock has no floors). Format constant only; not a reach claim. -->
```text
#arabicmusic #arabicmusiclovers #arabtiktok
```

<!-- DRIFT-GUARD:patterns — the proven hook MECHANISMS (4 psychological triggers + 5 evidence-rewrite mechanisms); each must appear lowercased in prompts._hook_spec -->
```text
curiosity gap
pattern interrupt
self-relevance
emotional arousal
result-first
atmospheric pov
peer-challenge
social proof
fomo
```

<!-- DRIFT-GUARD:composition — legacy Layer B / vet_hashtags band + slot constants (hashtags.py). Integers only; equals MEGA_MEDIA_FLOOR / MID_MEDIA_FLOOR / INT32_MEDIA_COUNT / MEGA_SLOT_MAX. NOT applied by ship_from_lock. -->
```text
MEGA_MEDIA_FLOOR=2_000_000
MID_MEDIA_FLOOR=10_000
INT32_MEDIA_COUNT=2_147_483_647
MEGA_SLOT_MAX=1
```

## Operator hard rules (override any generic advice below)

1. **Hooks are RETENTION mechanics, NOT artist hype.** The line is about the
   *viewer's attention*, never about how great the artist is. "wait for the last
   line" — yes. "his coldest verse ever" — no. Hyping the artist is explicitly
   banned. Ground only enough that the clip *pays the loop off* (no bait).
2. **Max 4 hashtags. Hard.** More than 4 is forbidden. Enforced in code
   ([hashtags.py](../../../src/fanops/hashtags.py) `ship_from_lock`), not by asking
   the model nicely. General guides say "use 20–30" — ignored; the operator rule wins.
   The posted `caption` is one hook sentence; the 3–4 tags live in `hashtags`.
3. **Hashtags come from the source lock via `ship_from_lock`** — never words the
   model invents, never the 80-pile / store ∪ corpus. Membership is the lock
   (`hashtag_store` on every surface of that source). Rank for the lock menu is
   `play_count` then 7-day reel max (`current_top_reel_play_max_7d`). Empty
   lock → empty tag line. Invented / off-lock tags die. No AR floor, no mega
   slot, no `VETTED` pool, no semantic ban-list on the ship path.

---

## Part 1 — Retention hook patterns (proven)

### Why the first 3 seconds are the whole game

- The first 3 seconds drive **~80% of completion variance**; 2026 algorithms
  prioritise "intro retention" — % who watch past 3s. [vexub], [TikTok for Business via OpusClip]
- Videos holding **≥65% of viewers past 3s get 4–7× more impressions**. [OpusClip]
- **Layered hooks** (visual motion + text + audio) lift 3-second holds **~3×** vs a
  static text-only intro; rapid zoom beats a static shot ~2.5× in muted playback. [vexub]
- A hook should land in **~10–14 words / under 3s**. [vexub], [Terra Market Group]

### The craft: 4 proven TRIGGERS + force multipliers

A hook works by firing at least one of four psychological **triggers** in the first ~2s; the
strongest **stack** two or three. (These are the DRIFT-GUARD:patterns, mirrored in `_hook_spec`.)

| Trigger | The cognitive event | Music-clip example |
|---|---|---|
| **Curiosity gap** / open loop | "wait — what?" (the brain must close the gap) | "the part you'll replay" · "wait for what he admits" |
| **Pattern interrupt** / contrarian | "that's not what I expected" | "maybe your favorite artist copied too" · "nobody this good should be this unknown" |
| **Self-relevance** / identity | "that's me / that's for me" (2026's highest-scoring) | "this one's for who you can't get over" · "you ever felt that?" |
| **Emotional arousal** | "I *feel* that" (awe, longing, betrayal, devotion) | "you don't expect a rapper to make you pray" |

**Force multipliers** (these separate a hook that hits from one that dies):
- **Specific — about the VIEWER, not the clip.** Name the viewer's feeling/identity so they
  recognise themselves in <2s. A *universal shared feeling* is fine; *vague* is not. Do not
  describe the clip's plot.
- **Zero throat-clearing** — open ON the trigger.
- **Raw + spoken** — how a real person talks, not polished copy.
- **Stack two triggers** when the clip allows.

Sources: [OpusClip] (34,635-clip study: Identity Call / Contrarian / Open Loop / Confession score
highest; ≥65% 3-s benchmark), [vexub] (curiosity-gap + pattern-interrupt mechanics), [Terra Market
Group] (retention formulas), [go-viral.app] (first-3-seconds). Success is **proven + measurable**,
not taste — the viewer-POV meter + the learning loop pick winners from real data.

### Banned in hooks (these are why the old output was garbage)

- **Third-person scene-narration** — recapping what happens to the artist ("he stopped answering
  for a reason", "front row last song", "started in a bedroom copying his brother"). It fires NO
  trigger. The frame is the viewer, never a recap.
- **Artist praise / hype** ("his hardest bar", "GOAT", "🐐").
- **Lyric paraphrase** — restating the bar they can already hear; NOT a caption, NOT a quote.
- **Generic filler that names no feeling and fits any clip** ("his coldest opener").
- **Hooking on the editing** ("watch how this cuts", "drone up").
- **Set-dressing / scenery curiosity** — a question about what the frame merely LOOKS like ("why is
  the room bleeding red", "why is the clock frozen", "why are they standing like that"). The clip
  never answers it, so it's bait; it describes the SET, not the viewer. (Live incident 2026-07-13:
  a red-lit shoot-BTS window produced six of these.)
- **Bait** the clip doesn't pay off.

A clip with no honest hook is better **clean** (hook = null) than slop — and the generator's OUTPUT
rule says exactly that: hook whenever an honest one exists; null for a window with no verbal/event
anchor (song playback, b-roll, set logistics). Null is a last resort, never a shortcut.

---

## Part 1b — The full mechanism taxonomy (exhaustive reference)

The **prompt is selective by design**: only the fan-relevant mechanisms enter the generator —
the 4 triggers + 5 mechanisms carry their craft + fail-condition in `_hook_spec` (and the
DRIFT-GUARD:patterns block above mirrors all 9), and the input-dependent *selection* of which one
fits a clip lives in `_hook_decision` (moment-only). Dumping all 13 into the prompt contradicts the
selection spec and worsens few-shot parroting — the diagnosed failure mode. This table is the
exhaustive set for **reference**; the "Where" column says what actually reaches the model.

| Mechanism | Evidence (3s-hold / lift) | Fail-condition | Where |
|---|---|---|---|
| **Result-First** | ≥80% hold (measured) | the chaotic *before* drags past ~3s | prompt (`_hook_spec` + decision B) |
| **Open-Loop** | ≥78% hold (measured) | the loop never pays off (bait) | prompt (trigger 1) |
| **Contrarian** | ≥75% hold (measured) | the contrarianism is hollow | prompt (trigger 2) |
| **Curiosity-Gap** | ≥70% hold (measured) | the gap is never closed | prompt (trigger 1) |
| **Pattern-Interrupt** | +36% completion (measured) | interrupt with no point behind it | prompt (trigger 2) |
| **Identity / Self-Relevance** | 2026's highest-scoring | addresses no one in particular | prompt (trigger 3) |
| **Emotional Arousal** | high-arousal only | a low-arousal mood (scrolled past) | prompt (trigger 4) |
| **Atmospheric POV** | widely claimed | reads as a marketing directive | prompt (`_hook_spec` + decision A) |
| **Peer-Challenge** | widely claimed | a hollow dare the clip can't earn | prompt (`_hook_spec` + decision B) |
| **Social-Proof / Devotional** | widely claimed | the validation reads fabricated | prompt (`_hook_spec`) |
| **FOMO / Scarcity** | widely claimed | the urgency is artificial | prompt (`_hook_spec`) |
| **Warning / Negativity** | widely claimed | off-brand for a fan repost (creator-voice) | **doc-only** |
| **Specificity / Concrete-Numbers** | widely claimed | the author has no real stats → fabrication | **doc-only** (the spec instead BANS round/clickbait numbers) |

### Selection hierarchy (D1 — mirrored in `_hook_decision`)

Read the clip's **visual** energy (frames) + **audio** transient (signal peaks) + **register**
(dialect), then pick:

- **A — low-energy / atmospheric opening** → Atmospheric POV or Curiosity Gap.
- **B — high-energy / hard drop or punchline** → Result-First or Peer-Challenge (destination by ~3s).
- **C — dense Arabic verse** non-Arabic scrollers can't parse → Curiosity/Tension as a high-contrast
  **English** hook that frames the feeling (never a literal translation).

HOOK placement is Alignment 5 (middle-centre),
MarginV 0, `\fad(0,200)` in `overlay.build_ass`. Existing Review `{cid}.mp4` files burned with the
old top-third hook are recut in place by `fanops overlay-reburn` (ass-only; proved crop; never
center fail-open). The author still emits a **single high-contrast string** the renderer ships
unchanged.

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
`size_band` / `size_rank_key` are Layer B observatory derivation (Part 3), not
the caption menu.

### Shipped line (`ship_from_lock`)

- `ship_from_lock(picks, lock, n=4)` — picks ∩ lock, pick order, hard cap 4
- invented / off-lock tags die (not in the intersection)
- empty lock → empty tag line; tags-only HOLD still holds
- **no** AR region floor, **no** mega/untrusted slot cap, **no** backfill
- no `VETTED` / `_MEGA` pool, no semantic ban-list
- no mega / relevance / discovery-slot recipe

`vet_hashtags` (store ∪ corpus, AR floor, `MEGA_SLOT_MAX`) remains in tree as
Layer B / legacy composition — it does **not** write the posted tag line.

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
4. **Layer B — observatory / legacy** (`persona_research.derive_corpus`,
   `vet_hashtags`) — zero-network corpus derivation and old store∪corpus
   composition. Still on disk; **does not** decide posted tags. Studio / discover
   may show it; caption does not ship it.
5. **Attribution severance** — a tag's worth is Instagram's own number for the
   tag, never a post that used it (`tests/test_hashtag_attribution_severance.py`).

## Wiring (where this lives in the engine)

- [source_tags.py](../../../src/fanops/source_tags.py) — per-source lock produce / sidecar.
- [fanops_hashtags.py](../../../src/fanops/fanops_hashtags.py) — tick remesure
  (`refresh_store_if_due` / `_remesure_sidecar`); manual `refresh_store` for Layer A.
- [hashtags.py](../../../src/fanops/hashtags.py) — `ship_from_lock` (caption ship);
  `vet_hashtags` / band constants = Layer B legacy only.
- [caption.py](../../../src/fanops/caption.py) — ingest/compose call `ship_from_lock`
  against the source lock.
- [prompts.py](../../../src/fanops/prompts.py) `caption_prompt` — per-surface
  `hashtag_store` is the source lock.
- [persona_research.py](../../../src/fanops/persona_research.py) — Layer B observatory
  (`derive_corpus`, `refresh_corpora_if_due`); not the posted line.
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
