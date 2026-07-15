# Reframe Remediation Roadmap

> Engineering execution plan derived from [`RCDR-centered-multi-untracked.md`](RCDR-centered-multi-untracked.md)
> and [`framing-spec.md`](framing-spec.md). Defines slices, ownership, invariants, acceptance, tests,
> rollout, rollback, and blast radius. **No implementation here** — no crop math, thresholds, fingerprint
> or version changes. Implementation begins only after this roadmap is approved.

## Two tracks (do not hold Track A hostage to Track B)

- **Track A — visually justified now.** Every improvement warranted by the visual + detector evidence
  alone. Requires no speaker attribution.
- **Track B — audio-dependent.** Only work whose correctness genuinely needs to know the active speaker.

---

## Track A slices

Legend: **Sub** = owning subsystem · **Inv** = invariant (see spec) · **AC** = acceptance criteria ·
**Radius** = blast radius (clips whose output changes).

### S1 — Subject-aware fallback foundation *(enabler)*
- **Sub:** fallback-composition. **Inv:** F5.
- **AC:** for a no-track window with detected faces, the produced region is a function of those positions
  (it moves when they move); no fixed-centre output when the subject is off-centre.
- **Tests:** unit fixtures asserting the fallback region follows synthetic face positions.
- **Radius:** all no-track fallback renders (the 67 + any future no-track window).
- **Rollback:** revert to the fixed-centre fallback; global kill switch below.
- **Rollout:** **first** — enables S2/S3/S5.

### S2 — D1-A empty-gap elimination
- **Sub:** fallback-composition. **Inv:** F1 (+F5, F6).
- **AC:** AC-A1 (≥1 face every frame), AC-A2 (both persistent subjects retained, widest crop), AC-A3.
- **Tests:** the 6 D1-A source windows + a synthetic two-far-faces fixture — assert no zero-face frame; both retained.
- **Radius:** 6 D1-A clips + future wide two-persistent-far-faces.
- **Rollback:** revert S1. **Rollout:** after S1 (parallel with S3).

### S3 — D1-B subject integrity
- **Sub:** fallback-composition. **Inv:** F2 (+F5, F6).
- **AC:** AC-B1 (dominant not edge-pinned/occluded), AC-B2 (widest crop); **AC-B3 residual accepted** (off-frame speaker not shown — see AR-1).
- **Tests:** representative D1-B fixtures — principal face substantially in-frame, not edge-pinned.
- **Radius:** 25 D1-B clips + future single-dominant-off-centre.
- **Rollback:** revert S1. **Rollout:** after S1 (parallel with S2).

### S4 — D2 layout-aware routing
- **Sub:** classification / treatment-routing. **Inv:** F4.
- **AC:** AC-D1 — a presenter-dominant PIP layout does not enter the active-speaker/two-shot path. (Layout is a geometry signal: one large face + a column of small faces — no audio.)
- **Tests:** D2 fixtures — PIP layout is not routed as live multi-speaker.
- **Radius:** 36 D2 clips + future PIP.
- **Rollback:** revert the routing change. **Rollout:** independent subsystem — parallel to S1–S3.

### S5 — D2 composition (dead-space), tile-retention-agnostic
- **Sub:** fallback-composition. **Inv:** F3, F2.
- **AC:** AC-D2 (presenter salient, not dead-space-dominant), AC-D3 (widest crop). **AC-D4 (tile retention) is P1-gated** → this slice is scoped to the F3/F2 improvement under the interim "preserve tiles" posture; presenter-only tightening waits for P1 (Track B).
- **Tests:** D2 fixtures — presenter occupies the salient region; background not dominant.
- **Radius:** 36 D2 clips.
- **Rollback:** revert S1. **Rollout:** after S1 + S4.

### S6 — Regression fixtures *(cross-cutting)*
- **Sub:** tests. **Inv:** all F-rules.
- **AC:** fixtures for D1-A, D1-B, D2 that **fail on the current content-blind behaviour** and **pass under
  the corrected F-rules**. CI-only (never run locally, per project constraint).
- **Note:** follow `tests/test_smart_framing.py` conventions. Open fixture-design item for implementation:
  headless YuNet may not detect drawn synthetic faces (cf. the subtitle-fixture lesson that glyphs render
  nothing on the CI runner) — fixtures may need short real clips or a detector stub. Flag, do not pre-solve.
- **Rollout:** each fixture lands **with** its slice (S2→AC-A, S3→AC-B, S4/S5→AC-D).

---

## Track B slices (audio-dependent — do not start until B1 exists)

### B1 — Speaker-attribution evidence *(evidence, not code)*
- Produce per-clip **active-speaker timelines** for the 31 D1 clips and **tile-materiality** for the 36 D2
  clips; report confidence and list clips where attribution is unresolved.
- **Blocker:** attribution needs *who-speaks-when* (speaker diarization), not transcription. The toolchain
  has whisper only, and the podcast audio is likely a single mixed track. A method must be chosen first:
  a diarization model · per-mic stems **if** the raw multitrack survives · or manual annotation.
- **Gates:** P1, P2, B2, B3.

### B2 — D1 speaker following (resolves P2)
- **Sub:** tracking / active-speaker selection. **Depends on:** B1. Only meaningful once attribution exists.

### B3 — D2 tile-retention resolution (resolves P1)
- **Depends on:** B1 (tile materiality). Decides presenter-only vs presenter+tiles for D2.

---

## Rollout order

`S1` → `{S2, S3, S4}` in parallel → `S5` → regression fixtures land per slice → **Track B** (`B1` evidence →
`B2`, `B3`). S4 (routing) is subsystem-independent and may lead.

## Rollback strategy

- **Global backbone:** `FANOPS_SMART_FRAMING=0` restores the blind centre crop **byte-identical** (existing
  kill switch) — the whole track reverts with one flag, no new mechanism needed.
- **Per-slice:** revert that slice's composition/routing change; S2/S3/S5 additionally revert via S1.

## Blast radius & the rerender consequence

Per-slice radius is listed above (the 6 / 25 / 36 target sets + their future-analogues). Each Track-A slice
changes the output of its target clips; their render fingerprint changes **as a consequence**, so the
daemon would re-render them on a later pass. **Re-render is a rollout-gated consequence — not proposed
here and not to be performed until the roadmap is approved and each slice is independently verified.**

## Accepted residuals (carried forward, not fixed)

- **AR-1 — D1-B off-frame speaker (AC-B3).** Track A frames the on-camera dominant; if the intermittent
  second host is the one speaking off-frame, the output shows a non-speaking host. Accepted as an interim:
  strictly better than the current edge-pinned frame and no worse than the blind centre on speaker
  attribution. Resolved by B2.
- **AR-2 — Corpus rate is a lower bound.** ~280 clips unaudited; the true degraded fraction is ≥ 19.3 %.
  Accepted unknown, not a blocker; a broader audit is optional future work.
- **AR-3 — Detector positional precision** on profile/small/downcast faces is unquantified. The F-rules are
  qualitative and tolerate detector noise; revisit only if fixtures reveal instability.
- **AR-4 — D1-A ideal is deferred.** Track A delivers a subject-aware fallback that **retains both**
  subjects; it is **not** active-speaker following. The ideal (a working tracker that cuts to the speaker)
  remains open (B2). Track A is an accepted compositional floor, not the ideal.

## What must be true before implementation begins

This roadmap approved · [ADR-0103](../../adr/0103-reframe-subject-and-layout-aware-framing.md) accepted ·
[`framing-spec.md`](framing-spec.md) rules confirmed. Track A may then proceed on visual evidence; Track B
waits on B1.
