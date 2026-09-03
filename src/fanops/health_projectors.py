# src/fanops/health_projectors.py — pure HealthReport projectors (MOL-965 WP2).
from __future__ import annotations
from typing import NamedTuple

from fanops.config import Config
from fanops.health_types import (
    DAEMON_CHECK_LABEL_NEEDLE,
    HALF_LIVE_CHECK_LABEL,
    STRIP_METRICS_CHECK_LABEL,
    DepHealth,
    HealthReport,
    Severity,
    check_severity,
    overall_severity,
    report_is_healthy,
)

_PROM_HELP = {
    "fanops_posts": ("Posts by lifecycle state", "gauge"),
    "fanops_posts_actionable": ("Awaiting-approval posts on a live lineage (the operator worklist)", "gauge"),
    "fanops_awaiting_moments": ("Distinct clips awaiting operator approval", "gauge"),
    "fanops_daemon_heartbeat_age_seconds": ("Seconds since last daemon heartbeat", "gauge"),
    "fanops_daemon_heartbeat_stale": ("1 when daemon heartbeat exceeds stale threshold", "gauge"),
    "fanops_dep_up": ("Runtime dependency up (1) or down (0)", "gauge"),
    "fanops_metrics_degraded": ("1 when a metrics read degraded fail-open", "gauge"),
}


def _prom_gauge(name: str, value: int | float, labels: dict | None = None) -> str:
    lbl = ""
    if labels:
        lbl = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
    return f"{name}{lbl} {value}"


class HalfLiveState(NamedTuple):
    """FANOPS_LIVE=1 but nothing routes live (or compute failed → not solid LIVE)."""
    is_half_live: bool
    hint: str
    compute_error: str | None = None


def half_live_state(cfg: Config) -> HalfLiveState:
    """THE half-live compute — doctor check assembly + strip/Go-Live share this (MOL-965 WP2).

    Never fails open to solid LIVE on compute error. Escalation posture decides raise vs surface.
    """
    try:
        if not cfg.is_live:
            return HalfLiveState(False, "", None)
        if cfg.live_route_exists:
            return HalfLiveState(False, "", None)
        raw = cfg.poster_backend_raw or "(unset)"
        hint = (f"LIVE flag is set but nothing routes live — FANOPS_POSTER={raw} is ignored "
                "(it's a legacy bridge, not the switch). Check .env / the Go-Live tab: route a "
                "channel to a provider with creds, or flip back to dryrun.")
        return HalfLiveState(True, hint, None)
    except Exception as exc:
        from fanops.escalation import EscalationPosture, decide
        err = str(exc)[:160]
        if decide("operator", 0) is EscalationPosture.nonzero:
            return HalfLiveState(
                True,
                f"could not compute live-route coherence ({err}) — not confirmed LIVE; "
                f"not treating as solid LIVE",
                err,
            )
        raise


def project_half_live(report: HealthReport) -> tuple[bool, str]:
    """Pure: half-live badge from the live-route check in HealthReport (no re-probe)."""
    for c in report.checks:
        if c.get("label") == HALF_LIVE_CHECK_LABEL or "live route exists" in (c.get("label") or ""):
            if check_severity(c) in (Severity.FAIL, Severity.UNKNOWN) or not c.get("ok", True):
                return True, c.get("hint") or ""
            return False, ""
    return False, ""


def project_daemon_slice(report: HealthReport) -> dict | None:
    """Pure: pump/daemon check slice from HealthReport."""
    for c in report.checks:
        if DAEMON_CHECK_LABEL_NEEDLE in (c.get("label") or ""):
            return {
                "label": c["label"],
                "ok": bool(c.get("ok", True)),
                "severity": check_severity(c).value,
                "hint": c.get("hint") or "",
            }
    return None


def project_golive_readiness(report: HealthReport) -> dict:
    """Pure: Go-Live readiness fields from one HealthReport (no second doctor assembly)."""
    half, hint = project_half_live(report)
    return {
        "checks": report.checks,
        "notes": report.notes,
        "half_live": half,
        "half_live_hint": hint,
        "severity": overall_severity(report),
        "healthy": report_is_healthy(report),
        "deps": report.deps,
        "daemon_slice": project_daemon_slice(report),
    }


def project_strip_health(report: HealthReport) -> dict:
    """Pure: Home-strip health badges from HealthReport (incl. strip-metrics freshness)."""
    half, hint = project_half_live(report)
    strip_unknown = any(
        c.get("label") == STRIP_METRICS_CHECK_LABEL and check_severity(c) is Severity.UNKNOWN
        for c in report.checks
    )
    return {
        "half_live": half,
        "half_live_hint": hint,
        "severity": overall_severity(report).value,
        "healthy": report_is_healthy(report),
        "daemon_slice": project_daemon_slice(report),
        "strip_metrics_unknown": strip_unknown,
    }


def project_daemon_strip(
    snap: dict,
    *,
    age: float | None,
    stale: bool,
    pending_gates=None,
    run_line: str | None = None,
    alive_mid: bool = False,
) -> dict:
    """Pure: Home daemon partial from snapshot FACTS + live heartbeat/activity.

    `snap["verdict"]` is never an input. Loop heartbeat lands only after a pass
    completes; live activity (`alive_mid` or a non-idle run_line) is the mid-pass
    signal `daemon.status` already uses via `daemon_progress`."""
    out = dict(snap)
    live_activity = bool(alive_mid) or bool(run_line and run_line != "run=idle")
    loaded = bool(out.get("loaded")) or live_activity
    out["loaded"] = loaded
    out["pending_gates"] = pending_gates
    out["heartbeat_age_s"] = age
    if live_activity or (loaded and not stale):
        out["verdict"] = "alive"
    elif loaded and age is None:
        out["verdict"] = "loaded but no heartbeat yet"
    elif loaded and stale:
        out["verdict"] = f"loaded but stale (last heartbeat {int(age)}s ago)"
    if run_line and run_line != "run=idle":
        out["run_line"] = run_line
    return out


def project_deps_from_rows(rows: list) -> list[DepHealth]:
    """Pure: snapshot / report dep rows → DepHealth list for Go-Live pills."""
    out: list[DepHealth] = []
    for d in rows or []:
        if isinstance(d, DepHealth):
            out.append(d)
            continue
        if not isinstance(d, dict):
            continue
        out.append(DepHealth(
            name=d.get("name") or "",
            ok=bool(d.get("ok")),
            detail=d.get("detail") or "",
            severity=d.get("severity"),
        ))
    return out


def project_prometheus_health(
    report: HealthReport,
    *,
    heartbeat: tuple[float | None, bool, int],
) -> list[str]:
    """Pure: Prometheus gauge lines from HealthReport + shared heartbeat_stale triple."""
    lines: list[str] = []
    for d in report.deps:
        lines.append(_prom_gauge("fanops_dep_up", 1 if d.ok else 0, {"dep": d.name}))
    age, stale, _iv = heartbeat
    if age is not None:
        lines.append(_prom_gauge("fanops_daemon_heartbeat_age_seconds", age))
    lines.append(_prom_gauge("fanops_daemon_heartbeat_stale", 1 if stale else 0))
    return lines
