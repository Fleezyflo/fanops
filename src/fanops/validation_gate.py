"""Phase 2 — the OFF-until-proven gate. The speculative learning stack (variant_amplify especially,
which re-mines sources off lift_score) must not ACT on metrics whose field shape has never been
confirmed against live Blotato. learning_validated(cfg) is True only after `fanops cutover metrics`
reconciled a REAL row against track._W (cutover.json metrics_confirmed). Until then the consequential
actuator stays inert even with its kill switch ON — converting 'default OFF' (trust the operator)
into 'OFF until proven' (structural). Pure read, no side effects; takes cfg, imports no actuator."""
from __future__ import annotations
import json
from fanops.config import Config


def learning_validated(cfg: Config) -> bool:
    p = cfg.cutover_path
    if not p.exists():
        return False
    try:
        return bool(json.loads(p.read_text()).get("metrics_confirmed"))
    except Exception:
        return False                                # corrupt scratch file -> treat as unvalidated
