<!-- Generated: 2026-06-16 -->
# Scaffolding verdict (audit "over-built" items)

The effectiveness audit flagged three items as possibly over-engineered. Verdict after
review: **KEEP all three** — each is cheap and earns real value; removing them is churn
without payoff. Documented here so the call isn't re-litigated.

| Item | Where | Audit concern | Verdict + rationale |
|---|---|---|---|
| Schema-migration framework | `ledger.py` (`_MIGRATIONS`, `SCHEMA_VERSION`, newer-schema refusal) | ships one identity no-op migration | **KEEP.** Near-zero cost; the first real schema change needs exactly this (forward-safety + downgrade refusal already protect a future-version ledger from silent field drop). Removing it now means rebuilding it under pressure later. |
| Dual ASR engine resolver | `transcribe.py` (`_resolve_model`, faster-whisper ↔ legacy whisper) | elaborate for a single-operator tool | **KEEP.** It is graceful degradation, not speculation: faster-whisper is the fast path, the legacy `whisper` CLI is the fallback when it's absent (CI/air-gapped hosts). The fork is the difference between "transcribes" and "crashes on a host without faster-whisper." |
| `publish_lead_minutes` | `config.py` | defaults to 0 (no behavior) | **KEEP.** A real editorial knob (shift the publish window) with a safe default of off. Harmless at 0; deleting a working, defaulted-off config knob is pure churn. |

## Deferred (separate decision, NOT in this hardening pass)
The **learning stack** (`variant_learning` / `variant_amplify` / `variant_transfer`, `track.lift_score`,
`validation_gate`) is provably inert until a live `cutover.json metrics_confirmed` exists. It is
**left exactly as-is** by this pass — the keep-vs-remove decision is its own future call once the
system has posted live and there is one real metrics row to validate `lift_score` against.
