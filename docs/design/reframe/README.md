# Reframe framing — root-cause & remediation (design set)

Permanent engineering record for the `centered_multi_untracked` reframe defect class. **Investigation is
closed; implementation is gated on approval of the roadmap + ADR-0103.** Future work on the reframe framing
path should start here rather than re-discovering the evidence.

| Document | What it is |
|---|---|
| [`RCDR-centered-multi-untracked.md`](RCDR-centered-multi-untracked.md) | Root Cause Decision Record — *what is wrong*, facts/observations/inferences/hypotheses separated. |
| [`framing-spec.md`](framing-spec.md) | Framing Specification — objective correctness rules (F1–F6) + escalated product decisions (P1–P2) + per-defect acceptance criteria. |
| [`remediation-roadmap.md`](remediation-roadmap.md) | Engineering plan — slices, subsystems, invariants, tests, rollout, rollback, blast radius; Track A (visual) / Track B (audio). |
| [ADR-0103](../../adr/0103-reframe-subject-and-layout-aware-framing.md) | Architectural decision — subject-aware + layout-aware framing. |
| [`evidence/`](evidence/) | `defect-map.json` (per-clip D1-A/D1-B/D2), `framing-metrics.json` (full-box metrics), `raw-detections.json`. |

## The defect in one line
A wide (16:9) source whose subject is off-centre is reframed to a **content-blind fixed centre region** that
ignores where the subject actually is.

## Affected set (67 clips, 19.3 % of the classified corpus — a lower bound)
| Class | n | Content | Current output | Primary failing subsystem |
|---|---|---|---|---|
| **D1-A** | 6 | live two-host podcast, hosts wide apart | empty table, **no face** (severe) | tracking (+ fallback) |
| **D1-B** | 25 | same podcast, one host dominant on-camera | host edge-pinned behind the mic (poor) | unresolved: tracking vs fallback (needs audio) |
| **D2** | 36 | presenter-dominant video-call PIP grid | presenter off-centre, dead background (moderate) | classification routing (+ fallback) |

Detection and rendering are **not** the primary demonstrated fault.

## Related
- Subsystem trace: [C3 — Clip Production & Framing](../../CODEMAPS/subsystem-traces/C3_clip_production_framing.md).
- Project memory: `reframe-cmu-investigation-gate`, `prefer-minimal-zoom-reframe`, `framing-trust-gate-pins-outcome-not-tuple`, `dynamic-reframer-built`.
