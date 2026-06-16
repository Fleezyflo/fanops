---
name: fanops-hook-hashtag
description: Use when writing or reviewing on-screen HOOKS or HASHTAGS for FanOps clips. The hook's only job is RETENTION (stop the scroll, force watch-through) — never artist praise. Hashtags are capped at 4, hard, and chosen from a reach-vetted set ranked by real post volume — never words the model invents. Evidence-backed; sources cited inline.
---

# FanOps Hooks & Hashtags — researched, reach-vetted

The knowledge that drives two things the engine generates: the **on-screen hook**
(big text in a clip's first ~2s) and the **hashtag caption**. Both were freestyled
by the model before this skill existed — hooks paraphrased the lyric transcript
("shackled up, feels like flying"), hashtags were 5–15 random words. Both are now
grounded in what actually works, with proof.

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

### The pattern library (use these; adapt the slot to the clip)

Every pattern is a watch-mechanic. The `{slots}` are filled from the clip's actual
content so the loop is true. Hooks are ≤6 words, in the clip's own language.

| # | Pattern | Mechanic | Template | Music-clip example |
|---|---|---|---|---|
| 1 | **Open loop / payoff tease** | watch-to-end | `wait for the {payoff}` | "wait for the last line" · "it switches at the end" |
| 2 | **Curiosity gap** | intrigue, unresolved | `the {thing} nobody {verb}` · `you're not ready for {x}` | "the part nobody clipped" · "you're not ready for the drop" |
| 3 | **Comment / opinion bait** | engagement → reach | `is this his {superlative}?` · `rate this {1-10}` | "is this the hardest verse?" · "rate this beat 1-10" |
| 4 | **Contrarian / bold claim** | pattern interrupt | `everyone slept on {x}` · `this shouldn't be {status}` | "everyone slept on this" · "this shouldn't be unsigned" |
| 5 | **POV / relatable** | self-relevance | `POV: {viewer scenario}` | "POV: you found him first" · "when the beat finally drops" |
| 6 | **Proof-first / stakes** | credibility, concrete | `{number/constraint}, {result}` | "one take, no autotune" · "0 budget, all bars" |

Sources for the formulas: [vexub] (Contrarian Claim, Mistake Warning, List Tease as
the top-3 cross-niche 2026 hooks; curiosity-gap mechanics), [OpusClip] (Bold
Statement / Question / Pattern-Interrupt / Proof-First and the ≥65% 3-s benchmark),
[Terra Market Group] (7 formulas for 70%+ retention), [go-viral.app] (first-3-seconds).

### Banned in hooks (these are why the old output was garbage)

- **Artist praise / hype** of any kind ("his hardest bar", "GOAT", "🐐").
- **Lyric paraphrase** — restating what's being said instead of teasing it. The
  on-screen text must NOT caption the audio.
- **Generic filler that fits any clip** ("his coldest opener", "the bar everyone replayed").
- **Hooking on the editing** ("watch how this cuts").
- **Bait** the clip doesn't pay off.

A clip with no honest retention hook is better **clean** (hook = null) than slop.

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
