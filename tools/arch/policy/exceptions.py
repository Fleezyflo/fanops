"""Exception suppression and baseline approval."""
from __future__ import annotations

from ..common import GOVERNANCE, load
from .rules import BLOCKING, INFO, Finding

# ── the approved baselines (DECLARED, human-owned, reviewed) ─────────────────────────────────
def _approved(key: str, default):
    p = GOVERNANCE / "baselines.json"
    if not p.exists():
        return default
    return load(p).get(key, default)


# ── exceptions ──────────────────────────────────────────────────────────────────────────────
def _apply_exceptions(findings: list[Finding]) -> list[Finding]:
    from ..registries import active_exceptions
    active = active_exceptions()
    for f in findings:
        for exc in active:
            if exc["rule"] != f.rule:
                continue
            scope = exc.get("scope", "")
            if scope in ("*", "") or any(scope in e for e in f.evidence) or scope in f.detail:
                f.suppressed_by = exc["id"]
                f.severity = INFO
                break
    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == BLOCKING and not f.suppressed_by]
