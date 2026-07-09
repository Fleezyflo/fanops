# src/fanops/pipeline_run.py
"""Per-workspace run lease — mutual exclusion for the respond→write_request→advance converge loop.

Mirrors stage_lock / ledger._file_lock: fcntl.flock LOCK_NB on 00_control/.run.lock. The flock is
the authority; the lockfile body (pid + started ISO) is advisory for status/diagnostics. Released on
context exit; kernel releases on process death so kill -9 self-heals with no manual rm."""
from __future__ import annotations
import fcntl, json, os, time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.errors import RunBusyError

_LOCK_NAME = ".run.lock"


def _lock_path(cfg: Config) -> Path:
    return cfg.control / _LOCK_NAME


def _read_body(lock_path: Path) -> dict:
    try:
        return json.loads(lock_path.read_text() or "{}")
    except Exception:
        return {}


@contextmanager
def run_lease(cfg: Config):
    """Acquire the per-workspace run lease. Non-blocking: raises RunBusyError when another LIVE driver
    holds the flock. Top-level drivers wrap their converge loop; advance/respond inside a driver skip."""
    lock_path = _lock_path(cfg)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            body = _read_body(lock_path)
            pid = body.get("pid", "?")
            raise RunBusyError(f"run busy (pid {pid}) — stop it or wait") from err
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps({"pid": os.getpid(), "started": started}).encode())
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def run_held(cfg: Config) -> bool:
    """True iff another LIVE process holds the run flock (LOCK_NB probe — file existence is NOT enough)."""
    lock_path = _lock_path(cfg)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)


def run_status_line(cfg: Config) -> str:
    """run=idle | run=<pid> age=<s> — probes the flock, reads the advisory body when held."""
    lock_path = _lock_path(cfg)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return "run=idle"
        except BlockingIOError:
            body = _read_body(lock_path)
            pid = body.get("pid", "?")
            started = body.get("started")
            if started:
                try:
                    t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    age = int(time.time() - t0.timestamp())
                except Exception:
                    age = 0
            else:
                age = 0
            return f"run={pid} age={age}"
    finally:
        os.close(fd)
