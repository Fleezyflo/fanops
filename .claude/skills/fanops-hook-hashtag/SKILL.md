---
name: fanops-hook-hashtag
description: Use when writing or reviewing on-screen HOOKS or HASHTAGS for FanOps clips. The hook's only job is RETENTION (stop the scroll, force watch-through) — never artist praise. Hashtags are capped at 4, hard, and chosen from a reach-vetted set ranked by real post volume — never words the model invents. Evidence-backed; sources cited inline.
---

# FanOps Hooks & Hashtags — researched, reach-vetted

> **Source of truth = code.** The runtime values live in `hashtags.VETTED` and
> `prompts._hook_spec`; this file documents them. The DRIFT-GUARD blocks below are
> mirror-tested by `tests/test_skill_drift.py` — if this doc and the code disagree,
> that test goes red. Edit code + doc together.

The knowledge that drives two things the engine generates: the **on-screen hook**
(big text in a clip's first ~2s) and the **hashtag caption**. Both were freestyled
by the model before this skill existed — hooks paraphrased the lyric transcript
("shackled up, feels like flying"), hashtags were 5–15 random words. Both are now
grounded in what actually works, with proof.

## Drift guards (machine-readable; mirror-tested against the code)

<!-- DRIFT-GUARD:hashtags — the reach-vetted set; must equal hashtags.VETTED exactly -->
```text
#hiphop #hiphopmusic #rap #rapper #bars #undergroundhiphop #newmusic #arabicmusic #arabtiktok #arabicmusiclovers #fyp #foryou #viral #reels
```

<!-- DRIFT-GUARD:patterns — the 4 proven psychological TRIGGERS a hook fires; each must appear in prompts._hook_spec -->
```text
curiosity gap
pattern interrupt
self-relevance
emotional arousal
```

## Operator hard rules (override any generic advice below)

1. **Hooks are RETENTION mechanics, NOT artist hype.** The line is about the
   *viewer's attention*, never about how great the artist is. "wait for the last
   line" — yes. "his coldest verse ever" — no. Hyping the artist is explicitly
   banned. Ground only enough that the clip *pays the loop off* (no bait).
2. **Max 4 hashtags. Hard.** More than 4 is forbidden. Enforced in code
   ([hashtags.py](../../../src/fanops/hashtags.py) `vet_hashtags`), not by asking
   the model nicely. General guides say "use 20–30" — ignored; the operator rule wins.
3. **Hashtags come from the reach-vetted set, ranked by real post volume.** Not
   words the model chooses. The set + counts are below, sourced.

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
- **Bait** the clip doesn't pay off.

A clip with no honest hook is better **clean** (hook = null) than slop.

---

## Part 2 — Reach-vetted hashtags (proven post volumes)

### The set, ranked by real volume

Counts are platform post-counts at research time (June 2026). Reach class, not a
live API — re-verify before treating a number as current.

| Tag | Posts (≈) | Class | Source |
|---|---|---|---|
| #hiphop | 504M | mega | [iqhashtags] |
| #hiphopmusic | 113M | mega | [iqhashtags] |
| #rap | 113M | mega | [iqhashtags / best-hashtags] |
| #rapper | high (8-fig) | large | [best-hashtags] |
| #bars | large | niche-genre | [iqhashtags] |
| #newmusic | large | discovery-music | [iqhashtags] |
| #undergroundhiphop | mid | niche-relevance | [iqhashtags] |
| #fyp / #foryou | ubiquitous | platform-discovery | [Buffer / TikTok] |
| #viral | ubiquitous | platform-discovery | [Buffer] |
| #arabicmusic | 195K | language/region | [displaypurposes] |
| #arabicmusiclovers | 7.4K | language-niche | [displaypurposes] |
| #arabtiktok / #اغاني | mid (regional) | language/region | [displaypurposes / TikTok] |

### Selection rule (≤4, deterministic)

A balanced 4 beats 4 mega-tags (mega-only = drowned instantly; niche-only = no
reach). Compose the 4 as:

1. **One mega genre tag** — #hiphop / #rap / #hiphopmusic (reach).
2. **One relevance tag** — #rapper / #bars / #undergroundhiphop (targets the right feed).
3. **One language/region tag IF the clip is Arabic** — #arabicmusic / #arabtiktok;
   else a second relevance/discovery-music tag (#newmusic).
4. **One platform-discovery tag** — #fyp (TikTok) / #foryou; reach is contested but
   it's the standard surface tag.

Mixing languages is fine (English #fyp on an Arabic clip is normal — reach beats
language purity). The hard cap is 4 regardless.

---

## Wiring (where this lives in the engine)

- [hashtags.py](../../../src/fanops/hashtags.py) — the vetted set as code constants
  (seeded from the table above) + `vet_hashtags(tags, platform, language, max=4)`:
  restricts to the vetted set, reach-orders, caps at 4. The **hard enforcement** —
  whatever the model returns is filtered through this.
- [prompts.py](../../../src/fanops/prompts.py) `hookedit_prompt` — retention patterns
  above; the artist-hype mandate is removed.
- [prompts.py](../../../src/fanops/prompts.py) `caption_prompt` — the model picks ≤4
  FROM the vetted set; it does not invent tags.

## Sources

- [vexub] https://vexub.com/blog/viral-short-form-video-hooks
- [OpusClip] https://www.opus.pro/blog/tiktok-hook-formulas
- [Terra Market Group] https://www.terramarketgroup.com/digital-marketing-2/short-form-video-hooks-7-formulas-for-70-retention/
- [go-viral.app] https://www.go-viral.app/blog/hook-first-3-seconds/
- [iqhashtags] https://iqhashtags.com/hashtags/hashtag/hiphop · /rap · /hiphopmusic
- [best-hashtags] https://best-hashtags.com/hashtag/hiphop/ · /rapper/
- [displaypurposes] https://displaypurposes.com/hashtags/hashtag/arabicmusic
- [Buffer] https://buffer.com/resources/tiktok-hashtags/
