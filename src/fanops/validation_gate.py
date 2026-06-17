"""Phase 2 — the OFF-until-proven gate. The speculative learning stack (variant_amplify especially,
which re-mines sources off lift_score) must not ACT on metrics whose field shape has never been
confirmed against live Blotato. learning_validated(cfg) is True only after `fanops cutover metrics`
reconciled a REAL row against track._W (cutover.json metrics_confirmed). Until then the consequential
actuator stays inert even with its kill switch ON — converting 'default OFF' (trust the operator)
into 'OFF until proven' (structural). Pure read, no side effects; takes cfg, imports no actuator."""
from __future__ import annotations
import json
from fanops.config import Config

# P3->P4 SIGNAL gate (mirrors variant_learning.variant_min_posts=8): one post proves PLUMBING, not
# signal. A dim is rankable only once at least _MIN_ATTRIBUTED_N analyzed+attributed posts back each of
# at least _MIN_VALUES distinct values — otherwise UCB explores forever on n≈1 cells and never exploits.
_MIN_ATTRIBUTED_N = 8
_MIN_VALUES = 2


def learning_validated(cfg: Config) -> bool:
    p = cfg.cutover_path
    if not p.exists():
        return False
    try:
        return bool(json.loads(p.read_text()).get("metrics_confirmed"))
    except Exception:
        return False                                # corrupt scratch file -> treat as unvalidated


def enough_attributed_signal(led, dim: str, *, min_n: int = _MIN_ATTRIBUTED_N,
                             min_values: int = _MIN_VALUES) -> bool:
    """SIGNAL half of the P4 gate: True iff aggregate_by_dim shows at least `min_n` attributed posts in
    each of at least `min_values` distinct values of `dim`. Lazy-imports digest so validation_gate stays
    a pure read that pulls in no actuator (and no import cycle)."""
    from fanops.digest import aggregate_by_dim
    agg = aggregate_by_dim(led, dim)
    return sum(1 for row in agg.values() if row.get("n", 0) >= min_n) >= min_values


def p4_unlocked(led, cfg: Config, dim: str) -> bool:
    """The full P4 unlock for ranking `dim`: PLUMBING proven (cutover metrics_confirmed) AND enough
    attributed SIGNAL. Both are required — never rank a dim on a live metrics shape that was never
    confirmed, nor on thin data. P4's actuators stay frozen until this is True for the dim they rank."""
    return learning_validated(cfg) and enough_attributed_signal(led, dim)
