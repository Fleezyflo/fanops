"""Live dependency health + best-effort bring-up (Issue 1: "nothing should be silently off").

MOL-298: runtime dependency verdicts are a THIN VIEW over health_model (one Postiz probe owner).
`system_health(cfg)` -> health_model.dep_health_list; `ensure_up` unchanged bring-up behavior."""
from __future__ import annotations
import logging
import shutil
import subprocess
import time
from pathlib import Path

from fanops.config import Config
from fanops.health_model import DepHealth, dep_health_list, postiz_dep_health

_log = logging.getLogger("fanops.health")

_DOCKER_INFO_TIMEOUT = 8
_DOCKER_WAIT_TRIES = 30
_DOCKER_WAIT_STEP = 3


def _docker_health() -> DepHealth:
    """Docker daemon verdict (tests patch health.subprocess — kept here, not in health_model)."""
    if not shutil.which("docker"):
        return DepHealth("docker", False, "docker CLI not installed")
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=_DOCKER_INFO_TIMEOUT)
        return DepHealth("docker", r.returncode == 0, "daemon up" if r.returncode == 0 else "daemon down")
    except Exception as exc:
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
    return log
