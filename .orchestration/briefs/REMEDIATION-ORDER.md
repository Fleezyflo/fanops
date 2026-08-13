# FanOps unattended-honesty remediation order

**Purpose:** one index for the deferred stack. Product briefs own WHAT. Implement lean against the product brief + `AGENTS.md`. No `*-EXEC` / bidding / orchestrator-spawn theatre.

**Law:** [AGENTS.md](../../AGENTS.md). Do not start a later root until earlier DoD is met (or the same ordered PR stack lands honesty first).

---

## Order (locked)

| # | Root | Product brief | Status (2026-08-13) |
|---|------|---------------|---------------------|
| **1** | Config truth plane | [config-truth-plane.md](config-truth-plane.md) | **Deferred** — next if not already on `main` |
| **2** | Fail-open escalation | [fail-open-escalation-policy.md](fail-open-escalation-policy.md) | **Partial on main** (MOL-960/961 spine); finish per policy DoD |
| **3** | Derived-signal primitives | [derived-signal-primitives.md](derived-signal-primitives.md) | **Partial** → [derived-signal-close-1-2.md](derived-signal-close-1-2.md) |
| **4** | Machine-health contract | [machine-health-contract.md](machine-health-contract.md) | **Deferred** — blocked on #3 close-out |
| **5** | Control vs data plane | [control-data-plane-untangle.md](control-data-plane-untangle.md) | **Deferred** — after #4 |
| **6** | Honesty ratchets | [honesty-ratchet-contract.md](honesty-ratchet-contract.md) | **Deferred** — last; needs #2+#4 symbols |

---

## Adjacent (not in 1–6; do not reorder into the middle)

| Topic | Brief | Note |
|-------|-------|------|
| Hashtag Layer A | [hashtag-scrape-effectiveness-audit.md](hashtag-scrape-effectiveness-audit.md) | Audit; remediate from audit findings + `AGENTS.md` |
| Auth decurate | [auth-decurate-plan.md](auth-decurate-plan.md) | Separate track |

---

## Recommended next waves (after this PR merges)

1. **[derived-signal-close-1-2.md](derived-signal-close-1-2.md)** — finish seam 1/2 residuals.  
2. **#1** config truth (if open) → complete **#2** escalation DoD.  
3. **#4** machine-health → **#5** plane untangle → **#6** ratchets.  
4. Hashtag remediation when operator prioritizes Layer A (orthogonal to #4).

---

## Three lenses (stack)

| Lens | Answer |
|------|--------|
| Best practice | Ordered roots; one owner per contract; CI proves semantics not call names |
| Root cause | Parallel “honesty” patches without a shared failure/health/config contract |
| Leanest | Product brief + `AGENTS.md` only — no EXEC spawn playbooks |
