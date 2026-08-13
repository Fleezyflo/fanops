# FanOps unattended-honesty remediation order

**Purpose:** one index for the deferred stack. Product briefs own WHAT; `*-EXEC.md` / `*-execute.md` own HOW.  
**Law:** [AGENTS.md](../../AGENTS.md). Do not start a later root until earlier DoD is met (or the same ordered PR stack lands honesty first).

---

## Order (locked)

| # | Root | Product brief | Execute brief | Status (2026-08-13) |
|---|------|---------------|---------------|---------------------|
| **1** | Config truth plane | [config-truth-plane.md](config-truth-plane.md) | [config-truth-plane-EXEC.md](config-truth-plane-EXEC.md) | **Deferred** — next if not already on `main` |
| **2** | Fail-open escalation | [fail-open-escalation-policy.md](fail-open-escalation-policy.md) | [fail-open-escalation-exec.md](fail-open-escalation-exec.md) | **Partial on main** (MOL-960/961 spine); finish per policy DoD |
| **3** | Derived-signal primitives | [derived-signal-primitives.md](derived-signal-primitives.md) | *(sole implementer; no EXEC)* | **Partial** this PR → [derived-signal-close-1-2.md](derived-signal-close-1-2.md) |
| **4** | Machine-health contract | [machine-health-contract.md](machine-health-contract.md) | [machine-health-contract-EXEC.md](machine-health-contract-EXEC.md) | **Deferred** — blocked on #3 close-out |
| **5** | Control vs data plane | [control-data-plane-untangle.md](control-data-plane-untangle.md) | [control-data-plane-execute.md](control-data-plane-execute.md) | **Deferred** — after #4 |
| **6** | Honesty ratchets | [honesty-ratchet-contract.md](honesty-ratchet-contract.md) | [honesty-ratchet-contract-EXEC.md](honesty-ratchet-contract-EXEC.md) | **Deferred** — last; needs #2+#4 symbols |

---

## Adjacent (not in 1–6; do not reorder into the middle)

| Topic | Brief | Note |
|-------|-------|------|
| Hashtag Layer A | [hashtag-scrape-effectiveness-audit.md](hashtag-scrape-effectiveness-audit.md) | Audit; remediate via [hashtag-scrape-remediation-EXEC.md](hashtag-scrape-remediation-EXEC.md) |
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
| Best practice | One registration plane → one escalation law → honest derived signals → one health vocabulary → one observe path → semantic ratchets |
| Root cause | Dual config worlds + log-only fail-open + wrong age/freshness primitives + multi-channel “health” |
| Leanest | Execute existing briefs in order; refuse parallel “while we’re here” roots |
