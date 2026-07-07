<!-- Generated: 2026-07-03 | Source: docs/CODEMAPS + docs/CODEMAPS/subsystem-traces | Maintained by hand hereafter -->
# docs/CODEMAPS — index

Deep structural reference for `src/fanops/`. Nested `CLAUDE.md` files (package root, `studio/`, `post/`,
`tests/`) carry the token-lean per-package map + invariants and POINT here; read a codemap below only when
you need the full detail. Route yourself with the "read when…" column instead of reading everything.

| File | Read this when… |
|---|---|
| [full-trace-index.md](full-trace-index.md) | You need the master map: 109-module coverage, the 10-cluster split, the safety-verdict table (10 invariants, all HOLD), dead-code triage, silent-failure inventory, how to regenerate. |
| [anomalies.md](anomalies.md) | You need the FLAT ledger of every anomaly/dead-code lead/silent swallow, per cluster in file:line order — incl. the 5 "dead" flags corrected to live (aliased/lazy imports) and the one real wiring bug. |
| [system-lens-map.md](system-lens-map.md) | You need the EXHAUSTIVE 64-env-var table (13 Studio-settable / 51 shell-only), the ingestion chain stage-by-stage, the full hashtag-vet algorithm, or persona-field→downstream-consumer chains. Every claim carries a verified file:line. |
| [architecture.md](architecture.md) | You want narrative system architecture — the stage DAG, the two agent gates, crash-safety model. |
| [data.md](data.md) | You need the data model: the single JSON ledger, the Source→Moment→Clip→Post lifecycle, control-file schemas. |
| [dependencies.md](dependencies.md) | You need the external-binary + Python-package dependency surface (ffmpeg/whisper/yt-dlp, subprocess timeouts). |
| [fresh-ingestion-trace.md](fresh-ingestion-trace.md) | **Source of truth** for the live ingest→publish path (post–P11 casting teardown): stage DAG, fan-out arithmetic, `affinity_admits` routing. |
| [lifecycle-full-picture.md](lifecycle-full-picture.md) | Dated 2026-06-27 audit snapshot (superseded by fresh-ingestion-trace for live behavior). |
| [hashtag-lifecycle.md](hashtag-lifecycle.md) | You are touching hashtags: persona corpus → vet → post → live Graph reach → surfaced. |
| [insights-culmination.md](insights-culmination.md) | You are touching the reach-loop bias actuators (framing/timing/casting/dim) — stamp → aggregate → actuator, all gated + amplify-only. |
| [persona-levers.md](persona-levers.md) | You are touching a persona lever (content_focus/energy/hook_angle) — what each is, its validation vocab, where it bites downstream. |
| [account-connection.md](account-connection.md) | You are wiring an account to a publisher (Postiz integration ids, per-platform channels). |

Deterministic artifacts + the 10 per-cluster function-by-function traces (`C1`–`C10`) live under
`.reports/` and `docs/CODEMAPS/subsystem-traces/`; the trace index above maps clusters→files→trace docs.
The env-var operator/dev reference distilled from the lens map is [../CONFIG.md](../CONFIG.md).

**Regenerating:** the deterministic layer (`.reports/ast_extract.py` + `build_graphs.py`, stdlib-only) is
safe to re-run after any edit; the semantic layer (the C1–C10 Sonnet traces) only needs a rerun when an
area's intent changes materially. See full-trace-index.md → "How to regenerate".
