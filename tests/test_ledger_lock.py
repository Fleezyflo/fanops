# tests/test_ledger_lock.py
"""H6 — the ledger lock must SELF-HEAL an orphaned lock (a process killed mid-write between
acquire and release) instead of wedging every subsequent command for the full timeout and then
raising. The fix is an flock-based lock: the kernel releases an flock when the holding process
dies, so an orphaned lock file is inert. Genuine contention by a LIVE holder (overlapping cron)
must still be excluded and must surface as a clean, typed error the CLI can catch."""
import fcntl
import json
import os
import time

import pytest

from fanops.config import Config
from fanops.errors import LockBusyError
from fanops.ledger import Ledger, _file_lock


def test_orphaned_lock_file_does_not_wedge_save(tmp_path):
    # An orphaned ledger.lock left on disk by a kill -9'd writer (no live process holds it).
    # With the OLD O_EXCL sentinel this wedged save() for the whole timeout then raised
    # TimeoutError. With flock the leftover file is inert -> save() self-heals and succeeds.
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    cfg.lock_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.lock_path.write_text("")  # the orphaned sentinel — nobody holds an flock on it

    t0 = time.monotonic()
    led.save()  # must NOT raise and must NOT stall for the timeout
    assert time.monotonic() - t0 < 5.0, "orphaned lock wedged save() instead of self-healing"
    json.loads(cfg.ledger_path.read_text())  # ledger actually written


def test_live_holder_excludes_second_acquirer_with_typed_error(tmp_path):
    # A genuinely-held lock (a concurrent LIVE process holds the flock — the overlapping-cron
    # case) must still be mutually exclusive: the second acquirer waits up to the timeout and
    # then raises a TYPED LockBusyError (not a bare TimeoutError, not an uncaught traceback).
    cfg = Config(root=tmp_path)
    cfg.lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(cfg.lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)  # live holder takes the lock
    try:
        t0 = time.monotonic()
        with pytest.raises(LockBusyError):
            with _file_lock(cfg.lock_path, timeout=0.5):
                pass
        assert time.monotonic() - t0 >= 0.5, "should have waited for the timeout before giving up"
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_lock_released_after_block_lets_next_acquirer_in(tmp_path):
    # Once a live holder releases, the next acquirer proceeds — proves the lock is real
    # mutual exclusion, not a permanent reject.
    cfg = Config(root=tmp_path)
    cfg.lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(cfg.lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    fcntl.flock(holder_fd, fcntl.LOCK_UN)  # released
    os.close(holder_fd)
    acquired = False
    with _file_lock(cfg.lock_path, timeout=0.5):
        acquired = True
    assert acquired


def test_cli_exits_cleanly_when_lock_busy(tmp_path, monkeypatch, capsys):
    # When save() hits a busy lock, cli.main must catch LockBusyError and return a nonzero exit
    # code WITHOUT letting a traceback escape (unattended cron must degrade, not crash-dump).
    from fanops import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("fanops.cli.Config", lambda: Config(root=tmp_path))
    cfg = Config(root=tmp_path)
    cfg.lock_path.parent.mkdir(parents=True, exist_ok=True)
    # shorten the timeout so the test doesn't wait the full default
    monkeypatch.setattr("fanops.ledger._DEFAULT_LOCK_TIMEOUT", 0.3, raising=False)
    holder_fd = os.open(str(cfg.lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        rc = cli.main(["ingest"])  # ingest_drops -> led.save() -> contends with the live holder
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)
    assert rc != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err  # clean message, not a stack dump


# ----------------------------------------------------------------------------
# B1 (AUDIT B4): the lock must span the WHOLE load-mutate-save, not just save().
# ----------------------------------------------------------------------------
import multiprocessing

from fanops.models import Source


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
        # second acquirer with a short timeout must FAIL while the first holds it
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
    # after release, the first process's write is durable (committed on context exit)
    assert "held" in Ledger.load(cfg).sources
