# src/fanops/stage_lock.py
"""Per-stage producer lock — mutual exclusion for a SLOW subprocess (transcribe/framing/keyframes)
keyed by (stage, source_id), so the same source can never be produced twice in parallel.

This is the primitive that closes the 'two whisper subprocesses on the same audio' race that wedged
the daemon for an hour: advance() ran transcribe lock-free in prewarm AND in-lock in the main pass,
both passes saw cached.exists()==False (whisper still running, no JSON on disk yet) and spawned a
second subprocess. With this lock, the second producer blocks here, the first finishes and atomically
writes the JSON, the second enters the critical section, finds the JSON, and short-circuits — bad
path unconstructable by design, not guarded by a sentinel that can be re-raced.

Mirrors ledger._file_lock exactly (fcntl.flock with timeout, typed busy error, self-heals an orphaned
lockfile because the kernel releases the flock on process death) — the contract is identical, only
the scope (per-stage-per-source instead of per-ledger) and the typed error (StageBusyError instead
of LockBusyError) differ. Tests in tests/test_stage_lock.py pin the contract; tests/test_ledger_lock.py
pins the ledger sibling and is the canonical reference for the shape.

Lockfile path: <agent_io>/.locks/<stage>/<key>.lock — one path per (stage, source) so producers for
different sources never serialize against each other (concurrent_workers parallelism survives), and
the GC sweep (M4) has one well-known prefix to walk for orphan cleanup."""
from __future__ import annotations
import fcntl, os, time
from contextlib import contextmanager
from pathlib import Path
from fanops.config import Config
from fanops.errors import StageBusyError

# A produce-stage subprocess (whisper at the medium model on a long source) can legitimately take
# 30-60 minutes on CPU. The default is generous because the LOCK ACQUIRE only ever waits when
# another producer is mid-run (which means the cache JSON is about to land, so the second producer
# will short-circuit immediately). A short timeout would surface false StageBusyError on long but
# healthy runs. Operators / tests tune via the `timeout=` parameter or _DEFAULT_STAGE_TIMEOUT.
_DEFAULT_STAGE_TIMEOUT = 7200.0


def _lock_path_for(cfg: Config, *, stage: str, key: str) -> Path:
    """Resolve the lockfile path. One path per (stage, source) under cfg.agent_io/.locks/.
    Pure function — does not create the file or directory. The contextmanager creates parents."""
    return cfg.agent_io / ".locks" / stage / f"{key}.lock"


@contextmanager
def stage_lock(cfg: Config, *, stage: str, key: str, timeout: float | None = None):
    """Acquire the per-stage producer lock for (stage, key). Mirrors ledger._file_lock:
    fcntl.flock with a polling wait bounded by `timeout`, self-heals an orphaned lockfile (the
    kernel released the flock when the previous holder died), raises a typed StageBusyError on
    genuine contention so the caller can surface a clean operator-facing message.

    timeout=None reads the module-level _DEFAULT_STAGE_TIMEOUT at CALL time (not bound as a
    default arg), so callers and tests can tune it without re-importing."""
    if timeout is None:
        timeout = _DEFAULT_STAGE_TIMEOUT
    lock_path = _lock_path_for(cfg, stage=stage, key=key)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    start = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as err:                # held by another LIVE producer
                if time.monotonic() - start > timeout:
                    raise StageBusyError(
                        f"stage lock busy > {timeout}s ({stage}/{key}): another fanops producer is "
                        f"running this stage on this source — {lock_path}") from err
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
