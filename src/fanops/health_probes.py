# src/fanops/health_probes.py — live probes, snapshot readers, dep/daemon health checks.
from __future__ import annotations
import logging
from datetime import datetime, timezone

from fanops.config import Config
from fanops.health_projectors import project_deps_from_rows
from fanops.health_types import (
    STRIP_METRICS_CHECK_LABEL,
    DepHealth,
    Severity,
)

_log = logging.getLogger("fanops.health")

_STAGE_HANG_CEILING_S = 3600


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
