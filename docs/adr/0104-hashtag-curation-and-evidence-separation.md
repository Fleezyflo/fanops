---
status: accepted
date: 2026-07-16
accepted: 2026-07-16
supersedes: []
references: [C5, "docs/CODEMAPS/hashtag-lifecycle.md", "PR #679", ".reports/hashtag-generic-identical-diagnosis-2026-07-16.md"]
deciders: [operator]
---

# ADR-0104 — The curated hashtag corpus and the discovery store are separate authorities

> **Accepted 2026-07-16.** Records the architectural principle and the one structural cut that enforces it.
> Tag choices themselves are data, not architecture, and are not decided here.

## Status

**Accepted.** No prior catalogue slug: the hashtag lifecycle had an *implicit, unrecorded* design in which
"the curated corpus" and "the measured evidence store" were the same population of tags flowing in a circle.
Nothing in the code or the data said so, and every artifact looked like evidence. This ADR names the
separation that was always intended and makes the circularity structurally impossible.

## Context — the failure this records

Measured on live control data and the 347-post ledger, 2026-07-16:

- `fanops_hashtags._seed_tags` **builds the discovery store out of every persona's `hashtag_corpus`**.
- `persona_research.research_corpus` proposed new corpus tags from `vetted_menu(load_store(cfg))` — **the
  store, re-ranked**.
- `persona_research.refresh_persona_corpus` wrote those proposals back into the corpus as `auto` entries,
  on a daemon tick.

So: **corpus → store → corpus**, closed, with no external evidence anywhere in it. The live store was
**byte-identical** to `seeds + frozen floor` — 53 tags, **0 discovered, `reach: {}`** — while every proposal
it produced was presented as research. The whole Meta budget (30 unique `ig_hashtag_search` / rolling 7 days)
had been spent re-measuring **30/30 tags already in a corpus**.

The consequence reached production. The curated corpora accumulated tags that could not describe any clip in
this catalogue — `#taylorswift`, `#80s`, `#instagood`, `#love`, `#explore`, a malformed
`#fypppppppppp…` (73 `p`s), and the **entire Wu-Tang Clan** (`#methodman`, `#rza`, `#wutang`, …) on a Syrian
rapper's interview catalogue. Two handles shipped Wu-Tang tags on **93%** of their posts.

PR #679 fixed the *selector* (the clip can now reach its own line). It did not, and could not, fix the
*population the selector draws from*.

## Decision

**Three authorities, named, with one-way edges.**

1. **Curated brand corpus** (`personas.json:hashtag_corpus`) — human-governed, persona-specific, versioned
   in the control file, `pinned` in `hashtag_corpus_meta`. It is **brand data**. It is never rewritten from
   the discovery store. It holds the curated *lead* (`_CORPUS_LEAD_MAX = 2` of 4).
2. **Discovery / evidence store** (`hashtags.json`) — **measured evidence**, not curation. Each entry carries
   `{reach, measured_at, source, confidence}`. Evidence accrues and is never destructively overwritten
   (PR #679's H3); it expires rather than curating forever.
3. **Content-derived candidates** — the model's per-clip picks, already membership-gated by `vetted`. These
   carry the clip's own relevance into the remaining slots (PR #679's H1).

**The cut that makes the loop impossible:** a tag may be **proposed for curation only if it carries real,
unexpired Graph measurement** (`source == "graph-reach"`, a parseable `measured_at`, positive reach, within
`_EVIDENCE_MAX_AGE_DAYS`). A corpus tag echoed into the store as an unmeasured **seed** carries none, so it
can never be proposed back. The edge is severed **by the data model**, not by a rule someone must remember.

**Promotion requires deterministic gates.** Evidence is necessary but not sufficient: a candidate must also
pass `hashtag_hygiene.tag_defect` (structural) before it can become curated data. Reach cannot buy junk a
curated slot.

**Hygiene is structural only, and deliberately so.** Malformed keysmash, generic engagement bait, and
platform discovery tags (which `vet_hashtags` already floors per platform — a corpus copy is a duplicate
lever) are machine-decidable and are refused at the write boundary. **Semantic fit is not attempted.** An
off-catalogue denylist is unbounded and would be guesswork dressed as a rule; "is `#taylorswift` right for
this artist" is the operator's judgement. That is *why* the corpus is human-governed.

## Consequences

- `research_corpus` returns `[]` when nothing has been measured. **Honest silence replaces a confident echo.**
  With the Meta budget exhausted (until ~2026-07-19) this is the common case, and it is correct.
- The curated corpora are small (2–4 tags) **on purpose**. Post-#679 the corpus no longer carries
  differentiation — it holds 2 slots of brand identity while the clip holds 2 of relevance. Padding to a
  quota would re-crowd the clip out of its own line.
- Legacy bare `reach` numbers read back as `source: "unknown"`, `measured_at: None`, and therefore **cannot
  curate**. We do not know their provenance; refusing to act on them is the correct consequence of not
  knowing, and back-dating them would manufacture exactly the false confidence this ADR exists to prevent.
- Dormant personas (no linked account) still seed the store, which is how `#science` and `#celebritygossip`
  reached a rap artist's menu. Their corpora are cleaned; their `intake.genre` is **not** — see Residuals.

## Residuals (accepted, recorded, not fixed here)

1. **The model repeats itself.** Replaying the real per-surface picks (`meta_captions.hashtags_raw`) across
   all 347 live posts: the model produced only **6–15 distinct pick-sets per handle over 66–76 surfaces, with
   54–76% concentration on a single set**. This is now the *dominant* remaining cause of near-identical lines.
   It is upstream of both #679 and this ADR — partly caused by the polluted corpus itself (the prompt tells
   the model to *prefer* the corpus, and the corpus was junk), so it should improve once clean corpora are
   live, but that is unproven until captions are regenerated.
2. **Dormant personas carry catalogue-wrong `intake.genre`** (`science`, `gossip`). They ship nothing today;
   activating one would backfill its genre's niche floor. Deliberately out of scope.
3. **The 12h refresh vs the 7-day budget window** is now harmless (evidence accrues) but still wasteful:
   ~13 of every 14 refreshes measure nothing.
