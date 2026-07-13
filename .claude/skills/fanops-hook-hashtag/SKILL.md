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
#hiphop #hiphopmusic #rap #rapper #bars #undergroundhiphop #newmusic #lyrics #freestyle #trap #rapmusic #celebritygossip #gossip #entertainmentnews #celebritynews #popculture #drama #entertainment #celebrity #arabicmusic #arabtiktok #arabicmusiclovers #fyp #foryou #viral #reels
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

## Operator hard rules (override any generic advice below)

1. **Hooks are RETENTION mechanics, NOT artist hype.** The line is about the
   *viewer's attention*, never about how great the artist is. "wait for the last
   line" — yes. "his coldest verse ever" — no. Hyping the artist is explicitly
   banned. Ground only enough that the clip *pays the loop off* (no bait).
2. **Max 4 hashtags. Hard.** More than 4 is forbidden. Enforced in code
   ([hashtags.py](../../../src/fanops/hashtags.py) `vet_hashtags`), not by asking
   the model nicely. General guides say "use 20–30" — ignored; the operator rule wins.
3. **Hashtags come from the reach-vetted FLOOR + live-discovered, operator-curated tags** —
   never words the model invents. The frozen set (below) is the cold-start FLOOR, not the
   ceiling: per-persona corpora curated from live Graph **co-occurrence discovery** join the
   membership and lead selection (Part 3). The frozen counts below are a class ranking, sourced.

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

### Render concerns (deferred)

Stacked EN/AR on-screen typography, the 100–200ms caption-lead **anticipation timing**, and central
**safe-zone Y** placement are *render* concerns (`overlay.build_ass`), out of scope for the generator
and deferred to a future render PR. Today the author emits a **single high-contrast string** the
renderer ships unchanged; the safe-zone work should also re-check the current top-third placement.

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

## Part 3 — The hashtag LIFECYCLE (where every posted tag comes from)

The lifecycle is now explicit, per-persona, and closed-loop. Top to bottom:

1. **A persona owns a curated corpus.** A `Persona` ([personas.py](../../../src/fanops/personas.py))
   is a first-class record in `00_control/personas.json` — `voice`, `tag_lean`, a
   **`hashtag_corpus`** (the per-persona pool), and the lever fields. Accounts link via
   `Account.persona_id`; the persona's voice/lean/corpus
   **hydrate** the account at load. Edited entirely in the Studio **Personas** tab.
2. **Where corpus tags come from — four sources, all visible in the Personas tab:**
   - **Live co-occurrence discovery** (`personas.discover_corpus` → `meta_graph.discover_candidates`):
     the **Research corpus** button resolves the persona's category seeds (corpus + lean pool),
     reads each seed's live `top_media`, and harvests the hashtags those
     *currently-winning* posts use alongside it — ranked by co-occurrence count. This is the only
     Graph-native way to surface a tag the system has **never named** (IG has no trending-by-topic
     endpoint); the harvest is budget-free (one `ig_hashtag_search` slot per seed, the caption read
     is free). FAIL-OPEN to the offline re-rank below without Meta creds. The periodic equivalent is
     `fanops hashtags discover` (reports per persona; never writes the menu).
   - **Offline bootstrap re-rank** (`personas.research_corpus`) — the fallback: the reach-best tags a
     persona lacks from the live Graph-reach store (instant, no extra Graph call).
   - **Operator recommend** (`meta_graph.tag_metrics`): type any candidate tag → the **Meta Graph API**
     returns its live IG reach (top-media engagement), one `ig_hashtag_search` budget slot (30 / 7 days)
     → Add to corpus. Per-tag evidence behind a curation decision.
   - **The frozen reach-vetted set** (Part 2) is the cold-start FLOOR only.
   Every source PROPOSES; the operator ACCEPTS into the corpus (the curation gate). Discovery never
   auto-writes a tag into a caption — a discovered tag ships only after the operator adds it.
