# Brief — Hashtag model diversity

**Unit tag:** `Unit: hashtag-model-diversity`
**Status:** brief only — no implementation in this document. **Nothing here may start before the evidence gate in §3 is satisfied.**
**Boundary:** R4 is closed and frozen (ADR-0104, [`r4-migration-record.md`](../../CODEMAPS/r4-migration-record.md)). This brief inherits R4's residual #1 and nothing else. §7 lists what it may not touch.

---

## 1. The problem, as measured

Replaying the model's **real recorded picks** (`meta_captions.hashtags_raw`) across all 347 live posts, per posting handle:

| Measure | Observed |
|---|---|
| surfaces per handle | 66–76 |
| distinct pick-sets per handle | **6–15** |
| share of a handle's surfaces on its single modal set | **54–76%** |

Handed a menu, the model converges on a modal answer. After R4 this is the **dominant** remaining cause of near-identical hashtag lines: the menu is now clean, persona-specific and non-circular, but a clean menu does not make the model choose differently *from* it.

Ranges, not a per-handle table, because ranges are what was measured. Do not quote a per-handle figure until §6's replay produces one.

**Do not read these numbers as a defect budget.** Per §2, a large part of that repetition is now correct.

---

## 2. The trap: some repetition is the design

Post-#679 the shipped line is **4 slots**:

- **2** — curated brand identity (`_CORPUS_LEAD_MAX`), drawn from a 2–4 tag corpus
- **≤1** — platform discovery floor (`#fyp` / `#reels` / `#viral`), one per platform, **by design**
- **the remainder** — the clip's own vetted picks

With `craft-curator`'s corpus at `#bars #lyrics #hiphopmusic`, the lead has roughly three combinations — **and that is the point.** It is the brand identity R4 exists to guarantee (ADR-0104: curated identity on 100% of lines).

**Therefore diversity must be measured on the clip-derived slots only, holding the lead fixed.** A whole-line diversity metric is maximised by deleting the curated lead — that is, by undoing R4. Any proposal that improves the metric by shrinking the lead is rejected by construction, not on judgement.

---

## 3. Evidence gate — required before any change

Every number in §1 was produced against the **polluted** corpus. They cannot attribute the repetition, because the prompt told the model to *prefer* a corpus that was junk. **No mechanism may be diagnosed and no code changed until picks exist that were generated against the clean menu.**

Two ways to get them:

| Route | Cost | Latency |
|---|---|---|
| Force-regenerate captions across the 347-post corpus | **real LLM spend** (`FANOPS_RESPONDER=llm` is the live setting) — order 347 calls | hours |
| Let the daemon generate naturally as clips flow | **free** — already paid for | days |

Prefer the free route unless the operator wants the answer sooner and accepts the spend. Minimum usable sample: **≥30 surfaces per posting handle**, or the concentration figure is noise.

---

## 4. Differential diagnosis — four mechanisms, one decision tree

The repetition is prompt-, sampling-, selector-, or model-behaviour-driven. These are distinguishable with data that already exists per surface; run the tree in order and stop at the first hit.

1. **Are two clips' prompts materially different?** Hash the `caption_prompt` output per surface.
   - **Prompts identical across clips** → the model cannot be blamed. The defect is **prompt construction**: the clip's own signal (transcript, entities) is not reaching the prompt. Fix upstream. Stop.
2. **Prompts differ. Does `hashtags_raw` (the model's raw picks, pre-selector) vary?**
   - **Raw varies, shipped line does not** → **selector**-driven. `vet_hashtags` is collapsing distinct inputs onto one output (cap 4, lead 2, floor ≤1 — see §2). Measure the free-slot headroom before touching anything else.
   - **Raw does not vary** → model or sampling. Go to 3.
3. **Does raw vary when temperature / top-p are raised on identical prompts?**
   - **Yes** → **sampling**-driven. Responder configuration, not a prompt problem.
   - **No** → **model behaviour** — mode collapse toward genre-typical tags. Needs prompt-level intervention (e.g. requiring the model to justify each pick from the transcript). This is the expensive branch; do not assume it.

**Prior, stated up front so it can be falsified:** per §2 the free-slot count is small, so even a perfectly diverse model would still produce a mostly-repeating *line*. Expect the **selector** branch to carry more of the explanation than model behaviour. Measure it; do not assume it.

---

## 5. Success metrics — joint, never single

Diversity alone is trivially gamed by shipping irrelevant tags. All five must hold **simultaneously**, on the same replay:

| Axis | Metric | Direction | Guard |
|---|---|---|---|
| Diversity | distinct **clip-derived** pick-sets per handle | ↑ | clip slots only — never whole-line (§2) |
| Concentration | share of a handle's surfaces on its modal **clip-derived** set | ↓ | per-handle, never pooled |
| Relevance | every shipped non-floor tag traceable to the clip's transcript/entities **or** the curated corpus | **100%** | no regression; 0 tags without provenance |
| Persona consistency | curated identity present on the line | **100%** | no regression — ADR-0104's guarantee |
| Safety | off-catalogue / malformed / generic-engagement tags shipped | **0** | no regression — R4's proof |

**No numeric diversity target is set here.** Setting one before §3's evidence exists would be inventing a threshold to hit — the same class of failure R4 removed. Derive the target from the first clean-menu measurement.

---

## 6. Required before/after replay

Same harness, same surfaces, same corpora — otherwise it is not a comparison. **Two baselines, because there are two axes:**

- **Selector baseline — exists now.** The R4 proof: clean corpora × *old* picks. Isolates selector behaviour with the model held fixed.
- **Model baseline — does not exist yet.** Clean corpora × *fresh* picks from §3. This is the real "before", and §3 is the only way to get it.
- **After.** Clean corpora × fresh picks × the change.

Corpus-wide (**all 347 surfaces**, not a sample) and reported **per handle** — the concentration is a per-handle property and a pooled number hides it. Publish §1's table plus every §5 guard, before and after, in one place.

---

## 7. Explicitly out of scope — closed, do not reopen

| Area | Closed by | Status |
|---|---|---|
| Corpus hygiene, curation, the circularity cut | ADR-0104, PR #681 | **Frozen.** The corpus is human-governed brand data. This program does not rewrite, pad, or auto-populate it. |
| Reach persistence / evidence accrual | PR #679 (H3) | **Closed.** Evidence accrues and is never destructively overwritten. Not a diversity lever. |
| Daemon code adoption | PRs #688, #689 | **Closed, and proven live** — one adopt per merge, then settle. |
| Store → corpus proposal | ADR-0104 | **Frozen.** Requires `source == "graph-reach"` and unexpired evidence. Not to be relaxed to buy variety. |

Two temptations, named here so they are refused on sight rather than re-argued:

- **Padding the corpus to a quota to manufacture variety.** This re-crowds the clip out of its own line — precisely the defect #679 fixed.
- **Relaxing the evidence rule so more tags can be proposed.** This restores the `corpus → store → corpus` circularity ADR-0104 severed by data model.
