# tests/test_ledger_lock.py
"""H6 / M1-E — ledger concurrency under the default SqliteLedgerStore backend.

SQLite WAL + BEGIN IMMEDIATE: a killed mid-write txn rolls back (no orphan flock wedge);
a second writer contends via SQLITE_BUSY bounded by busy_timeout → typed LockBusyError;
readers never block a writer (see test_ledger_sqlite_store.py). FANOPS_LEDGER_BACKEND=json
retains the flock-backed JsonLedgerStore — these tests exercise the sqlite default path."""
import multiprocessing
import sqlite3
import threading
import time

import pytest

from fanops.config import Config
from fanops.errors import LockBusyError
from fanops.ledger import Ledger
from fanops.ledger_sqlite import SqliteLedgerStore
from fanops.models import Source


def _store(cfg) -> SqliteLedgerStore:
    return SqliteLedgerStore(cfg)


def test_uncommitted_txn_does_not_wedge_save(tmp_path):
    # A writer killed mid-txn leaves no held lock — last COMMIT is intact and save() self-heals.
    cfg = Config(root=tmp_path)
    store = _store(cfg)
    led = Ledger.load(cfg)
    with store.lock():
        store.write_raw(led._to_doc())
    conn = sqlite3.connect(store.db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM ledger_rows")  # simulates partial write before kill -9
        conn.close()  # implicit ROLLBACK — orphaned txn, not a held lock
    except Exception:
        conn.close()
    t0 = time.monotonic()
    led.save()  # must NOT raise and must NOT stall for the timeout
    assert time.monotonic() - t0 < 5.0, "uncommitted txn wedged save() instead of self-healing"
    assert store.read_raw() is not None


def test_live_writer_excludes_second_acquirer_with_typed_error(tmp_path):
    # A genuinely-held write txn (overlapping cron) must exclude a second writer: busy_timeout
    # then raises a TYPED LockBusyError (not a bare OperationalError, not an uncaught traceback).
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    holder_store = _store(cfg)
    waiter_store = _store(cfg)
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
        t0 = time.monotonic()
        with pytest.raises(LockBusyError):
            with waiter_store.lock(timeout=0.5):
                pass
        assert time.monotonic() - t0 >= 0.5, "should have waited for busy_timeout before giving up"
    finally:
        release.set()
        t.join(5)


def test_lock_released_after_commit_lets_next_acquirer_in(tmp_path):
    # Once a live holder commits, the next writer proceeds — proves real mutual exclusion.
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    store = _store(cfg)
    holder = sqlite3.connect(store.db_path, timeout=30.0)
    holder.execute("BEGIN IMMEDIATE")
    holder.commit()
    holder.close()
    acquired = False
    with store.lock(timeout=0.5):
        acquired = True
    assert acquired


def test_cli_exits_cleanly_when_lock_busy(tmp_path, monkeypatch, capsys):
    # When save() hits a busy lock, cli.main must catch LockBusyError and return a nonzero exit
    # code WITHOUT letting a traceback escape (unattended cron must degrade, not crash-dump).
    from fanops import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("fanops.cli.Config", lambda: Config(root=tmp_path))
    monkeypatch.setattr("fanops.ledger_sqlite._DEFAULT_LOCK_TIMEOUT", 0.3, raising=False)
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    holder_store = _store(cfg)
    inside = threading.Event()
    release = threading.Event()

    def holder():
        with holder_store.lock(timeout=30):
            inside.set()
            release.wait(10)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    try:
        assert inside.wait(5), "holder never acquired the write lock"
        rc = cli.main(["ingest"])  # ingest_drops -> led.save() -> contends with the live holder
    finally:
        release.set()
        t.join(5)
    assert rc != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err  # clean message, not a stack dump


# ----------------------------------------------------------------------------
# B1 (AUDIT B4): the lock must span the WHOLE load-mutate-save, not just save().
# ----------------------------------------------------------------------------


def _hold_transaction_then_write(root, started, release):
    # Runs in a SEPARATE process: enter a transaction, signal we're inside, wait for the
    # parent to finish its exclusion check, then mutate + (implicitly) save on context exit.
    cfg = Config(root=str(root))
    with Ledger.transaction(cfg) as led:
        started.set()
        release.wait(5)
        led.add_source(Source(id="held", source_path="/h.mp4"))
        # save happens on context exit (under the still-held lock)


def test_transaction_holds_lock_across_the_whole_block(tmp_path):
    # While one process is INSIDE a transaction (before its save), a second process cannot
    # acquire the transaction lock until the first exits. This is the lost-update window the
    # save()-only lock left open: two passes both loaded a stale snapshot, last save() won.
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()  # materialize an initial ledger file
    started = multiprocessing.Event()
    release = multiprocessing.Event()
    p = multiprocessing.Process(target=_hold_transaction_then_write, args=(tmp_path, started, release))
    p.start()
    try:
        assert started.wait(5), "child never entered the transaction"
        raised = False
        try:
            with Ledger.transaction(cfg, timeout=0.5):
                pass
        except LockBusyError:
            raised = True
        assert raised, "transaction lock did NOT span the block — a 2nd acquirer got in mid-transaction"
    finally:
        release.set()
        p.join(5)
        assert p.exitcode == 0
    assert "held" in Ledger.load(cfg).sources
