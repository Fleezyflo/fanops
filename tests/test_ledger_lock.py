# tests/test_ledger_lock.py
"""H6 / M1-F — ledger concurrency under SqliteLedgerStore (sole backend)."""
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
    cfg = Config(root=tmp_path)
    store = _store(cfg)
    led = Ledger.load(cfg)
    with store.lock():
        store.write_raw(led._to_doc())
    conn = sqlite3.connect(store.db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM ledger_rows")
        conn.close()
    except Exception:
        conn.close()
    t0 = time.monotonic()
    led.save()
    assert time.monotonic() - t0 < 5.0
    assert store.read_raw() is not None


def test_live_writer_excludes_second_acquirer_with_typed_error(tmp_path):
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
        assert inside.wait(5)
        t0 = time.monotonic()
        with pytest.raises(LockBusyError):
            with waiter_store.lock(timeout=0.5):
                pass
        assert time.monotonic() - t0 >= 0.5
    finally:
        release.set()
        t.join(5)


def test_lock_released_after_commit_lets_next_acquirer_in(tmp_path):
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
        assert inside.wait(5)
        rc = cli.main(["ingest"])
    finally:
        release.set()
        t.join(5)
    assert rc != 0
    assert "Traceback" not in capsys.readouterr().err


def _hold_transaction_then_write(root, started, release):
    cfg = Config(root=str(root))
    with Ledger.transaction(cfg) as led:
        started.set()
        release.wait(5)
        led.add_source(Source(id="held", source_path="/h.mp4"))


def test_transaction_holds_lock_across_the_whole_block(tmp_path):
    cfg = Config(root=tmp_path)
    Ledger.load(cfg).save()
    started = multiprocessing.Event()
    release = multiprocessing.Event()
    p = multiprocessing.Process(target=_hold_transaction_then_write, args=(tmp_path, started, release))
    p.start()
    try:
        assert started.wait(5)
        raised = False
        try:
            with Ledger.transaction(cfg, timeout=0.5):
                pass
        except LockBusyError:
            raised = True
        assert raised
    finally:
        release.set()
        p.join(5)
        assert p.exitcode == 0
    assert "held" in Ledger.load(cfg).sources
