# src/fanops/health_model.py — MOL-298/MOL-965: single constructor (build_health_report);
# primary operator channel = fanops doctor; Studio/metrics = projectors; /healthz = process-only.
# Severity is the public check contract (not ok+warn soft-lies).
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, NamedTuple

from fanops.config import Config

_log = logging.getLogger("fanops.health")

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


def render_prometheus_metrics(cfg: Config) -> str:
    """Prometheus text exposition from ledger state + HealthReport. Fail-open: never raises."""
    import logging
    from fanops.ledger import Ledger
    from fanops.models import PostState
    from fanops.studio.views_review import awaiting_moment_count
    _log = logging.getLogger("fanops.health")
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


def report_is_healthy(report: HealthReport) -> bool:
    """Exit-code truth (MOL-965): healthy iff overall severity ∈ {OK, INFO, WARN}.

    WARN is non-blocking by construction — progress-blocking sensors emit FAIL (MOL-960).
    UNKNOWN on required signals ranks with FAIL → unhealthy. Never maps UNKNOWN → healthy.
    """
    return overall_severity(report) in (Severity.OK, Severity.INFO, Severity.WARN)


# ── MOL-965 WP2: one half-live compute + pure projectors of HealthReport ──────────────


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


def _docker_dep() -> DepHealth:
    import shutil, subprocess
    _DOCKER_INFO_TIMEOUT = 8
    if not shutil.which("docker"):
        return DepHealth("docker", False, "docker CLI not installed")
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=_DOCKER_INFO_TIMEOUT)
        return DepHealth("docker", r.returncode == 0, "daemon up" if r.returncode == 0 else "daemon down")
    except Exception as exc:
        _log.warning("_docker_dep: docker info failed (%s)", exc)
        return DepHealth("docker", False, f"{type(exc).__name__}")


def _postiz_probe(cfg: Config, *, probe=None):
    """ONE Postiz reach probe — shared by dep-health and doctor checks (no duplicate heuristic)."""
    from fanops.post.postiz import postiz_health_probe
    probe = probe or postiz_health_probe
    return probe(cfg)


def postiz_dep_health(cfg: Config, *, probe=None) -> DepHealth:
    """Map the unified Postiz probe to a DepHealth row (system_health / Studio strip)."""
    if not cfg.backend_has_creds("postiz"):
        return DepHealth("postiz", True, "skipped (not configured)")
    import logging
    _log = logging.getLogger("fanops.health")
    if not (cfg.postiz_url or "").strip():
        return DepHealth("postiz", False, "not configured")
    try:
        h = _postiz_probe(cfg, probe=probe)
    except Exception as exc:
        _log.warning("postiz_health_probe unavailable, falling back to host-alive: %s", type(exc).__name__)
        return _http_reachable(cfg.postiz_url, "postiz")
    if h.healthy:
        return DepHealth("postiz", True, "reachable")
    if h.status_code is not None:
        return DepHealth("postiz", False, f"answers HTTP but API unhealthy ({h.status_code}) — publishes stalled")
    return DepHealth("postiz", False, "unreachable")


def _http_reachable(url: str | None, name: str) -> DepHealth:
    import requests
    url = (url or "").rstrip("/")
    if not url:
        return DepHealth(name, False, "not configured")
    try:
        requests.get(url, timeout=3)
        return DepHealth(name, True, "reachable")
    except requests.exceptions.RequestException:
        return DepHealth(name, False, "unreachable")


def zernio_dep_health(cfg: Config) -> DepHealth:
    if not cfg.backend_has_creds("zernio"):
        return DepHealth("zernio", True, "skipped (not configured)")
    return _http_reachable(cfg.zernio_url, "zernio")


def dep_health_list(cfg: Config, *, postiz_probe=None) -> list[DepHealth]:
    """Runtime dependency rows — docker via health._docker_health when available (test patch compat)."""
    from fanops import health as health_mod
    from fanops.health import _postiz_compose_dir
    if _postiz_compose_dir(cfg) is not None:
        docker = health_mod._docker_health() if hasattr(health_mod, "_docker_health") else _docker_dep()
    else:
        docker = DepHealth("docker", True, "skipped (not configured)")
    return [docker, postiz_dep_health(cfg, probe=postiz_probe), zernio_dep_health(cfg)]


