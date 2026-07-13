# src/fanops/health_model.py — MOL-298: ONE typed health owner; doctor/health/learn_doctor are views
from __future__ import annotations
from dataclasses import dataclass, field
from typing import NamedTuple

from fanops.config import Config


class DepHealth(NamedTuple):
    """One runtime dependency's live verdict (docker / postiz / zernio)."""
    name: str
    ok: bool
    detail: str


@dataclass
class HealthReport:
    """The single health readout: setup checks, dependency rows, optional learning field-shape."""
    checks: list[dict]
    notes: list[str]
    deps: list[DepHealth] = field(default_factory=list)
    field_shape: dict | None = None

    def as_dict(self) -> dict:
        """Backward-compatible dict (doctor_report consumers)."""
        out: dict = {"checks": self.checks, "notes": self.notes}
        if self.deps:
            out["deps"] = self.deps
        if self.field_shape is not None:
            out["field_shape"] = self.field_shape
        return out

    def to_json_dict(self) -> dict:
        """Machine-readable JSON payload (MOL-299): healthy flag + serializable deps."""
        return {
            "healthy": report_is_healthy(self),
            "checks": self.checks,
            "notes": self.notes,
            "deps": [{"name": d.name, "ok": d.ok, "detail": d.detail} for d in self.deps],
            "field_shape": self.field_shape,
        }


_PROM_HELP = {
    "fanops_posts": ("Posts by lifecycle state", "gauge"),
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
    from collections import Counter
    from fanops.ledger import Ledger
    from fanops.models import PostState
    from fanops.studio.views_review import awaiting_moment_count
    _log = logging.getLogger("fanops.health")
    lines: list[str] = []
    degraded = False
    led = None
    try:
        led = Ledger.load(cfg)
        st = Counter(p.state.value for p in led.posts.values())
        for state in PostState:
            lines.append(_prom_gauge("fanops_posts", st.get(state.value, 0), {"state": state.value}))
        lines.append(_prom_gauge("fanops_awaiting_moments", awaiting_moment_count(led)))
    except Exception as exc:
        _log.warning("ledger read failed in /metrics (%s); degrading post gauges", exc)
        degraded = True
    try:
        rep = build_health_report(cfg, led=led)
        for d in rep.deps:
            lines.append(_prom_gauge("fanops_dep_up", 1 if d.ok else 0, {"dep": d.name}))
        age, stale, _iv = heartbeat_stale(cfg)
        if age is not None:
            lines.append(_prom_gauge("fanops_daemon_heartbeat_age_seconds", age))
        lines.append(_prom_gauge("fanops_daemon_heartbeat_stale", 1 if stale else 0))
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
    """Exit-code truth: any failed check or down dep -> unhealthy."""
    if any(not c.get("ok", True) for c in report.checks):
        return False
    if any(not d.ok for d in report.deps):
        return False
    return True


def _docker_dep() -> DepHealth:
    import shutil, subprocess
    _DOCKER_INFO_TIMEOUT = 8
    if not shutil.which("docker"):
        return DepHealth("docker", False, "docker CLI not installed")
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=_DOCKER_INFO_TIMEOUT)
        return DepHealth("docker", r.returncode == 0, "daemon up" if r.returncode == 0 else "daemon down")
    except Exception as exc:
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
        healthy = False
        hint = f"Postiz probe error ({str(e)[:120]}); see docs/POSTIZ_OPS.md."
    if not hint:
        hint = "Postiz backend unreachable — its health-check is nginx-only and can lie; see docs/POSTIZ_OPS.md."
    return {"label": "Postiz backend reachable (real /integrations probe, not the nginx health-check)",
            "ok": healthy, "hint": "" if healthy else hint}


def daemon_liveness_check(cfg: Config) -> dict:
    """Publish-pump liveness — doctor owns the implementation; health_model re-exports for views."""
    from fanops.doctor import _daemon_liveness_check
    return _daemon_liveness_check(cfg)


_STAGE_HANG_CEILING_S = 3600


def daemon_progress(cfg: Config) -> tuple[bool, str | None]:
    """Mid-pass liveness override: flock-held stage younger than ceiling => pump alive despite stale heartbeat."""
    try:
        from fanops.pipeline_run import run_stage_snapshot
        snap = run_stage_snapshot(cfg)
    except Exception:
        return False, None
    if not snap:
        return False, None
    line = f"mid-pass: {snap['stage']} ({snap['unit']}) {int(snap['stage_age'])}s"
    return snap["stage_age"] < _STAGE_HANG_CEILING_S, line


def heartbeat_stale(cfg: Config, *, interval: int | None = None) -> tuple[float | None, bool, int]:
    """Shared daemon heartbeat staleness (doctor + daemon.status — one threshold). Returns (age_s, stale, interval_s)."""
    from fanops import daemon
    from fanops.doctor import _DAEMON_DEFAULT_INTERVAL_S, _DAEMON_STALE_TICKS
    iv = interval if interval is not None else (daemon.installed_interval(cfg) or _DAEMON_DEFAULT_INTERVAL_S)
    try:
        age = daemon._heartbeat_age_s(cfg)
    except Exception:
        age = None
    stale = age is None or age > _DAEMON_STALE_TICKS * iv
    return age, stale, iv


def build_field_shape(cfg: Config, *, led=None, list_posts=None) -> dict | None:
    """Learning field-shape verdict — None when not applicable (no postiz key). Fail-open on fetch errors."""
    if not cfg.backend_has_creds("postiz"):
        return None
    from fanops.ledger import Ledger
    from fanops.learn_doctor import _field_shape_report_core
    led = led or Ledger.load(cfg)
    try:
        return _field_shape_report_core(led, cfg, list_posts=list_posts)
    except Exception:
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
        return {"label": "recent publish still live on platform (bounded sample)", "ok": ok,
                "hint": "" if ok else "the most recent published post could not be confirmed live — check platform / creds"}
    except Exception:
        return None


def build_health_report(cfg: Config, *, get=None, postiz_probe=None, zernio_auth=None,
                        led=None, list_posts=None, live_get=None) -> HealthReport:
    """THE health owner — composes doctor checks, deps, field-shape, bounded live confirm."""
    from fanops.doctor import _assemble_doctor_checks, _doctor_notes
    checks = _assemble_doctor_checks(cfg, get=get, postiz_probe=postiz_probe, zernio_auth=zernio_auth)
    live_chk = _bounded_live_confirm_check(cfg, get=live_get or get)
    if live_chk is not None:
        checks.append(live_chk)
    notes = _doctor_notes(cfg)
    deps = dep_health_list(cfg, postiz_probe=postiz_probe)
    fshape = build_field_shape(cfg, led=led, list_posts=list_posts)
    return HealthReport(checks=checks, notes=notes, deps=deps, field_shape=fshape)
