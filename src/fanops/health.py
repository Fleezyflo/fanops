"""Live dependency health + best-effort bring-up (Issue 1: "nothing should be silently off").

MOL-298: runtime dependency verdicts are a THIN VIEW over health_model (one Postiz probe owner).
`system_health(cfg)` -> health_model.dep_health_list; `ensure_up` unchanged bring-up behavior."""
from __future__ import annotations
import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from fanops.config import Config
from fanops.health_model import DepHealth, dep_health_list, postiz_dep_health

_log = logging.getLogger("fanops.health")

_DOCKER_INFO_TIMEOUT = 8
_DOCKER_WAIT_TRIES = 30
_DOCKER_WAIT_STEP = 3

_SNAPSHOT_TTL_S = 1800  # 3× default daemon tick; constant beside reader (no new FANOPS_* knob)

class SnapshotFreshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    UNREADABLE = "unreadable"

@dataclass(frozen=True)
class SnapshotRead:
    freshness: SnapshotFreshness
    data: dict | None = None

def _docker_health() -> DepHealth:
    """Docker daemon verdict (tests patch health.subprocess — kept here, not in health_model)."""
    if not shutil.which("docker"):
        return DepHealth("docker", False, "docker CLI not installed")
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=_DOCKER_INFO_TIMEOUT)
        return DepHealth("docker", r.returncode == 0, "daemon up" if r.returncode == 0 else "daemon down")
    except Exception as exc:
        _log.warning("_docker_health: docker info failed (%s)", exc)
        return DepHealth("docker", False, f"{type(exc).__name__}")

def system_health(cfg: Config) -> list[DepHealth]:
    """Thin view: runtime dependency rows from the unified health model."""
    return dep_health_list(cfg)

def postiz_health(cfg: Config) -> DepHealth:
    """Thin alias — same unified probe as doctor (health_model.postiz_dep_health)."""
    return postiz_dep_health(cfg)

def zernio_health(cfg: Config) -> DepHealth:
    from fanops.health_model import zernio_dep_health
    return zernio_dep_health(cfg)

def _postiz_compose_dir(cfg: Config) -> Path | None:
    """Where the Postiz docker-compose stack lives, so the launch can bring it up. FANOPS_POSTIZ_COMPOSE_DIR
    overrides; otherwise the conventional self-host path. Returns None when neither exists (nothing to start)."""
    v = (cfg.postiz_compose_dir or "").strip()
    candidate = Path(v).expanduser() if v else (Path.home() / "postiz-selfhost" / "postiz-docker-compose")
    return candidate if candidate.is_dir() else None

def _start_docker(log: list[str]) -> None:
    if shutil.which("open"):
        subprocess.run(["open", "-a", "Docker"], capture_output=True)
        log.append("starting Docker Desktop…")
        for _ in range(_DOCKER_WAIT_TRIES):
            if _docker_health().ok:
                log.append("  Docker daemon up"); return
            time.sleep(_DOCKER_WAIT_STEP)
        log.append("  Docker daemon did not come up in time (start it manually)")
    else:
        log.append("Docker daemon down and no `open` to launch it (start Docker manually)")

def _start_postiz(compose_dir: Path, log: list[str]) -> None:
    try:
        subprocess.run(["docker", "compose", "--project-directory", str(compose_dir), "up", "-d"],
                       capture_output=True, timeout=180)
        log.append(f"bringing up Postiz ({compose_dir})…")
    except Exception as exc:
        _log.warning("_start_postiz: bring-up failed (%s)", exc)
        log.append(f"  Postiz bring-up failed: {type(exc).__name__}")

def ensure_up(cfg: Config) -> list[str]:
    """Launch bring-up: start any down dependency the system knows how to start, best-effort."""
    log: list[str] = []
    if not _docker_health().ok:
        _start_docker(log)
    compose_dir = _postiz_compose_dir(cfg)
    if compose_dir is not None and not postiz_health(cfg).ok:
        _start_postiz(compose_dir, log)
    for line in log:
        _log.info(line)
    refresh_runtime_snapshots(cfg)
    return log

def refresh_runtime_snapshots(cfg: Config) -> None:
    refresh_dep_snapshot(cfg)
    refresh_daemon_strip_snapshot(cfg)
    refresh_strip_metrics(cfg)

