# src/fanops/health_types.py — Severity, DepHealth, HealthReport, aggregation helpers.
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

# Locked check labels — projectors match these; doctor emits them (MOL-965 WP2/WP3).
HALF_LIVE_CHECK_LABEL = "live route exists (FANOPS_LIVE=1 actually publishes)"
DAEMON_CHECK_LABEL_NEEDLE = "publish daemon alive"
STRIP_METRICS_CHECK_LABEL = "strip metrics snapshot fresh"


class Severity(str, Enum):
    """Locked machine-health severity (MOL-965 WP1). Exit / healthy read this — not ok+warn soft-lies."""
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


# Rank for aggregation. UNKNOWN shares FAIL rank (required-signal unknown → unhealthy).
_SEV_RANK = {
    Severity.OK: 0,
    Severity.INFO: 1,
    Severity.WARN: 2,
    Severity.FAIL: 3,
    Severity.UNKNOWN: 3,
}


@dataclass(frozen=True)
class DepHealth:
    """One runtime dependency's live verdict (docker / postiz / zernio). Severity is mandatory."""
    name: str
    ok: bool
    detail: str
    severity: Severity = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        sev = self.severity
        if sev is None:
            object.__setattr__(self, "severity", Severity.OK if self.ok else Severity.FAIL)
        elif not isinstance(sev, Severity):
            object.__setattr__(self, "severity", Severity(sev))

    def as_dict(self) -> dict:
        """Plain-dict form for JSON / doctor_report — never leak raw DepHealth to consumers."""
        return {"name": self.name, "ok": self.ok, "detail": self.detail,
                "severity": self.severity.value}


def check_severity(check: dict) -> Severity:
    """Read severity from a check dict. Production checks always carry it via doctor._check."""
    raw = check.get("severity")
    if raw is not None:
        return raw if isinstance(raw, Severity) else Severity(raw)
    # Legacy hand-built test dicts only — derive; do not invent a parallel warn channel.
    if not check.get("ok", True):
        return Severity.FAIL
    if check.get("warn"):
        return Severity.WARN
    return Severity.OK


def overall_severity(report: "HealthReport") -> Severity:
    """Worst severity across checks + deps. WARN means non-blocking by construction (blocking → FAIL)."""
    worst = Severity.OK
    for c in report.checks:
        sev = check_severity(c)
        if _SEV_RANK[sev] > _SEV_RANK[worst]:
            worst = sev
    for d in report.deps:
        sev = d.severity if isinstance(d.severity, Severity) else Severity(d.severity)
        if _SEV_RANK[sev] > _SEV_RANK[worst]:
            worst = sev
    return worst


@dataclass
class HealthReport:
    """The single health readout: setup checks, dependency rows, optional learning field-shape."""
    checks: list[dict]
    notes: list[str]
    deps: list[DepHealth] = field(default_factory=list)
    field_shape: dict | None = None

    def as_dict(self) -> dict:
        """Backward-compatible dict (doctor_report consumers). Deps are plain dicts (JSON-safe)."""
        out: dict = {"checks": self.checks, "notes": self.notes}
        if self.deps:
            out["deps"] = [d.as_dict() for d in self.deps]
        if self.field_shape is not None:
            out["field_shape"] = self.field_shape
        return out

    def to_json_dict(self) -> dict:
        """Machine-readable JSON payload (MOL-299): healthy flag + serializable deps."""
        return {
            "healthy": report_is_healthy(self),
            "severity": overall_severity(self).value,
            "checks": self.checks,
            "notes": self.notes,
            "deps": [d.as_dict() for d in self.deps],
            "field_shape": self.field_shape,
        }


def report_is_healthy(report: HealthReport) -> bool:
    """Exit-code truth (MOL-965): healthy iff overall severity ∈ {OK, INFO, WARN}.

    WARN is non-blocking by construction — progress-blocking sensors emit FAIL (MOL-960).
    UNKNOWN on required signals ranks with FAIL → unhealthy. Never maps UNKNOWN → healthy.
    """
    return overall_severity(report) in (Severity.OK, Severity.INFO, Severity.WARN)