def postiz_doctor_check(cfg: Config, *, probe=None) -> dict | None:
    """Doctor-shaped check from the SAME Postiz probe (replaces doctor._postiz_reach_check duplicate)."""
    if not cfg.backend_has_creds("postiz"):
        return None
    try:
        h = _postiz_probe(cfg, probe=probe)
        healthy = bool(getattr(h, "healthy", False))
        hint = getattr(h, "hint", "") or ""
    except Exception as e:
        _log.warning("postiz_doctor_check: probe failed (%s)", type(e).__name__)
        healthy = False
        hint = f"Postiz probe error ({str(e)[:120]}); see docs/POSTIZ_OPS.md."
    if not hint:
        hint = "Postiz backend unreachable — its health-check is nginx-only and can lie; see docs/POSTIZ_OPS.md."
    from fanops.doctor import _check
    lbl = "Postiz backend reachable (real /integrations probe, not the nginx health-check)"
    # Observe snapshot miss/stale/missing-row → UNKNOWN (required), never FAIL-as-if-probe-proved-down.
    if (not healthy) and "snapshot" in hint.lower():
        return _check(lbl, severity="unknown", hint=hint)
    return _check(lbl, healthy, hint)


def daemon_liveness_check(cfg: Config) -> dict:
    """Publish-pump liveness — doctor owns the implementation; health_model re-exports for views."""
    from fanops.doctor import _daemon_liveness_check
    return _daemon_liveness_check(cfg)


_STAGE_HANG_CEILING_S = 3600


def daemon_progress(cfg: Config) -> tuple[bool, str | None, dict | None]:
    """Activity-aware mid-pass liveness — the ONE owner both daemon.status and doctor call so the
    verdict is identical on every surface (no split-brain). Two signals combine:
      • snap = run_stage_snapshot(cfg) — the flock-held {stage, unit, stage_age} or None.
      • act  = daemon._newest_activity_ts(cfg) — the NEWEST run.log line of any kind.
    ALIVE when the log is FRESH (silent < ceiling): a stage that keeps emitting is working, however
    long it runs (a big transcribe/LLM stage legitimately runs >1h and logs every ~60s — that is NOT
    wedged). WEDGED only when a stage IS held AND the log has gone SILENT past the ceiling. A dead
    process (no launchd PID) is caught IMMEDIATELY by daemon.status — this override governs only the
    narrow "PID alive but stage silently hung" case, at the cost of up to _STAGE_HANG_CEILING_S (1h)
    detection lag (the ceiling MUST exceed the longest legitimate silent gap, or it false-flags a
    working pass). Returns the (alive_mid, line, snap) triple both callers destructure."""
    from datetime import datetime, timezone
    from fanops.errors import fail_open
    from fanops import daemon
    snap = None; act = None
    with fail_open("daemon_progress"):
        from fanops.pipeline_run import run_stage_snapshot
        snap = run_stage_snapshot(cfg)
        act = daemon._newest_activity_ts(cfg)
    silent_s = (datetime.now(timezone.utc) - act).total_seconds() if act else None
    # ALIVE: the log is fresh (still emitting) — working, however long the current stage runs.
    if silent_s is not None and silent_s < _STAGE_HANG_CEILING_S:
        line = (f"mid-pass: {snap['stage']} ({snap['unit']}) {int(snap['stage_age'])}s" if snap
                else f"active: last log {int(silent_s)}s ago")
        return True, line, snap
    # WEDGED: a stage is held AND the log has gone SILENT past the ceiling.
    if snap and silent_s is not None and silent_s >= _STAGE_HANG_CEILING_S:
        return False, f"mid-pass: {snap['stage']} ({snap['unit']}) SILENT {int(silent_s)}s", snap
    return False, None, None


def heartbeat_stale(cfg: Config, *, interval: int | None = None) -> tuple[float | None, bool, int]:
    """Shared daemon heartbeat staleness (doctor + daemon.status — one threshold). Returns (age_s, stale, interval_s)."""
    from fanops import daemon
    from fanops.doctor import _DAEMON_DEFAULT_INTERVAL_S, _DAEMON_STALE_TICKS
    iv = interval if interval is not None else (daemon.installed_interval(cfg) or _DAEMON_DEFAULT_INTERVAL_S)
    try:
        age = daemon._heartbeat_age_s(cfg)
    except Exception as exc:
        _log.warning("heartbeat_stale: heartbeat read failed (%s)", exc)
        age = None
    stale = age is None or age > _DAEMON_STALE_TICKS * iv
    return age, stale, iv


def build_field_shape(cfg: Config, *, led=None, list_posts=None) -> dict | None:
    """Learning field-shape verdict — None when not applicable (no postiz key). Fail-open on fetch errors."""
    if not cfg.backend_has_creds("postiz"):
        return None
    from fanops.field_shape import _field_shape_report_core
    from fanops.ledger import Ledger
    led = led or Ledger.load(cfg)
    try:
        return _field_shape_report_core(led, cfg, list_posts=list_posts)
    except Exception as exc:
        _log.warning("build_field_shape: field-shape report failed (%s)", exc)
        return None


