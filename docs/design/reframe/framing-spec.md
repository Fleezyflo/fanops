# Reframe Framing Specification

> Permanent engineering specification for the vertical (9:16) reframe of wide (16:9) sources. Derived from
> the evidence in [`RCDR-centered-multi-untracked.md`](RCDR-centered-multi-untracked.md). This is a
> **specification of correct output**, not an algorithm: it states the invariants a reframe must satisfy
> and the acceptance criteria per defect class. Implementation choices (crop math, composition style,
> detection tuning) are out of scope and belong to the roadmap/implementation phase.

## Status

Rules **F1–F6** are objective correctness rules established by the investigation; they are recorded as
binding, not offered as questions. Product decisions **P1–P2** genuinely cannot be inferred from the
evidence and are escalated (audio-gated). Acceptance criteria are stated qualitatively — no pixel
thresholds are fixed here; the implementation phase derives measurable checks from these.

---

## Objective correctness rules (binding)

**F1 — Subject presence.** Every produced clip must contain the primary human subject for its full
duration. A frame that contains **no detected face while faces are present in the source window** is
prohibited. *(Establishes: D1-A empty-gap output is categorically wrong. Objective — a clip that shows no
one is not framing.)*

**F2 — Subject integrity.** The primary on-camera subject's face must be **substantially within the frame
and not pinned to a frame edge or occluded by foreground** (e.g. the microphone / pop-filter). *(D1-B, D2.
Objective — competent framing of a present subject.)*

**F3 — No dead-space-dominant composition.** The frame must not be weighted onto empty background while the
subject sits to one side; the subject occupies the compositionally salient region of the frame. *(D2.
Objective.)*

**F4 — Layout-aware treatment.** A **presenter-dominant PIP layout** (one large presenter plus a column of
small, inert remote tiles) must **not** be routed into live multi-speaker / active-speaker-switching
treatment. Inert remote tiles are not co-speakers to cut between. *(D2. Objective — the tiles are a UI
layout, not a live two-shot.)*

**F5 — Subject-derived fallback.** When no active-speaker track is available, the fallback composition must
be **derived from the detected subject positions**, never a fixed content-blind region. *(Systemic root.
Objective — the content-blind centre is the proven common cause of all three failure shapes.)*

**F6 — Zoom restraint (operator directive, 2026-07-15).** Framing prefers the **widest** crop that
satisfies F1–F3; zoom-in is applied only to **remove dead space**, never for emphasis, without explicit
operator approval. When uncertain, **show more, not less.** *(Binding operator input; see project memory
`prefer-minimal-zoom-reframe`.)*

---

## Escalated product decisions (cannot be inferred — audio-gated)

These are genuine editorial choices, not correctness rules. They must not be resolved by guessing; they
depend on the speaker-attribution evidence (roadmap Track B). Until resolved, the reversible interim
posture is the one most consistent with F6 ("show more").

**P1 — D2 remote-tile retention.** When the presenter is framed (F2/F3/F4), are the remote tiles
**preserved** (presenter + tile column) or **dropped** (presenter-only)? Depends on whether remote
participants materially speak. *Interim posture: preserve, pending materiality evidence.* — **OPEN.**

**P2 — D1 speaker following.** When the active speaker is **reliably known**, does the clip cut/follow to
whoever is speaking, or hold a single stable framing that satisfies F1/F2? Genuine style choice; only
answerable once attribution exists. — **OPEN.**

---

## Acceptance criteria, per defect class (kept separate — do not merge D1-A / D1-B / D2)

Each criterion is the observable property a corrected output must exhibit; each is verified against
rendered pixels + detector evidence, not against fingerprint equality.

### D1-A — wide two-shot / empty-gap (6 clips)
- **AC-A1 (F1):** every sampled frame of the output contains at least one detected subject face. Zero-face
  frames are a hard failure.
- **AC-A2 (F5, F6):** absent speaker attribution, **both** persistently-present subjects are retained in
  the composition (neither is arbitrarily dropped). The composition is the widest that keeps both present.
- **AC-A3 (regression):** the six D1-A source windows, re-evaluated, no longer produce a content-blind
  centre region.
- *Deferred to Track B:* whether, once attribution exists, the clip should instead follow the active
  speaker (P2). Not required for D1-A acceptance.

### D1-B — dominant host edge-pinned (25 clips)
- **AC-B1 (F2):** the on-camera dominant subject's face is substantially in frame and not edge-pinned or
  mic-occluded in the output.
- **AC-B2 (F5, F6):** the composition is derived from the dominant subject's position, at the widest crop
  that satisfies AC-B1 (no punch-in for emphasis).
- **AC-B3 (accepted residual, explicit):** framing the on-camera dominant does **not** guarantee the
  active speaker is shown when the intermittent second host speaks off-frame. This residual is **accepted
  for Track A** because it is strictly better than the current edge-pinned output and no worse than the
  blind centre on the speaker-attribution axis (neither shows the off-frame speaker). Its resolution is
  Track B (P2).

### D2 — presenter-dominant PIP (36 clips)
- **AC-D1 (F4):** a presenter-dominant PIP layout is not routed into active-speaker/two-shot treatment.
- **AC-D2 (F3, F2):** the presenter occupies the salient region; the output is not weighted onto empty
  background; the presenter's face is not edge-pinned.
- **AC-D3 (F6):** achieved at the widest crop that satisfies AC-D2 (no punch-in).
- **AC-D4 (P1 dependency, explicit):** whether the corrected composition **preserves or drops the remote
  tiles** is **not** fixed by this spec — it is the open product decision P1. A D2 correction that satisfies
  AC-D1/AC-D2/AC-D3 while **preserving** the tiles is acceptable under the interim posture; a presenter-only
  composition requires P1 to be resolved first.

---

## Non-goals / out of scope
- No crop geometry, thresholds, composition style (letterbox vs stack vs pad), detection tuning, fingerprint
  or version changes are specified here. Those are implementation decisions gated on the roadmap.
- The ~280 non-affected clips are out of scope for this spec (see RCDR Q6 #4).
