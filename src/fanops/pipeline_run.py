# src/fanops/pipeline_run.py
"""Per-workspace run lease — mutual exclusion for the respond→write_request→advance converge loop.

Mirrors stage_lock / ledger._file_lock: fcntl.flock LOCK_NB on 00_control/.run.lock. The flock is
the authority; the lockfile body (pid + started ISO) is advisory for status/diagnostics. Released on
context exit; kernel releases on process death so kill -9 self-heals with no manual rm."""
from __future__ import annotations
import fcntl, json, logging, os, time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.errors import RunBusyError, fail_open

_LOCK_NAME = ".run.lock"
_log = logging.getLogger(__name__)
_note_stage_warned = False


def _note_stage_log(msg, *args, **kwargs):
    global _note_stage_warned
    if _note_stage_warned:
        return
    _note_stage_warned = True
    _log.warning(msg, *args, **kwargs)


def _lock_path(cfg: Config) -> Path:
    return cfg.control / _LOCK_NAME


def _read_body(lock_path: Path) -> dict:
    try:
        return json.loads(lock_path.read_text() or "{}")
    except Exception:
        return {}


def _iso_age(raw: str) -> int:
    try:
        t0 = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(time.time() - t0.timestamp())
    except Exception:
        return 0


def _stage_age(body: dict) -> int:
    stage_started = body.get("stage_started")
    if not stage_started:
        return 0
    return _iso_age(stage_started)


def note_stage(cfg: Config, stage: str, unit_id: str) -> None:
    """Advisory mid-pass heartbeat — writes stage/unit into .run.lock body without holding flock."""
    lock_path = _lock_path(cfg)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with fail_open("note_stage", log=_note_stage_log):
        body = _read_body(lock_path)
        payload: dict = {"pid": body.get("pid", os.getpid()), "stage": stage, "unit": unit_id,
                         "stage_started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if body.get("started"):
            payload["started"] = body["started"]
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        try:
            os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, json.dumps(payload).encode())
        finally:
            os.close(fd)


def run_stage_snapshot(cfg: Config) -> dict | None:
    """{stage, unit, stage_age} when flock held and body carries stage; else None."""
    if not run_held(cfg):
        return None
    body = _read_body(_lock_path(cfg))
    stage = body.get("stage")
    if not stage:
        return None
    return {"stage": stage, "unit": body.get("unit", "?"), "stage_age": _stage_age(body)}


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
    """run=idle | run=<pid> age=<s> [stage=<stage>:<unit> stage_age=<s>] — probes flock, reads body."""
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
            age = _iso_age(started) if started else 0
            line = f"run={pid} age={age}"
            stage = body.get("stage")
            if stage:
                unit = body.get("unit", "?")
                line += f" stage={stage}:{unit} stage_age={_stage_age(body)}"
            return line
    finally:
        os.close(fd)
