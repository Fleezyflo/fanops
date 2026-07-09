# tests/test_traceback_chaining.py
"""MOL-107 / PKT-1 — four caught re-raises dropped the exception cause (ruff B904). A re-raise
inside an `except` block must chain the original via `raise ... from err` so the operator sees the
underlying failure (which OperationalError contended, which JSONDecodeError body) in the traceback,
not just the typed wrapper. This pins the two LOCK-CONTENTION paths (R-002 ledger store lock,
R-004 stage_lock) where the dropped cause is the actual contention error — the failure mode that
most needs the chain when an unattended cron hits genuine contention.

DIAGNOSTIC-ONLY (S5): these tests exercise the SAME live-holder contention path the existing lock
tests use (tests/test_ledger_lock.py, tests/test_stage_lock.py) with the same timeout=0.5 — they
assert ONLY the added cause chain, never a change to the lock's timeout/retry/backoff."""
import fcntl
import os
import sqlite3
import threading

import pytest

from fanops.config import Config
from fanops.errors import LockBusyError, StageBusyError
from fanops.ledger import Ledger
from fanops.ledger_sqlite import SqliteLedgerStore
from fanops.stage_lock import stage_lock, _lock_path_for


def test_ledger_lock_busy_preserves_operationalerror_cause(tmp_path):
    # R-002: a live write holder forces SqliteLedgerStore.lock to time out and raise LockBusyError.
    # The raise chains the OperationalError from SQLITE_BUSY (`from err`), so __cause__ is that
    # OperationalError — not None (the bare re-raise dropped it).
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    holder_store = SqliteLedgerStore(cfg)
    waiter_store = SqliteLedgerStore(cfg)
    inside = threading.Event()
    release = threading.Event()

    def holder():
        with holder_store.lock(timeout=30):
            inside.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert inside.wait(5), "holder never acquired the write lock"
        with pytest.raises(LockBusyError) as ei:
            with waiter_store.lock(timeout=0.5):
                pass
    finally:
        release.set()
        t.join(5)
    assert isinstance(ei.value.__cause__, sqlite3.OperationalError), \
        "LockBusyError dropped the OperationalError cause (B904 raise-from missing)"


def test_stage_lock_busy_preserves_blockingioerror_cause(tmp_path):
    # R-004: same contract for the per-stage producer lock — StageBusyError must chain the
    # BlockingIOError from the contended flock poll.
    cfg = Config(root=tmp_path)
    lp = _lock_path_for(cfg, stage="transcribe", key="src_cccccccccccc")
    lp.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(StageBusyError) as ei:
            with stage_lock(cfg, stage="transcribe", key="src_cccccccccccc", timeout=0.5):
                pass
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)
    assert isinstance(ei.value.__cause__, BlockingIOError), \
        "StageBusyError dropped the BlockingIOError cause (B904 raise-from missing)"
