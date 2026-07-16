# Brief — Hashtag model diversity

**Unit tag:** `Unit: hashtag-model-diversity`
**Status:** brief only — no implementation. §4's gate must be satisfied before any code changes.
**Boundary:** R4 is closed and frozen (ADR-0104, [`r4-migration-record.md`](../../CODEMAPS/r4-migration-record.md)). §9 lists what this program may not touch.

---

## 1. What is actually measured

All figures below are from the live ledger (`00_control/ledger.sqlite`, 347 clips × 1 surface each), replaying
`meta_captions.hashtags_raw` — **the model's verbatim picks, recorded independently of the selector**.

| handle | n | model raw: sets / modal% | old selector shipped: sets / modal% | new selector (replay): sets / modal% |
|---|---|---|---|---|
| markmakmouly | 76 | 15 / 68.4% | 4 / 92.1% | 5 / 78.9% |
| backlikeineverleft | 71 | 9 / 76.1% | 5 / 93.0% | 5 / 80.3% |
| perca.late | 67 | 14 / 53.7% | 6 / 92.5% | 10 / 61.2% |
| cisumwolfhom | 67 | 7 / 67.2% | 7 / 91.0% | 2 / 98.5% |
| hrmny-blog | 66 | 6 / 75.8% | 6 / 90.9% | 4 / 80.3% |

**Structural floor: 3.5–4.8% modal, ~140 distinct sets** — the selector run on *maximally diverse* synthetic
picks from the live 18-tag menu. This is the concentration the design itself imposes. It is small. The
curated lead fixes 2 of 4 slots, but the remaining 2 vary freely, so **the design is not a meaningful source
of repetition.** Any claim that "some of this is by design" is bounded by ~4pp and must not be used to excuse
more.

`recent` does not move any of these numbers: replaying with an empty recency list and with every prior tag
for the handle gives **byte-identical** results, because recency is the third sort key and tier/`picked`
already break every tie. The graded-LRU term from #679 is inert on this data.

---

## 2. The confound — why none of §1's right-hand columns predict production

**The model's picks were conditioned on the prompt, and the prompt showed it the old, polluted corpus.**
`caption_prompt` presents the corpus as the model's menu. So `hashtags_raw` is not an independent variable —
it is a *response* to a corpus that no longer exists.

The evidence is direct. `cisumwolfhom` (persona `burner-bold`) picked, 45 times out of 67:

```
#explorepage  #hiphop  #trending  #viral
```

Its **pre-migration** corpus was `#viral #rapmusic #hiphop #trending #post #fypppp…(73 p's) #explore #love
#explorepage #instagood #art #highlights`. The model was picking the junk it was shown. R4 removed that junk,
so those picks no longer survive the vetted membership gate; `o_kept` empties, the 3-tag corpus backfills the
line, and the replay pins at **98.5% / 2 sets**.

**That 98.5% is an artifact of composing old picks × new menu — a world that will never exist.** The same
confound applies to the four handles that appear to *improve*. Replay holds the selector honest but cannot
fix a wrong input distribution.

Two consequences, both binding on this program:

- **A free "selector-only" replay is not available.** The selector is a pure function, but its behaviour
  depends on the pick distribution, so isolating it requires picks drawn from the *current* prompt.
- **The one free, valid measurement is the structural floor** (§1), because it uses synthetic inputs and
  therefore has no prompt conditioning to confound.

---

## 3. The live hypothesis this program must test first

Prior work assumed clean corpora would *reduce* repetition. **The measured data suggests the opposite is
plausible and it must be tested, not assumed.**

`burner-bold`'s corpus went from **12 tags to 3**. The prompt instructs the model to *prefer* the corpus.
A smaller menu gives the model less to vary over, so R4's curation may **increase** model repetition even as
it improves relevance. Relevance and diversity may be in direct tension, and R4 bought relevance.

This is the first thing §4's evidence must answer, because it determines whether the program is "make the
model vary more" or "give the model more admissible material to vary over" — different work entirely.

---

## 4. Evidence gate — staged, cheapest first

