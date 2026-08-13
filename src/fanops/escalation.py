"""Shared fail-open escalation: postures + sole attempt ceiling over agentstep."""
from __future__ import annotations
from enum import Enum
from fanops.config import Config
from fanops.agentstep import bump_attempts as _bump, clear_attempts as _clear

class EscalationPosture(str, Enum):
    degrade = "degrade"
    refuse = "refuse"
    terminate = "terminate"
    nonzero = "nonzero"

ATTEMPT_CEILING = 3
_GATE_DETERMINISTIC_MAX = ATTEMPT_CEILING  # legacy name; sole numeric home is ATTEMPT_CEILING

def record_attempt(cfg: Config, scope: str, key: str) -> int:
    """Thin wrapper — agentstep is the only on-disk attempt store."""
    return _bump(cfg, scope, key)

def clear_attempts(cfg: Config, scope: str, key: str) -> None:
    _clear(cfg, scope, key)

def decide(failure_class: str, attempts: int) -> EscalationPosture:
    """Map failure class + attempt count to a posture. Deterministic / repeated burns at ATTEMPT_CEILING → terminate."""
    fc = (failure_class or "").strip().lower()
    if fc in ("optional", "observability", "enrichment"):
        return EscalationPosture.degrade
    if fc in ("config", "operator", "toolchain_run", "auth_publish"):
        return EscalationPosture.nonzero
    # deterministic producer + repeated transient/generic: burn shared attempts; ceiling → terminate
    if fc in ("deterministic", "context_limit", "schema", "toolchain", "transient", "generic"):
        if attempts >= ATTEMPT_CEILING:
            return EscalationPosture.terminate
        return EscalationPosture.refuse
    if attempts >= ATTEMPT_CEILING:
        return EscalationPosture.terminate
    return EscalationPosture.refuse
