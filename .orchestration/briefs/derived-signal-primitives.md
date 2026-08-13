# Brief — Derived-signal primitives (remediation #3)

**Root issue #3 in remediation order** (after config truth → fail-open escalation).  
**Status:** **partially landed** on `publish/derived-signal-primitives`. Close residuals via [derived-signal-close-1-2.md](derived-signal-close-1-2.md) before treating #3 as Done.  
**Principle:** derived operator signals use **direct state or explicit unknown** — never rewritten-mtime age, never TTL-less snapshot calm, never fail-open-to-LIVE.

**Governing rules:** [AGENTS.md](../../AGENTS.md). Cite symbols (STD-DOC-01).

---

## Three lenses

| Lens | Answer |
|------|--------|
| Best practice | Logical clock for gate age; typed Fresh\|Stale\|Missing\|Unreadable snapshot reads; label failure → `"unknown"` |
| Root cause | Three wrong primitives (mtime age, forever-fresh files, LIVE fail-open) made honesty impossible downstream |
| Leanest | Stamp + typed reader + label change beside existing seams — no health-contract schema, no new package, no new `FANOPS_*` |

---

## What this PR lands

| Seam | Symbols | DoD |
|------|---------|-----|
| **Gate age** | `write_request` `opened_at`; `_gate_opened_epoch` / `_pending_gates`; doctor `_GATE_STALE_TICKS` | Age survives rewrites; not `st_mtime` |
| **Snapshot freshness** | `SnapshotFreshness` / `SnapshotRead` / `_read_snapshot`; strip / Postiz / daemon consumers | Non-FRESH ≠ calm zero / silent hide (primary paths) |
| **LIVE → unknown** | `effective_publish_mode`; `_half_live_state`; doctor half-live | Accounts boom → `"unknown"`; compute fail → not solid LIVE |

TTL constant: `health._SNAPSHOT_TTL_S = 1800` (beside reader).

---

## Explicitly not closed here (follow-on)

| Item | Brief |
|------|-------|
| Seam 1/2 residuals (remint, falsy epoch, spine calm-zero, daemon “off” lie, …) | [derived-signal-close-1-2.md](derived-signal-close-1-2.md) |
| Config truth (#1) | [config-truth-plane.md](config-truth-plane.md) |
| Fail-open escalation (#2) | [fail-open-escalation-policy.md](fail-open-escalation-policy.md) |
| Machine-health (#4) | [machine-health-contract.md](machine-health-contract.md) |
| Control vs data plane (#5) | [control-data-plane-untangle.md](control-data-plane-untangle.md) |
| Honesty ratchets (#6) | [honesty-ratchet-contract.md](honesty-ratchet-contract.md) |

---

## Non-goals (still refuse)

Health-contract types/docs; mass fail_open audits; hashtag Layer A redesign; editing `lanes.json`.