Nothing may be diagnosed or changed until picks exist that were generated against the **clean** menu.

| Stage | What | Cost | Gate to proceed |
|---|---|---|---|
| **0** | Structural floor (§1) — synthetic picks, no LLM | **free**, already run: 3.5–4.8% | done |
| **1** | **30 surfaces per posting handle** (~150 captions) regenerated against clean corpora | ~150 LLM calls | measure §5 tree + §6 criteria |
| **2** | Expand to full corpus **only if** stage 1's interval straddles a decision boundary | ~347 total | — |

**Power.** The statistic is a proportion (modal share), so `SE = sqrt(p(1-p)/n)`. At p ≈ 0.7:

- **n = 30/handle** → SE ≈ 8.4pp → 95% CI ≈ **±16pp**. Detects a ≥20pp shift — the size of effect worth acting on.
- **n = 70/handle** → SE ≈ 5.5pp → 95% CI ≈ **±11pp**. Buys ~5pp of precision for 2.3× the spend.

So stage 1 resolves any large effect, and stage 2 is justified only near a boundary. **Do not open with 347
generations.**

**Methodological trap — do not compare distinct-set counts across different n.** Distinct-set count grows
with sample size, so §1's "15 sets over 76 surfaces" is *not* comparable to "8 sets over 30 surfaces". Modal
share is n-robust in expectation; use it as the primary statistic, and rarefy to a common n before ever
quoting a count.

Waiting for the daemon to regenerate naturally is free but delivers an uncontrolled, slowly-arriving sample;
prefer it only if no one needs the answer this week.

---

## 5. Differential diagnosis — one decision tree

Run in order; stop at the first hit.

1. **Are two clips' prompts materially different?** Hash `caption_prompt` output per surface.
   - **Identical across clips** → the model cannot be blamed. Defect is **prompt construction**: clip signal
     is not reaching the prompt. Fix upstream. Stop.
2. **Prompts differ. Does `hashtags_raw` vary?**
   - **Raw varies, shipped does not** → **selector**-driven. Measure membership-survival (§7) before anything else.
   - **Raw does not vary** → model or sampling. Go to 3.
3. **Does raw vary when temperature / top-p are raised on identical prompts?**
   - **Yes** → **sampling**-driven: responder configuration.
   - **No** → **model behaviour** (mode collapse toward genre-typical tags). The expensive branch.

**Prior, held loosely:** given the limited number of free slots and the membership gate, selector effects are
expected to contribute materially — but this remains a hypothesis to be tested, and §1 shows the structural
floor is only ~4pp, so the selector has less room to explain than it first appears.

---

## 6. Root-cause acceptance criteria

A mechanism is accepted as **the dominant cause** only if it clears all of the following. Without this, every
experiment explains *some* variance and the program never terminates.

**Denominator.** Concentration is not measured against zero — it is measured against the structural floor:

```
excess = observed_modal_share − structural_floor        (floor ≈ 4%, §1, re-derive per handle)
```

**Acceptance:** neutralising the mechanism must collapse **≥80% of `excess`**, per handle, on ≥4 of 5 handles,
measured on the same replay — **while regressing none of:**

| Guard | Bound |
|---|---|
| persona consistency (curated identity present) | 100%, no regression |
| transcript/corpus relevance (every non-floor tag traceable) | 100%, no regression |
| malformed rate | 0, no regression |
| off-catalogue rate | 0, no regression |
| determinism where required (`_render_fingerprint`-style purity) | unchanged |

A mechanism explaining <80% of excess is a **contributor**, recorded and not acted on further. If two
mechanisms each clear 80%, they interact — report the interaction rather than picking a winner.

---

## 7. Intervention ladder — with stop conditions

Cheapest first. **Stop at the first rung that clears §6.** Do not proceed to the next rung to chase a better
number.

