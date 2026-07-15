# Root Cause Decision Record — reframe `centered_multi_untracked` (67 clips)

> Permanent engineering record. Investigation is **closed**. This document states *what is wrong*; the
> correctness rules are in [`framing-spec.md`](framing-spec.md); the engineering plan is in
> [`remediation-roadmap.md`](remediation-roadmap.md); the architectural decision is
> [ADR-0103](../../adr/0103-reframe-subject-and-layout-aware-framing.md).

**Provenance:** produced 2026-07-15 against the live corpus at `FANOPS_ROOT=/Users/molhamhomsi/FanOps`
(the operator's data root), source tip `main @ 0a3b503`. Accepted provisionally by the operator after a
three-round evidence-discipline review that rejected earlier drafts for asserting ownership beyond the
evidence.

**Evidence base:** all 67 clips of the affected set (5 source videos) → 27 distinct visual scenes, **every
scene visually audited**. Machine-readable evidence under [`evidence/`](evidence/): `defect-map.json`
(per-clip defect assignment), `framing-metrics.json` (full-box framing metrics), `raw-detections.json`
(per-frame YuNet output). Counterfactual renders (begin/mid/end × current-centre / dominant-lock /
fit-both, 27 sheets) were produced to scratch and are reproducible from the metrics + the documented
method; they are not committed.

**Evidence tiers used throughout:** **[FACT]** mechanically proven (code trace or measured metric) ·
**[OBS]** visually verified in rendered frames · **[INF]** supported but not proven · **[HYP]** requires
future validation. This separation is load-bearing — do not promote a lower tier when citing this record.

---

## Q1 — The actual defect, independent of the implementation

When the source is a wide (16:9) frame whose salient human subject(s) are **not near the horizontal
centre**, the produced 9:16 clip does not contain them well-framed: (a) two wide-set subjects → the frame
lands on the empty space *between* them, showing **no person**; (b) one off-centre subject → the subject
is **edge-cut / mic-occluded**; (c) a side-weighted subject → the frame is weighted onto **empty
background**. In one sentence: **the corpus contains off-centre subjects, and the produced framing uses a
fixed geometric region that ignores where the subject actually is.** This occurs in two structurally
different content situations (D1, D2); D1 has two subtypes that must never be merged.

---

## Defect D1 — A real two-person conversation is not framed to a participant (31 clips)

Three sources (`src_1797a00225b9`, `src_74de70400619`, `src_fd591f652ad4`) are one live two-host podcast,
hosts seated wide apart, each at a boom mic. Held as **two explicit subtypes**.

- **[FACT]** All 31: classified `multi-speaker-talk`; the active-speaker tracker *completed* → **no track
  (0 segments)**; the render therefore used a fixed centre crop.
- **[FACT]** The primary face is materially cut or edge-pinned in **85–100 % of frames** on every clip.

### D1-A — wide two-shot / empty-gap crop (6 clips, **severe**)
`clip_1ddaab762ea8, clip_3d86cdfeea35, clip_a3307598519c, clip_64960b1fd132, clip_ed310fe65a3f, clip_768c87426e4c`
- **[FACT]** Two small faces (max face-height ≤ 0.24) at opposite frame edges; the face span exceeds the
  vertical-crop width; the centre band contains a detected face in **0 % of frames**.
- **[OBS]** They render as an empty table — mic booms, a light-box, props — **no face** at begin/mid/end.
- **[OBS]** Both hosts are persistently co-present at their mics in the wide view.

### D1-B — dominant host edge-pinned (25 clips, **poor**)
- **[OBS]** The on-camera host is reduced to a sliver behind the pop-filter; the rest of the frame is dead
  table.
- **[OBS]** A real second host is present at his own mic in the wide view (fully, or as a reaching arm),
  appearing **intermittently** in these windows — not an artifact.

### D1 shared inferences / hypotheses
- **[INF]** These are genuine conversations where framing should follow the speaker — supported by both
  hosts at live mics; **not proven** (no audio analysis).
- **[INF]** The tracker's null is a capability shortfall on this footage (small / profile / downcast /
  mic-occluded faces), not a correct "nothing to track" — **not proven**.
- **[HYP]** The active speaker per cut is unknown. This bears differently on each subtype: for **D1-A**
  both are persistently present (a following crop has two live targets); for **D1-B** the second is
  intermittent (whether it ever holds the active speech is the open question). **Requires audio.**

---

## Defect D2 — Presenter-dominant PIP layout routed as multi-speaker (36 clips)

Two sources (`src_7b91a936ab3e`, `src_f1cd09169200`) are a video-call show: one **dominant presenter**
filling the left ~⅔ of the frame plus **three small remote participant tiles** stacked at the right edge.
(Deliberately not "single-presenter": whether the remote participants are immaterial is unproven pending
audio.)

- **[FACT]** All 36: detector reports ~4 faces/frame (presenter + 3 tiles) → `multi-speaker-talk`; tracker
  returned no track; render used the fixed centre crop.
- **[FACT]** The presenter's face is edge-pinned/cut in 85–100 % of frames; the presenter sits left of
  centre → the frame is weighted onto empty patterned wall.
