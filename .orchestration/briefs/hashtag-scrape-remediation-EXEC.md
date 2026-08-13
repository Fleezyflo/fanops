# EXEC — Hashtag scrape remediation (after audit)

**You are the orchestrator.** Product truth for findings: [hashtag-scrape-effectiveness-audit.md](hashtag-scrape-effectiveness-audit.md).  
**This brief:** how to land remediation without bloating into health-contract / derived-signal / config-truth work.

**Depends on:** operator prioritization; live session health (MOL-793 / `scrape-login` is operator-only).  
**Law:** [AGENTS.md](../../AGENTS.md). No local `pytest`. Do not edit `lanes.json`.

---

## Mission (one sentence)

Make Layer A **honest and effective**: cooldown/session stalls are loud; `last_complete_pass` remains the freshness clock; Studio/doctor/run.log agree — without Meta Graph fallback or a second hashtag writer.

---

## Three lenses

| Lens | Binding |
|------|---------|
| Best practice | One writer (`fanops_hashtags`), one freshness key (`last_complete_pass`), one outage surface |
| Root cause | Frozen scrape / dead session reads as “quiet success”; Studio omits cooldown reason (MOL-742) |
| Leanest | Surface reason + severity already in run.log; finish MOL-742 wiring; no new scrape taxonomy |

---

## Hard bans

| Ban | Why |
|-----|-----|
| Silent Graph fallback when scrape missing | Contract: abort loud |
| Second writer of `hashtags.json` | Ownership drift |
| Expanding into #1–#6 honesty stack | Wrong brief |
| Local `pytest` / live `scrape-login` from agent | Operator / CI only |
| Mass doctor exit redesign “while here” | Owned by #2 fail-open |

---

## Work packages (serial)

| WP | Objective | Allowlist (α) | DoD |
|----|-----------|---------------|-----|
| **WP1** | Confirm audit still true on tip of `main` | read-only `rg` / Studio hashtag views | Written delta: still frozen? / fixed? |
| **WP2** | MOL-742 — surface cooldown reason on Studio hashtag UI | `views_hashtags.py`, templates under hashtags, tests | Reason string visible when cooldown active; no mtime lie |
| **WP3** | Doctor / digest agreement with Layer A outage | `doctor` hashtag check only if already on affect graph | No fake PASS when cooldown/session dead; else STOP and file under #2 |
| **WP4** | Optional: effectiveness tuning **only if** audit named a concrete knob | named symbols from audit | One change; measure before/after in PR body |

---

## Acceptance

- [ ] No second `hashtags.json` writer  
- [ ] Freshness still `last_complete_pass` (not file mtime)  
- [ ] Cooldown/session stall visible in Studio when configured  
- [ ] `./scripts/check.sh` green; CI tests only  

---

## Report format

1. WP outcomes + symbols  
2. Diff stat  
3. Still deferred (Meta Graph, scrape-login, #2 doctor exit if blocked)  
