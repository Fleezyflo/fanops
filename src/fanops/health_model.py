# src/fanops/health_model.py — MOL-298/MOL-965: single constructor (build_health_report);
# primary operator channel = fanops doctor; Studio/metrics = projectors; /healthz = process-only.
# Severity is the public check contract (not ok+warn soft-lies).
from __future__ import annotations
import logging
from typing import Literal

from fanops.config import Config
from fanops.health_probes import (
    _STAGE_HANG_CEILING_S,
    build_field_shape,
    daemon_liveness_check,
    daemon_progress,
    dep_health_list,
    heartbeat_stale,
    postiz_dep_health,
    postiz_doctor_check,
    zernio_dep_health,
)
from fanops.health_projectors import (
    HalfLiveState,
    _PROM_HELP,
    _prom_gauge,
    half_live_state,
    project_daemon_slice,
    project_daemon_strip,
    project_deps_from_rows,
    project_golive_readiness,
    project_half_live,
    project_prometheus_health,
    project_strip_health,
)
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

_log = logging.getLogger("fanops.health")


def snapshot_postiz_probe(cfg: Config):
    from fanops.health_probes import snapshot_postiz_probe as _impl
    return _impl(cfg)


def snapshot_daemon_status(cfg: Config, interval: int) -> dict:
    from fanops.health_probes import snapshot_daemon_status as _impl
    return _impl(cfg, interval)


def deps_from_snapshot(cfg: Config) -> list[DepHealth]:
    from fanops.health_probes import deps_from_snapshot as _impl
    return _impl(cfg)


def strip_metrics_freshness_check(cfg: Config) -> dict:
    from fanops.health_probes import strip_metrics_freshness_check as _impl
    return _impl(cfg)


__all__ = [
    "DAEMON_CHECK_LABEL_NEEDLE",
    "HALF_LIVE_CHECK_LABEL",
    "STRIP_METRICS_CHECK_LABEL",
    "DepHealth",
    "HalfLiveState",
    "HealthReport",
    "Severity",
    "_STAGE_HANG_CEILING_S",
    "build_health_report",
    "check_severity",
    "daemon_liveness_check",
    "daemon_progress",
    "dep_health_list",
    "half_live_state",
    "heartbeat_stale",
    "overall_severity",
    "postiz_dep_health",
    "postiz_doctor_check",
    "project_daemon_slice",
    "project_daemon_strip",
    "project_deps_from_rows",
    "project_golive_readiness",
    "project_half_live",
    "project_prometheus_health",
    "project_strip_health",
    "render_prometheus_metrics",
    "report_is_healthy",
    "zernio_dep_health",
]


def render_prometheus_metrics(cfg: Config) -> str:
    """Prometheus text exposition from ledger state + HealthReport. Fail-open: never raises."""
    from fanops.ledger import Ledger
    from fanops.models import PostState
    from fanops.studio.views_review import awaiting_moment_count
    lines: list[str] = []
    degraded = False
    led = None
    try:
        led = Ledger.load(cfg)
        st = led.state_histogram()
        for state in PostState:
            lines.append(_prom_gauge("fanops_posts", st[state], {"state": state.value}))
        # T2.5: the DERIVED companion to the raw per-state census above. `fanops_posts{state="awaiting_approval"}`
        # is a state census; this is the operator's queue (`Ledger.review_posts` = awaiting_approval AND a live
        # lineage), and the two differ by posts stranded under a retired moment. Sized in POSTS to be comparable
        # with the gauge it sits beside — a HELD clip's post counts here, because releasing the hold IS operator
        # work; `fanops_awaiting_moments` below is the clip-sized view of the same worklist and excludes it.
        lines.append(_prom_gauge("fanops_posts_actionable", led.attention_counts()["posts"]))
        lines.append(_prom_gauge("fanops_awaiting_moments", awaiting_moment_count(led)))
    except Exception as exc:
        _log.warning("ledger read failed in /metrics (%s); degrading post gauges", exc)
        degraded = True
    try:
        rep = build_health_report(cfg, led=led)
        lines.extend(project_prometheus_health(rep, heartbeat=heartbeat_stale(cfg)))
    except Exception as exc:
        _log.warning("health read failed in /metrics (%s); degrading health gauges", exc)
        degraded = True
    lines.append(_prom_gauge("fanops_metrics_degraded", 1 if degraded else 0))
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name not in seen and name in _PROM_HELP:
            help_txt, typ = _PROM_HELP[name]
            out.append(f"# HELP {name} {help_txt}")
            out.append(f"# TYPE {name} {typ}")
            seen.add(name)
        out.append(line)
    return "\n".join(out) + "\n"


def build_health_report(cfg: Config, *, get=None, postiz_probe=None, zernio_auth=None,
                        led=None, list_posts=None, live_get=None,
                        probe_policy: Literal["live", "observe"] = "live",
                        daemon_status=None) -> HealthReport:

    """Sole machine-health constructor — doctor checks, deps, field-shape, bounded live confirm.

    Operator surfaces project from this report (or thin doctor_report.as_dict); they must not
    assemble a parallel verdict. /healthz and fanops up are not consumers of this constructor.

    probe_policy:
      - 'live' (default): real network/launchd probes — doctor / CLI / Go-Live readiness.
      - 'observe': snapshot + local cfg only — CP observe (MOL-965 WP2-fix2).
        Defaults postiz_probe→snapshot_postiz_probe, daemon_status→snapshot_daemon_status,
        deps→deps_from_snapshot; strip metrics freshness check (WP3); skips Meta token
        network + bounded live confirm.
    Explicit injectables always win over policy defaults.
    """
    from fanops.doctor import _assemble_doctor_checks, _doctor_notes
    if probe_policy == "observe":
        if postiz_probe is None:
            postiz_probe = snapshot_postiz_probe
        if daemon_status is None:
            daemon_status = snapshot_daemon_status
    checks = _assemble_doctor_checks(
        cfg, get=get, postiz_probe=postiz_probe, zernio_auth=zernio_auth,
        daemon_status=daemon_status, probe_policy=probe_policy)
    notes = _doctor_notes(cfg)
    if probe_policy == "observe":
        deps = deps_from_snapshot(cfg)
        fshape = None  # no live Postiz list_posts on observe
        # WP3: strip metrics TTL is a required observe signal — UNKNOWN → unhealthy.
        checks.append(strip_metrics_freshness_check(cfg))
    else:
        deps = dep_health_list(cfg, postiz_probe=postiz_probe)
        fshape = build_field_shape(cfg, led=led, list_posts=list_posts)
    return HealthReport(checks=checks, notes=notes, deps=deps, field_shape=fshape)