- **[OBS]** The presenter is always at least partly visible but left-weighted with a large dead-background
  region; small in a wallpaper field on distant-camera clips. Never catastrophic, consistently
  un-composed. (All 14 PIP scenes audited.)
- **[OBS]** A "lock the largest face" alternative **mislocks onto a remote tile** whenever the presenter's
  face is small — the tile out-scores the distant presenter.
- **[INF]** A PIP grid is not a live multi-speaker two-shot; the active-speaker routing is an inappropriate
  *treatment mapping* (the face-count itself is correct).
- **[HYP]** Whether the remote tiles ever carry the active speaker (so that framing only the presenter
  would lose conversational content) is unknown. **Requires audio/content.**

---

## Shared terminal mechanism (both defects)

- **[FACT]** In both defects the final composition is a **content-blind fixed region** (centre 9:16, same
  x-range regardless of subject position); nothing in the terminal step uses the detected face positions.
  This is the common mechanism that turns D1's tracker-null and D2's mis-routing into a visibly bad frame.

---

## Q2 — Percentage of corpus materially affected

- **[FACT]** 67 clips, from 5 of 7 sources = **19.3 %** of the 347 dry-run-classified clips.
  D1-A 6 = **1.7 %** (severe) · D1-B 25 = **7.2 %** (poor) · D2 36 = **10.4 %** (moderate).
- **[HYP]** **Lower bound.** The ~280 non-`centered_multi_untracked` clips were not audited; the true
  corpus-wide degradation rate is ≥ 19.3 %.

---

## Q3 — User-visible degradation, per defect

- **D1-A (6) — severe:** empty table, mics, **no human** the whole clip. The clip has no subject.
- **D1-B (25) — poor:** the speaking host is jammed against the frame edge / half-hidden behind the
  pop-filter, rest dead table. Reads as broken/amateur framing.
- **D2 (36) — moderate:** presenter off-centre with a large dead patterned-wall region (or small in
  background). Watchable but visibly un-composed.

---

## Q4 — Which subsystem is failing to produce the desired outcome

- **Detection — not the primary demonstrated fault. [FACT/INF]** It provides enough presence and
  positional evidence to prove the downstream framing failure, but its reliability as a production framing
  signal across every frame remains unquantified.
- **D1-A — multiple interacting: primarily TRACKING, then FALLBACK COMPOSITION.** Two persistently-present
  speakers; the tracker produces nothing; the fallback then lands on the empty gap. Rendering is sound.
- **D1-B — multiple interacting, ownership UNRESOLVED pending audio.** If the off-frame second holds speech
  during these cuts → primarily TRACKING (must follow it) + fallback. If the on-camera dominant is
  effectively the sole speaker → primarily FALLBACK COMPOSITION (edge-cuts a mostly-single subject). The
  audio evidence decides which.
- **D2 — multiple interacting: primarily CLASSIFICATION (treatment routing), then FALLBACK COMPOSITION.**
  Detection and rendering are sound.
- **Common:** the **fallback-composition** subsystem is the shared failing link — a fixed region rather
  than one derived from the known subject positions.

---

## Q5 — Confidence, per claim

| Claim | Confidence | Why |
|---|---|---|
| D1-A exists (empty-gap, no subject) | **High** | 0 faces in the centre band in metric **and** pixels, all 6 |
| D1-B exists (dominant edge-pinned) | **High** | 85–100 % cut measured + visual on all D1-B scenes |
| D1-A subsystem = tracking (+ fallback) | **Medium** | No-track is [FACT]; that a better tracker *should* succeed here is [INF], unproven |
| D1-B subsystem ownership | **Low** | Genuinely split between tracking and fallback; unresolved until audio |
| D2 exists (PIP un-composed) | **High** | Visual across all 14 PIP scenes + metrics |
| D2 subsystem = classification routing (+ fallback) | **High** | Tiles counted as speakers; active-speaker treatment structurally wrong; largest-face alt mislocks |
| Detection not the primary demonstrated fault | **Medium–High** | Reports the present people in every scene; per-frame framing-signal reliability unquantified |
| Two distinct defects, D1 in two subtypes | **High** | Different content/subsystem/degradation; only the fallback is shared |
| Corpus rate 19.3 % | **Medium** | Exact for the audited set; lower bound |
| Second host / remote tiles are *material* | **Low–Medium** | Present at mics/on-call, but materiality = "do they speak," needs audio |

---

## Q6 — What remains unknown

1. **Active speaker over time**, per clip — no audio analysis done. Central for D1-B ownership and D1-A following.
2. **Remote-tile materiality** (D2) — do remote guests speak?
3. **Tracker feasibility** on this footage — no-track proven today; trackability neither proven nor disproven.
4. **Corpus-wide rate** — ~280 clips unaudited; ≥ 19.3 %.
5. **Detector per-frame recall / positional precision** on profile/cap/downturned/small faces — sufficient
   to prove *presence*, unquantified as a production framing signal.
6. **Generality** — only two layout families appear here; other off-centre layouts elsewhere in the corpus
   are unknown.

Unknowns #1 and #2 gate only the audio-dependent work (Track B in the roadmap); they do **not** gate the
visually-justified remediation (Track A). #5 and #6 are accepted residuals, recorded in the roadmap.
