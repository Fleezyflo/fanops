"""Live dependency health + best-effort bring-up (Issue 1: "nothing should be silently off").

The system's runtime dependencies — the Docker daemon, the Postiz API, the Zernio API — used to be able
to sit dead while the Studio ran happily, so the operator only found out via a buried downstream error
(`could not list channels`). This module makes that state FIRST-CLASS and VISIBLE:

- `system_health(cfg)` returns a live red/green verdict per dependency (surfaced on the Studio).
- `ensure_up(cfg)` is the launch bring-up: if the Docker daemon is down it starts Docker Desktop; if the
  Postiz stack is down and a compose dir is configured it `docker compose up -d`s it. Best-effort and
  fail-soft — a bring-up that can't run never blocks the launch, it just reports.

All checks are cheap and bounded; nothing here publishes or mutates the ledger."""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple
import requests
from fanops.config import Config

_log = logging.getLogger("fanops.health")

_HTTP_TIMEOUT = 3            # s — a liveness ping, not a real call
_DOCKER_INFO_TIMEOUT = 8    # s — `docker info` round-trip
_DOCKER_WAIT_TRIES = 30     # poll the daemon after launching Docker Desktop (×_DOCKER_WAIT_STEP)
_DOCKER_WAIT_STEP = 3       # s


class DepHealth(NamedTuple):
    """One dependency's live verdict. `ok` is the red/green; `detail` is a short human reason."""
    name: str
    ok: bool
    detail: str


def _docker_health() -> DepHealth:
    if not shutil.which("docker"):
        return DepHealth("docker", False, "docker CLI not installed")
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=_DOCKER_INFO_TIMEOUT)
        return DepHealth("docker", r.returncode == 0, "daemon up" if r.returncode == 0 else "daemon down")
    except Exception as exc:                              # FileNotFound / Timeout / OSError -> down, never raise
        return DepHealth("docker", False, f"{type(exc).__name__}")


def _http_reachable(url: str | None, name: str) -> DepHealth:
    url = (url or "").rstrip("/")
    if not url:
        return DepHealth(name, False, "not configured")
    try:
        requests.get(url, timeout=_HTTP_TIMEOUT)         # any HTTP answer (even 404) == the host is alive
        return DepHealth(name, True, "reachable")
    except requests.exceptions.RequestException:
        return DepHealth(name, False, "unreachable")


def postiz_health(cfg: Config) -> DepHealth:
    return _http_reachable(cfg.postiz_url, "postiz")


def zernio_health(cfg: Config) -> DepHealth:
    return _http_reachable(cfg.zernio_url, "zernio")


def system_health(cfg: Config) -> list[DepHealth]:
    """The live red/green for every runtime dependency, in launch order (Docker first — it hosts Postiz)."""
    return [_docker_health(), postiz_health(cfg), zernio_health(cfg)]


def _postiz_compose_dir() -> Path | None:
    """Where the Postiz docker-compose stack lives, so the launch can bring it up. FANOPS_POSTIZ_COMPOSE_DIR
    overrides; otherwise the conventional self-host path. Returns None when neither exists (nothing to start)."""
    v = (os.getenv("FANOPS_POSTIZ_COMPOSE_DIR") or "").strip()
    candidate = Path(v).expanduser() if v else (Path.home() / "postiz-selfhost" / "postiz-docker-compose")
    return candidate if candidate.is_dir() else None


def _start_docker(log: list[str]) -> None:
    # macOS: launch Docker Desktop, then poll the daemon (bounded) so a slow boot doesn't hang the launch.
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
    """Launch bring-up: start any down dependency the system knows how to start, best-effort. Returns a log
    of what it did (also logged). Never raises — a launch must proceed even if a bring-up can't run."""
    log: list[str] = []
    if not _docker_health().ok:
        _start_docker(log)
    compose_dir = _postiz_compose_dir()
    if compose_dir is not None and not postiz_health(cfg).ok:
        _start_postiz(compose_dir, log)
    for line in log:
        _log.info(line)
    return log