def _bounded_live_confirm_check(cfg: Config, *, get=None) -> dict | None:
    """Bounded really-live sample: confirm ONE recent published IG/TikTok post (fail-open)."""
    if not cfg.is_live:
        return None
    from fanops.ledger import Ledger
    from fanops.models import PostState, Platform
    from fanops.meta_graph import confirm_post_live
    try:
        led = Ledger.load(cfg)
        candidates = [p for p in led.posts.values()
                      if p.state in (PostState.published, PostState.analyzed) and p.public_url]
        if not candidates:
            return None
        p = candidates[-1]
        if p.platform not in (Platform.instagram, Platform.tiktok):
            return None
        res = confirm_post_live(cfg, p, reported_username=p.account, get=get)
        ok = bool(res.get("confirmed"))
        from fanops.doctor import _check
        return _check(
            "recent publish still live on platform (bounded sample)", ok,
            "" if ok else "the most recent published post could not be confirmed live — check platform / creds")
    except Exception as exc:
        _log.warning("_bounded_live_confirm_check: live confirm failed (%s)", exc)
        return None



def snapshot_postiz_probe(cfg: Config):
    """Observe-mode Postiz probe: deps_health.json only — never calls postiz_health_probe."""
    from fanops.health import SnapshotFreshness, read_dep_snapshot
    from fanops.post.postiz import PostizHealth
    sr = read_dep_snapshot(cfg)
    if sr.freshness is not SnapshotFreshness.FRESH or not isinstance(sr.data, dict):
        return PostizHealth(False, None, f"Postiz health unknown (snapshot {sr.freshness.value})")
    for d in sr.data.get("deps") or []:
        if (d.get("name") or "") != "postiz":
            continue
        ok = bool(d.get("ok"))
        code = d.get("status_code")
        detail = d.get("detail") or ""
        if ok:
            return PostizHealth(True, 200 if code is None else code, "")
        return PostizHealth(False, code, detail or "Postiz unhealthy (snapshot)")
    # Configured-but-missing-from-snapshot → unknown, not silent skip-green
    if cfg.backend_has_creds("postiz"):
        return PostizHealth(False, None, "Postiz missing from dep snapshot")
    return PostizHealth(True, None, "skipped (not configured)")


def snapshot_daemon_status(cfg: Config, interval: int) -> dict:
    """Observe-mode launchd status: daemon strip snapshot only — never daemon.status()."""
    from fanops.health import SnapshotFreshness, read_daemon_strip_snapshot
    sr = read_daemon_strip_snapshot(cfg)
    if sr.freshness is not SnapshotFreshness.FRESH or not isinstance(sr.data, dict):
        # snapshot_freshness marker → doctor emits Severity.UNKNOWN (not "no heartbeat" FAIL).
        return {"installed": False, "loaded": False, "verdict": f"snapshot {sr.freshness.value}",
                "snapshot_freshness": sr.freshness.value,
                "heartbeat_age_s": None, "interval": interval}
    out = dict(sr.data)
    out.setdefault("interval", interval)
    out["snapshot_freshness"] = SnapshotFreshness.FRESH.value
    return out


def deps_from_snapshot(cfg: Config) -> list[DepHealth]:
    """Observe-mode deps: project deps_health.json — no live docker/postiz/zernio probes."""
    from fanops.health import SnapshotFreshness, read_dep_snapshot
    sr = read_dep_snapshot(cfg)
    if sr.freshness is not SnapshotFreshness.FRESH or not isinstance(sr.data, dict):
        return [DepHealth("deps", False, f"deps unknown (snapshot {sr.freshness.value})",
                          severity=Severity.UNKNOWN)]
    return project_deps_from_rows(sr.data.get("deps") or [])


def strip_metrics_freshness_check(cfg: Config) -> dict:
    """Required strip-metrics signal: non-FRESH → UNKNOWN (never calm zeros via healthy report)."""
    from fanops.doctor import _check
    from fanops.health import SnapshotFreshness, read_strip_metrics
    sr = read_strip_metrics(cfg)
    if sr.freshness is SnapshotFreshness.FRESH and isinstance(sr.data, dict):
        return _check(STRIP_METRICS_CHECK_LABEL, True)
    return _check(
        STRIP_METRICS_CHECK_LABEL, severity="unknown",
        hint=f"strip metrics snapshot {sr.freshness.value}",
    )


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
    if probe_policy != "observe":
        live_chk = _bounded_live_confirm_check(cfg, get=live_get or get)
        if live_chk is not None:
            checks.append(live_chk)
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
