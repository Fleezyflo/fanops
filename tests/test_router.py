# tests/test_router.py — M2 (structural-hooks): the hook-strategy ROUTER, a read-only Moment-level
# classifier that runs AFTER the specificity critic and BEFORE the render loop. It annotates
# Moment.hook_strategy and renders nothing. This file covers the reserved-key registry + routing
# vocabulary (Task 1); the route_moments classifier behavior is covered below it (Task 2).
from fanops.router import STRATEGY_KEYS, TEXT, CLEAN_FINAL, CLEAN_AWAITING, awaiting


def test_strategy_keys_registry():
    # the proven structural-hook families reserved NOW so clean_awaiting_strategy:<key> can name a
    # reserved-but-unbuilt strategy (PRD resolved decision, 2026-06-17)
    assert set(STRATEGY_KEYS) == {"impact_cut", "intro_tease", "cold_open", "open_loop",
                                  "before_after", "pov_card", "loop", "reaction"}
    assert STRATEGY_KEYS[0] == "impact_cut"          # MVP builds impact_cut first (M4)

def test_routing_reason_vocabulary():
    assert TEXT == "text" and CLEAN_FINAL == "clean_final"
    assert CLEAN_AWAITING == "clean_awaiting_strategy"
    assert awaiting("impact_cut") == "clean_awaiting_strategy:impact_cut"
