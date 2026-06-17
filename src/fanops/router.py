# src/fanops/router.py
"""M2 (structural-hooks): the hook-strategy ROUTER — a read-only, Moment-level classifier that runs
AFTER the specificity critic (hookjudge.ingest_hook_judge) and BEFORE the render loop. It records a
per-Moment `hook_strategy` reason and RENDERS NOTHING; the existing render loop reads the annotation.
Default-OFF (cfg.hook_router): observe-only, so an additive annotation is the sole delta (non-regression).
M2 emits text | clean_final | clean_awaiting_strategy:impact_cut; `stitch:<format>` only becomes
reachable when a format handler ships (M4 impact-cut). A clean_awaiting_strategy Moment is preserved
from reconcile_moments GC (ledger.py) so a future strategy can re-route it."""
from __future__ import annotations

# Proven structural-hook families, reserved NOW so clean_awaiting_strategy:<key> can name a
# reserved-but-unbuilt strategy (PRD resolved decision, 2026-06-17). MVP builds impact_cut (M4) first,
# then intro_tease (M6); the rest are reserved slots filled in M9.
STRATEGY_KEYS: tuple[str, ...] = ("impact_cut", "intro_tease", "cold_open", "open_loop",
                                  "before_after", "pov_card", "loop", "reaction")

# Routing reasons recorded on Moment.hook_strategy:
TEXT = "text"                                # the on-screen text hook survived the critic — no structural hook
CLEAN_FINAL = "clean_final"                  # clean clip, no structural strategy applies — ship bare
CLEAN_AWAITING = "clean_awaiting_strategy"   # clean clip reserved for a strategy not yet built (GC-preserved)

def awaiting(key: str) -> str:
    """`clean_awaiting_strategy:<key>` — reserve a clean clip for a not-yet-built strategy."""
    return f"{CLEAN_AWAITING}:{key}"
