# FanOps unattended-honesty remediation order

**Purpose:** one index for the deferred stack. Product briefs own WHAT. Implement lean against the product brief + `AGENTS.md`. No `*-EXEC` / bidding / orchestrator-spawn theatre.

**Law:** [AGENTS.md](../../AGENTS.md). Do not start a later root until earlier DoD is met (or the same ordered PR stack lands honesty first).

---

## Order (locked)

| # | Root | Product brief | Status (main `b8788be8`) |
|---|------|---------------|--------------------------|
| **1** | Config truth plane | [config-truth-plane.md](config-truth-plane.md) | **LANDED** (`#962`) |
| **2** | Fail-open escalation | [fail-open-escalation-policy.md](fail-open-escalation-policy.md) | **Partial** (MOL-960/961 spine); finish per policy DoD |
| **3** | Derived-signal primitives | [derived-signal-primitives.md](derived-signal-primitives.md) | **Partial** → [derived-signal-close-1-2.md](derived-signal-close-1-2.md) |
| **4** | Machine-health contract | [machine-health-contract.md](machine-health-contract.md) | **LANDED** (`#971`) |
| **5** | Control vs data plane | [control-data-plane-untangle.md](control-data-plane-untangle.md) | **Mostly landed** (`#972`–`#975`); leftovers CPDP-07/08/09/10/13 — inventory is source of OPEN IDs |
| **6** | Honesty ratchets | [honesty-ratchet-contract.md](honesty-ratchet-contract.md) | **Deferred** — needs #2 DoD + #4 symbols (health landed; escalation DoD still open) |

---

## Adjacent (not in 1–6; do not reorder into the middle)

| Topic | Brief | Note |
|-------|-------|------|
| Hashtag Layer A | [hashtag-scrape-effectiveness-audit.md](hashtag-scrape-effectiveness-audit.md) | Audit; remediate from audit findings + `AGENTS.md` |
| Auth decurate | [auth-decurate-plan.md](auth-decurate-plan.md) | Separate track |

---

## Recommended next waves

1. Finish **#2** escalation DoD and/or **#3** [derived-signal-close-1-2.md](derived-signal-close-1-2.md).  
2. CPDP leftovers **07/08/09/10/13** (atomic briefs; one ID at a time).  
3. **#6** honesty ratchets after #2 DoD.  
4. Hashtag remediation when operator prioritizes Layer A.

---

## Three lenses (stack)

| Lens | Answer |
|------|--------|
| Best practice | Ordered roots; one owner per contract; CI proves semantics not call names |
| Root cause | Parallel “honesty” patches without a shared failure/health/config contract |
| Leanest | Product brief + `AGENTS.md` only — no EXEC spawn playbooks |