def refresh_dep_snapshot(cfg: Config) -> list:
    """Exactly one Postiz network call when configured (dep_health_list postiz_probe= seam)."""
    from fanops.health_model import dep_health_list
    from fanops.post.postiz import postiz_health_probe
    from fanops.controlio import write_json_atomic
    from fanops.timeutil import iso_z
    from datetime import datetime, timezone
    held = []
    def _once(c):
        if not held:
            held.append(postiz_health_probe(c))
        return held[0]
    deps = dep_health_list(cfg, postiz_probe=_once)
    postiz_status = held[0].status_code if held else None
    write_json_atomic(cfg.deps_health_path, {
        "checked_at": iso_z(datetime.now(timezone.utc)),
        "deps": [{"name": d.name, "ok": d.ok, "detail": d.detail,
                  "status_code": (postiz_status if d.name == "postiz" else None)} for d in deps],
    })
    return deps

def refresh_daemon_strip_snapshot(cfg: Config) -> dict:
    from fanops import daemon
    from fanops.controlio import write_json_atomic
    from fanops.timeutil import iso_z
    from datetime import datetime, timezone
    interval = daemon.installed_interval(cfg) or 600
    rep = daemon.status(cfg, interval=interval)
    siblings = daemon.sibling_agents_status()
    blob = {**rep, "siblings": siblings, "interval": interval,
            "checked_at": iso_z(datetime.now(timezone.utc))}
    write_json_atomic(cfg.daemon_strip_path, blob)
    return blob

def refresh_strip_metrics(cfg: Config) -> dict:
    """PendingIndex runs HERE (writer), never in build_system_strip.
    Uses Ledger + fanops.pipeline_status only — does NOT import studio.views."""
    from collections import Counter
    from fanops.ledger import Ledger
    from fanops.models import SourceState
    from fanops.pipeline_status import PendingIndex, source_backlog
    from fanops.controlio import write_json_atomic
    from fanops.timeutil import iso_z
    from datetime import datetime, timezone
    led = Ledger.load(cfg)
    idx = PendingIndex.build(cfg, led)
    bl = source_backlog(led, cfg, idx)
    by_kind = Counter(kind for _, kind, _ in idx.ordered)
    blocked = bl.blocked_on_gates or (
        by_kind.get("moments", 0) + by_kind.get("moment_hooks", 0) + by_kind.get("captions", 0))
    errored_ids = [sid for sid, s in sorted(led.sources.items())
                   if s.state in (SourceState.error, SourceState.moments_empty)]
    blob = {
        "checked_at": iso_z(datetime.now(timezone.utc)),
        "blocked_gates": int(blocked),
        "recoverable_sources": int(bl.recoverable),
        "errored_first_id": errored_ids[0] if errored_ids else None,
    }
    write_json_atomic(cfg.strip_metrics_path, blob)
    return blob

def _read_snapshot(p: Path) -> SnapshotRead:
    """Typed snapshot read: Fresh | Stale | Missing | Unreadable. Enforces checked_at TTL.
    Never probes network/launchctl/PendingIndex. Missing/stale must NOT be treated as calm zeros."""
    if not p.exists():
        return SnapshotRead(SnapshotFreshness.MISSING)
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return SnapshotRead(SnapshotFreshness.UNREADABLE)
    if not isinstance(data, dict):
        return SnapshotRead(SnapshotFreshness.UNREADABLE)
    checked = data.get("checked_at")
    if not checked:
        return SnapshotRead(SnapshotFreshness.STALE, data)
    try:
        from fanops.timeutil import parse_iso
        age = (datetime.now(timezone.utc) - parse_iso(str(checked))).total_seconds()
    except (ValueError, TypeError):
        return SnapshotRead(SnapshotFreshness.UNREADABLE, data)
    if age < 0 or age > _SNAPSHOT_TTL_S:
        return SnapshotRead(SnapshotFreshness.STALE, data)
    return SnapshotRead(SnapshotFreshness.FRESH, data)

def read_dep_snapshot(cfg: Config) -> SnapshotRead:
    return _read_snapshot(cfg.deps_health_path)

def read_daemon_strip_snapshot(cfg: Config) -> SnapshotRead:
    return _read_snapshot(cfg.daemon_strip_path)

def read_strip_metrics(cfg: Config) -> SnapshotRead:
    return _read_snapshot(cfg.strip_metrics_path)
