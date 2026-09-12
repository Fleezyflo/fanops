# Weakness register (2026-09-12)

| ID | Cluster | Class | Status | Evidence |
|----|---------|-------|--------|----------|
| U0-F01 | U0 | doc-rot | open | `full-trace-index.md` frozen 2026-07-11 vs 196 live modules |
| C1-F01 | C1 | mis-surfacing | open | `ledger_wipe.snapshot_is_restorable` stdlib log not `get_logger` |
| C2-F01 | C2 | swallow | open | `vocals._demucs_env` broad `except Exception` |
| C3-F01 | C3 | cost | accepted | dual ffmpeg grid pass per 2-shot clip (`framing._compute_track`) |
| C4-F01 | C4 | swallow | mitigated | `persona_directives.persona_facts` now logs; catch still broad |
| C5-F01 | C5 | design-note | accepted | `hashtags.vet_hashtags` cap-window floor semantics |
| C6-F01 | C6 | invariant | open | `crosspost._JITTER_MAX` monotonicity comment-only |
| C7-F01 | C7 | silent-fail | open | `meta_graph._read_queries` no log on corrupt file |
| C8-F01 | C8 | mis-report | open | `doctor` half_live fallback masks config corruption |
| C9-F01 | C9 | guard-gap | open | `app_routes_live.do_wipe_confirm` no preview server gate |
| C10-F01 | C10 | doc-rot | closed | `zero_post_clips` wired in `app_routes_home.py` |
| H-CI-F01 | H-CI | gate-gap | closed | `test_production_audit_gates.py` covers C-partition |
| H-DOCS-F01 | H-DOCS | doc-rot | open | subsystem traces not regen for +87 modules |
| H-INT-F01 | H-INT | taxonomy | accepted | ARCH-001 S-map vs C-map dual partition by design |