| # | Rung | Stop condition |
|---|---|---|
| 1 | **Membership headroom** — measure what fraction of model picks survive the vetted gate. If <2 survive per surface, the corpus backfills and the line pins regardless of the model (§2). Widening admissible material is data work, not code. | diversity clears §6 with relevance intact → **stop** |
| 2 | **Prompt** — does the prompt carry clip-distinguishing signal at all (§5.1)? | clears §6 → **stop** |
| 3 | **Sampling** — responder temperature / top-p. | clears §6 → **stop** |
| 4 | **Selector** — slot allocation, `_CORPUS_LEAD_MAX`, cap. **Changing these touches ADR-0104's guarantee; requires an ADR amendment, not a patch.** | clears §6 → **stop** |
| 5 | **Model prompt redesign** — e.g. require the model to justify each pick from the transcript. Expensive; last. | — |

Rung 1 before rung 5 is the whole point: §2 shows the currently-observed collapse is a *membership* effect,
not a model effect.

---

## 8. Measurement apparatus — specified now, implementation-neutral

**Source.** `00_control/ledger.sqlite` → `ledger_rows(map_name, row_id, payload)`; `map_name='clips'`;
`payload` is clip JSON. Counts today: `clips` 347, `posts` 347, `moments` 347, `sources` 7, `batches` 5.

**Per-surface record.** `payload.meta_captions["<handle>/<platform>"]`:

```json
{"caption": "...", "hashtags": [...], "hashtags_raw": [...],
 "hook": null, "axis": null, "rationale": null,
 "tag_sources": {"#tag": "content|corpus|region|graph-reach|discovery|genre-floor"}}
```

**Field semantics — the one thing to get right.** `tag_sources` records **where a tag was found**, not **who
chose it** (`hashtags.py:405`, priority `content > corpus > region > graph-reach > discovery > genre-floor`).
A model pick that is also a corpus member labels `corpus`. **Authorship lives in `hashtags_raw` alone.**
Therefore:

```
model-honoured slots   = hashtags ∩ hashtags_raw
selector-inserted slots = hashtags − hashtags_raw
```

Live baseline: `tag_sources` vocabulary is `{corpus: 1378, region: 10}` — exactly 4.0 tags/surface, and
`content` **never appears**, because the caption path deliberately does not pass `content=`
(`caption.py:328`; regression-locked by `tests/test_content_aware_hashtags.py` — "corpus-only"). Do not read
`content: 0` as a defect and do not wire it; that is a settled decision.

**Extraction query.**

```sql
SELECT payload FROM ledger_rows WHERE map_name = 'clips';
-- then, per surface: json_extract(payload, '$.meta_captions')
```

**Statistical outputs**, per handle: `n`; modal share of `frozenset(hashtags_raw)` ± Wilson 95% CI; modal
share of `frozenset(hashtags)` ± CI; distinct-set counts **rarefied to a common n**; mean |raw ∩ shipped|;
membership-survival rate; `excess` vs the per-handle structural floor.

**Report format.** §1's table, before and after, plus every §6 guard, in one document. Per handle, never
pooled — concentration is a per-handle property and pooling hides it.

---

## 9. Out of scope — closed, do not reopen

| Area | Closed by | Status |
|---|---|---|
| Corpus hygiene, curation, the circularity cut | ADR-0104, PR #681 | **Frozen.** Human-governed brand data. Not rewritten, padded, or auto-populated. |
| Reach persistence / evidence accrual | PR #679 (H3) | **Closed.** Not a diversity lever. |
| Daemon code adoption | PRs #688, #689 | **Closed, proven live** — one adopt per merge, then settle. |
| Store → corpus proposal | ADR-0104 | **Frozen.** Requires `source == "graph-reach"` + unexpired. Not relaxed to buy variety. |
| `content=` wiring | `tests/test_content_aware_hashtags.py` | **Settled: corpus-only.** Not a gap. |

Temptations refused on sight:

- **Padding the corpus to a quota to manufacture variety** — re-crowds the clip out of its own line, the
  defect #679 fixed. Note §3: if evidence shows the 3-tag corpus is the binding constraint, the answer is an
  **ADR-0104 amendment with the operator curating more real tags**, never an auto-pad.
- **Relaxing the evidence rule so more tags can be proposed** — restores the circularity ADR-0104 severed.
