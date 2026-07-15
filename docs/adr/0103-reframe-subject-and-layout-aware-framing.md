---
status: accepted
date: 2026-07-15
accepted: 2026-07-16
supersedes: []
references: [C3, "docs/design/reframe/RCDR-centered-multi-untracked.md", "docs/design/reframe/framing-spec.md", "docs/design/reframe/remediation-roadmap.md"]
deciders: [operator]
---

# ADR-0103 — Reframe framing is subject-aware and layout-aware

> **Accepted 2026-07-16** (proposed 2026-07-15, with the reframe remediation roadmap). Records the
> architectural principle only; no algorithm, threshold, fingerprint, or version change is decided here.
> Implementation (Track A) is authorized under this ADR + the roadmap; Track B stays deferred.

## Status

**Accepted 2026-07-16.** No prior catalogue slug — the reframe framing path had an *implicit, unrecorded*
design (content-blind centre fallback + face-count-only treatment routing) that the
`centered_multi_untracked` investigation showed to be wrong for real content. This ADR makes the corrected
principle explicit. The operator accepted it for implementation on the permanent evidence package (PR #660),
recording:

- **Track A is authorized from the existing visual evidence** — the composition + routing failures are
  provable from the detector output and the scene-by-scene visual audit alone, without speaker attribution.
- **Track B remains blocked on speaker attribution.** Active-speaker *selection* (who to show when
  participants alternate) waits for diarization and must not be guessed from visual signal.
- **Mild framing and minimal zoom are binding product requirements** (spec F6), not preferences.
- **Empty-gap outputs are prohibited** — a produced clip may not resolve to a region containing no
  participant when detected subjects exist outside it.
- **A content-blind centre crop is not an acceptable fallback when the detected subject positions prove it
  excludes or materially misframes people.** The fallback must be derived from those positions.

## Context

The subject-aware reframe (`framing.py` / `clip.py`, subsystem trace
[C3](../CODEMAPS/subsystem-traces/C3_clip_production_framing.md)) routes each window to a strategy and, when
no strategy applies, falls back to a fixed centre crop. A full-corpus audit of the 67 clips that reached
that fallback (`centered_multi_untracked`; evidence in
[`docs/design/reframe/RCDR-centered-multi-untracked.md`](../design/reframe/RCDR-centered-multi-untracked.md))
established, with mechanical metrics + a scene-by-scene visual audit:

- The terminal fallback is a **content-blind fixed region**: it ignores where the detected subjects are.
  For off-centre subjects this produces frames with **no subject at all** (6 clips), an **edge-pinned /
  mic-occluded** subject (25 clips), or a **dead-space-dominant** composition (36 clips) — 19.3 % of the
  classified corpus, a lower bound.
- Treatment routing maps "≥2 detected faces + speech" to live multi-speaker / active-speaker treatment. A
  **presenter-dominant video-call PIP layout** (one presenter + inert remote tiles) satisfies that
  face-count but is not a live two-shot; the routing is a category error.
- **Detection and rendering are sound**; the failing subsystems are **tracking** (produces no track on real
  two-person footage), **classification/treatment-routing** (PIP mis-mapped), and — shared by both — the
  **content-blind fallback composition**.

## Decision

1. **Subject-aware fallback (principle).** When no active-speaker track is available, the reframe's fallback
   composition **must be derived from the detected subject positions**, never a fixed content-blind region.
   A produced clip must contain its primary subject (no empty-gap frames) and must not edge-pin, occlude, or
   dead-space-dominate that subject. (Spec rules F1, F2, F3, F5.)

2. **Layout-aware treatment (principle).** Treatment routing **must distinguish a presenter-dominant PIP
   layout from a live multi-speaker two-shot**. Inert remote tiles are a UI layout, not co-speakers, and
   must not be routed into active-speaker switching. (Spec rule F4.)

3. **Zoom restraint (principle).** Framing prefers the widest crop satisfying the above; zoom-in is used
   only to remove dead space, never for emphasis, without explicit operator approval. (Spec rule F6; operator
   directive, project memory `prefer-minimal-zoom-reframe`.)

4. **Scope gate (principle).** **Active-speaker *selection*** — deciding which participant to show when
   participants alternate — is **deferred behind a speaker-attribution (diarization) dependency** and must
   not be guessed from visual signal alone. Subject-aware composition and layout-aware routing proceed on
   visual evidence; speaker following waits for attribution. (Roadmap Track A vs Track B.)

This ADR does **not** revive or specify any particular composition style (letterbox / stack / pad / track),
threshold, or crop geometry; those are implementation decisions under the roadmap.

## Alternatives considered

- **Keep the content-blind centre (status quo).** Rejected: proven to fail on 19.3 % of the corpus,
  including clips that show no subject at all.
- **Generic "lock the largest face" fallback.** Rejected on evidence: for presenter-dominant PIP with a
  distant presenter, the largest-scoring face is a remote tile, so the lock mislocks onto the tile. A
  subject-aware fallback must be layout- and context-aware, not merely "largest face."
- **Block all remediation until speaker attribution exists.** Rejected by operator direction: the
  composition and routing failures are provable from visual evidence alone; only speaker *selection* needs
  audio. Splitting the work (Track A / Track B) avoids holding the whole remediation hostage to diarization.

## Consequences

- The fallback-composition and treatment-routing subsystems gain a dependency on detected subject positions
  and layout shape (already produced by detection — no new detector capability is asserted).
- Corrected clips change pixels → their render fingerprints change → they re-render on a later daemon pass.
  This is a rollout-gated consequence, sequenced in the roadmap, not a standalone action.
- The existing `FANOPS_SMART_FRAMING=0` kill switch remains the global rollback (restores the blind centre
  byte-identical).
- **Accepted residual:** under Track A, D1-B frames the on-camera dominant and may show a non-speaking host
  when the off-frame second speaks; this is accepted until Track B (attribution) resolves speaker following.