3. **Selection** ([hashtags.py](../../../src/fanops/hashtags.py) `vet_hashtags`): at caption
   ingest, the linked persona's `corpus` JOINS the vetted membership (a curated tag the
   frozen set doesn't know now SURVIVES) and is the **priority pool** — it leads the ≤4,
   then the reach order. Hard cap 4, deterministic, never empty.
4. **Presentation to the model** (`caption_prompt`): each surface's `corpus` rides the
   payload + a rule tells the model to prefer it; the deterministic gate guarantees the
   corpus leads regardless.
5. **The store is judged by LIVE Meta Graph reach** — NEVER by a post that used a tag (a post's
   success/failure attributes to the hook/clip/account, never the hashtag; pinned by
   `tests/test_hashtag_attribution_severance.py`). `fanops_hashtags.refresh_store` harvests
   co-occurring candidates from the niche seeds, measures their Graph reach within the 30/7-day
   budget, ranks by reach, and writes the `{tags, reach}` store (`FANOPS_HASHTAG_TRENDS` **defaults
   ON**, fail-open to the frozen floor without a Meta token); `refresh_store_if_due` re-runs it on a
   12h throttle inside `fanops run`.
6. **Surfaced:** the Personas tab renders each corpus **reach-ranked**, flags currently-most-active
   (store-present) tags ★, and shows each curated tag's **live Graph reach** — so a curation decision
   is backed by what is actually reaching on the platform now.

## Wiring (where this lives in the engine)

- [personas.py](../../../src/fanops/personas.py) — the `Persona` entity + registry + the
  curated `hashtag_corpus` writers + `research_corpus` (bootstrap proposal). Accounts link
  via `Account.persona_id`; the corpus hydrates onto the account at load.
- [hashtags.py](../../../src/fanops/hashtags.py) — the vetted set as code constants
  (seeded from the table above) + `vet_hashtags(tags, platform, language, max=4, *, store, corpus, content)`:
  the corpus joins the membership + leads, then restricts to the vetted set, reach-orders,
  caps at 4. The **hard enforcement** — whatever the model returns is filtered through this.
- [meta_graph.py](../../../src/fanops/meta_graph.py) `tag_metrics` — on-demand live IG reach
  for ONE tag (the operator-recommend evidence), budget-bounded; `harvest_cooccurring` /
  `sample_trends` feed the store build.
- [fanops_hashtags.py](../../../src/fanops/fanops_hashtags.py) — `refresh_store` (builds the store
  from live Graph reach: harvest → measure → rank) + `refresh_store_if_due` (12h-throttled run-loop
  refresh). The own-post-reach attribution model was deleted — a hashtag is judged only by Graph reach.
- [studio/personas.py](../../../src/fanops/studio/personas.py) — the Studio **Personas** tab
  actions (add/edit/connect, curate corpus, recommend, research).
- [prompts.py](../../../src/fanops/prompts.py) `hookedit_prompt` — retention patterns
  above; the artist-hype mandate is removed.
- [prompts.py](../../../src/fanops/prompts.py) `caption_prompt` — the model picks ≤4
  FROM the vetted set / the surface's corpus; it does not invent tags.

## Sources

- [vexub] https://vexub.com/blog/viral-short-form-video-hooks
- [OpusClip] https://www.opus.pro/blog/tiktok-hook-formulas
- [Terra Market Group] https://www.terramarketgroup.com/digital-marketing-2/short-form-video-hooks-7-formulas-for-70-retention/
- [go-viral.app] https://www.go-viral.app/blog/hook-first-3-seconds/
- [iqhashtags] https://iqhashtags.com/hashtags/hashtag/hiphop · /rap · /hiphopmusic
- [best-hashtags] https://best-hashtags.com/hashtag/hiphop/ · /rapper/
- [displaypurposes] https://displaypurposes.com/hashtags/hashtag/arabicmusic
- [Buffer] https://buffer.com/resources/tiktok-hashtags/
