# Reframe E1/E2 — Implementation Contract

Branch `fix/reframe-e1-geom-e2-recall` off `main@a9f48f1` (post-#640). This contract is the
precondition to writing E1/E2 code. **Non-goals (hard):** no live qualification, no clip migration,
no bulk reframing in this PR. E1/E2 change detection contracts, cache versions, and fingerprints;
qualification (a fresh dry-run + stratified visual review + a new visual-gated pilot) happens
separately, AFTER this PR is green.

## 1. Detection tuple / schema change (carry face WIDTH)

The safe-area clamp needs horizontal extent, which no tuple carries today.

- `framing._detect_faces` ([framing.py:131]) emits `(cx, cy, fh, ey, score)` from YuNet row
  `f = [x, y, w, h, …, score]`. Width `f[2]/w` is already computed (it feeds `cx`) then **discarded**.
  **Change:** append `fw = min(1.0, max(0.0, f[2]/w))` → `(cx, cy, fh, ey, score, fw)` — a 6-tuple.
  **Index-preserving:** `score` stays at `[4]`, so `_pick_dominant_face` (`f[4]`, `f[2]`) and
  `_face_count` (`f[4]`, `f[2]`) are untouched; new readers take `f[5]`.
- `framing._track_observe` ([framing.py:345]) emits per-side `(fx, fy, fh, ey)` — append `fw` the same way.
- Downstream tuple layers gaining `fw` (append at the end, index-preserving):
  - **Focus** (`subject_focus` → clip): `(fx, fy, fh, ey)` → `(fx, fy, fh, ey, fw)` (`fw` at `[4]`).
  - **Track segment** (`speaker_track` → clip): `(t0, t1, fx, fy, fh, ey)` → `(…, fw)` (`fw` at `[6]`).
- Reader updates in `clip.py`: `_focus_crop` reads `focus[4]`; `_already_aspect` reads `focus[4]`;
  `_crop_box`/`_segment_chain` read `seg[6]`; `_track_crop` reduces the per-segment `fw` (median, like `fh`).

## 2. Version bumps

| Constant | File | Change | Effect |
|---|---|---|---|
| `_DETECT_V` | framing.py:128 | 2 → 3 | invalidates `.detect.json` grid caches → re-detect corpus-wide |
| `_SIDECAR_V` | framing.py:21 | 5 → 6 | invalidates `.track.json` + `subject_focus` sidecars (tuples gain `fw`) |
| `_REFRAME_GEOM_V` | clip.py:646 | 4 → 5 | changes `fp_new` for zoom/track clips → re-render; **centered clips (no geom) byte-identical** |

The `_REFRAME_GEOM_V` payload gate (`geom = bool(track) or (focus and len(focus)>2)`) bounds the
re-render blast radius to zoom/track renders only.

## 3. The four geometry paths that MUST share the safety rule

All four call `_place` ([clip.py:234]), which today clamps the crop origin **only to source bounds** —
nothing keeps the face box inside the crop with a margin, and headroom is a fixed `_EYELINE_FRAC` fraction.
The safe-area rule must reach every one:

1. `_focus_crop` ([clip.py:272], `_place` at :282) — static single-subject lock.
2. `_track_crop` ([clip.py:255], `_place` at :266) — multi-segment single-pass pan.
3. `_already_aspect` ([clip.py:326], `_place` at :336) — already-9:16 gentle zoom.
4. `_crop_box` ([clip.py:361], `_place` at :371) — the per-segment **concat** path (`_segment_chain`).

**Implementation:** centralize the rule where `_place` computes the origin (it, or a helper it calls,
takes `fw`), so all four inherit one implementation — (a) keep the full face box
`[fx−fw/2, fx+fw/2] × [head_top, chin]` inside the crop with a minimum edge margin; (b) protect
head-top (`crop_top ≤ head_top − headroom`); (c) when the safe box can't fit at the chosen zoom,
**reduce the zoom (widen the crop)** rather than cut the face — never widen beyond source.

## 4. Classification change for multi-person recall (E2)

- **Root cause:** `_face_count` ([framing.py:281]) returns the **median** per-frame real-face count;
  a turned/distant 2nd host's `score×fh` falls below `_PHANTOM_QUALITY_RATIO(0.3)×dom_q` in ≥half the
  frames → median 1 → `CT_SINGLE`. It also oscillates on clip-boundary shifts (5 multi / 16 single from
  one source).
- **Change:** classify a two-shot by **L/R position clustering**, reusing `_ASD_SIDE_SPLIT(0.5)` (already
  used by `_track_observe`): if two distinct L/R face clusters are present across the sampled frames (each
  in ≥K frames) with trusted speech, classify `CT_MULTI` — even when each is dominant only intermittently.
  Relax the phantom gate for a **second cluster** (a face on the opposite side is structurally a second
  speaker, not wall-art). Keep phantom-rejection for the single-side case (a decoy beside one speaker → SINGLE).
- **Stability gate:** deterministic for a fixed window AND consistent across adjacent clips of one
  conversation — the Phase-0 stability harness is the regression test (a fixed two-person podcast must not
  oscillate multi↔single).

## 5. What becomes stale (regenerate; do NOT reuse defective outputs)

- `.detect.json` grids (`_DETECT_V`), `.track.json` + `subject_focus` sidecars (`_SIDECAR_V`).
- `.render.json` fingerprints for **zoom/track** clips (`_REFRAME_GEOM_V`) → re-render; centered clips unchanged.
- The full-corpus dry-run manifest, per-clip classification outcomes, framing traces, candidate migration
  plans, the pilot manifest — **all stale**; regenerate from scratch. Do not reuse any plan produced by the
  defective classifier/geometry.
- Arch-governance derived artifacts (`side_effects/dependencies/MANIFEST/kb` + `ARCHITECTURE_GOVERNANCE.md`)
  — regenerate for the line shifts + new deps (`tools.arch regen` + `docs`; the CI gate enforces it).

## 6. Failing-before / passing-after invariants (mechanical, unit-testable on crop coordinates)

- **SAFE-AREA:** the emitted crop contains the full detected face box with ≥ margin M on every edge.
  Fails-before on `clip_67d8` (near cheek at the frame edge); passes-after.
- **HEADROOM:** `crop_top ≤ head_top − headroom` — a tall cap is not clipped. Fails-before on `clip_67d8`.
- **ZOOM-BACKOFF:** when the safe box can't fit at target zoom, the emitted crop's zoom is reduced
  (larger `cw`/`ch`), never a face-cutting crop.
- **RECALL:** a two-cluster L/R window with speech classifies `CT_MULTI`. Fails-before (median→SINGLE).
- **NO-REGRESSION:** centered renders (`focus=None, track=None`) stay **byte-identical** (fingerprint unchanged).
- **Acceptance caveat:** the YuNet face-box metric under-reads on caps/profiles/downturned heads, so the
  mechanical invariant is necessary but NOT sufficient — the final sign-off is the visual pilot (§7).

## 7. Visual regression corpus + qualification procedure (runs AFTER this PR, not in it)

- **Corpus** (versioned fixtures, prefer short real footage over synthetic boxes): fixed two-person podcast;
  distant 2nd speaker; profile; one leaning out of frame; speaker handoff; stable single; near-left-edge;
  near-right-edge; high headwear (cap); subtitles near face; genuine no-person; wide contextual; mixed.
  Each pins: source + expected classification + expected strategy + safe-area invariants + forbidden output
  + whether geom/fp is pinned.
- **Qualification sequence** (gated, after E1/E2 land + green on both CI lanes): re-run full-corpus
  `--dry-run` → visually review a **stratified** sample (multi-person, single, profile, edge, headwear —
  sampled **across a single source's clips**, not the lexicographic head) → a NEW one-source visual pilot
  gated on frame review → only THEN bulk. **None of this executes in the E1/E2 implementation PR.**

## 8. Conservative fallback when classification confidence is insufficient (E3, decided)

- **Under uncertainty** — unstable/low-confidence classification, or `CT_MULTI` with no clean track — fall
  back to the **original blind CENTRE-CROP** (`focus=None, track=None`), NEVER a static one-person crop.
  The centre-crop is the defined acceptance floor ("no worse than the original centre crop"), so it can
  never regress.
- A **confidently-detected two-person podcast** → active-speaker tracking (the E4-fixed concat path), gated
  on tracking quality proven in the re-pilot; degrade to **preserve-the-two-shot** (fit/pad both), then
  centre-crop, if tracking quality isn't demonstrably good.

## Phase-1 record clarification (#640)

PR #640 (`a9f48f1`) is **fingerprint-neutral for existing stored fingerprints** — no `.render.json` `fp`
changes, so nothing re-renders — **but it intentionally changes the rendered BYTES of future multi-segment
tracked outputs** by enforcing `-fps_mode cfr` (CFR fills the concat per-join PTS gaps). That is a deliberate
output-correctness change on new renders, distinct from a fingerprint change.

## Sequencing recommendation

E1 (geometry + width schema) and E2 (recall) co-invalidate the same caches and both move fingerprints, so
landing them **together in this one PR** (ordered commits: E1 detection-schema → E1 geometry → E2 recall →
E3 fallback → invariant tests) means **one** qualification run afterward instead of two. The qualification
run itself is a separate, gated step after this PR is green.
