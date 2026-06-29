# tests/test_stage_lock.py
"""The per-stage file-lock primitive — exclusion for a SLOW PRODUCER (transcribe/framing/keyframes)
keyed by (stage, key=source_id). Mirrors tests/test_ledger_lock.py contract: orphaned lockfile from a
killed producer SELF-HEALS (the kernel releases the flock on process death); a live holder excludes
a second acquirer and the wait surfaces a typed StageBusyError (not a bare TimeoutError, not a stack
dump). The lockfile lives at <agent_io>/.locks/<stage>/<key>.lock — one path per (stage, source) so
producers for different sources never serialize against each other.

The fix it pins: when advance() is called twice in rapid succession, both passes called
transcribe_source on the same source. The cache file didn't exist yet (whisper still running), the
ledger sentinel was on the throwaway prewarm ledger (never saved), and two whisper subprocesses
started on the same audio. With the lock, the second producer blocks on the lock, finds the JSON on
disk inside the lock, and short-circuits — bad path unconstructable."""
import fcntl
import os
import time

import pytest

from fanops.config import Config
from fanops.errors import StageBusyError
from fanops.stage_lock import stage_lock, _lock_path_for


def test_orphaned_lockfile_does_not_wedge_acquire(tmp_path):
    # An orphaned lockfile left by a kill -9'd producer (no live process holds it). With flock the
    # leftover file is inert — the next acquirer takes it immediately, no timeout wait.
    cfg = Config(root=tmp_path)
    lp = _lock_path_for(cfg, stage="transcribe", key="src_aaaaaaaaaaaa")
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text("")                                    # orphan sentinel; nobody holds an flock on it
    t0 = time.monotonic()
    with stage_lock(cfg, stage="transcribe", key="src_aaaaaaaaaaaa"):
        pass
    assert time.monotonic() - t0 < 5.0, "orphaned lock wedged acquire instead of self-healing"


def test_live_holder_excludes_second_acquirer_with_typed_error(tmp_path):
    # A genuine live holder (a concurrent producer) must exclude. The second acquirer waits up to
    # the timeout and then raises a TYPED StageBusyError (mirroring LockBusyError's contract).
    cfg = Config(root=tmp_path)
    lp = _lock_path_for(cfg, stage="transcribe", key="src_bbbbbbbbbbbb")
    lp.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        t0 = time.monotonic()
        with pytest.raises(StageBusyError):
            with stage_lock(cfg, stage="transcribe", key="src_bbbbbbbbbbbb", timeout=0.5):
                pass
        assert time.monotonic() - t0 >= 0.5, "should have waited for the timeout before giving up"
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_lock_released_after_block_lets_next_acquirer_in(tmp_path):
    # Once a live holder releases, the next acquirer proceeds — proves the lock is real mutual
    # exclusion, not a permanent reject.
    cfg = Config(root=tmp_path)
    lp = _lock_path_for(cfg, stage="transcribe", key="src_cccccccccccc")
    lp.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    fcntl.flock(holder_fd, fcntl.LOCK_UN)                # released
    os.close(holder_fd)
    acquired = False
    with stage_lock(cfg, stage="transcribe", key="src_cccccccccccc", timeout=0.5):
        acquired = True
    assert acquired


def test_different_stage_or_key_do_not_serialize(tmp_path):
    # The whole point of a per-(stage,key) lock is that a transcribe lock on source A does NOT
    # block a framing lock on source A, nor a transcribe lock on source B. Otherwise concurrent
    # sources would serialize through one bottleneck.
    cfg = Config(root=tmp_path)
    lp_a = _lock_path_for(cfg, stage="transcribe", key="src_dddddddddddd")
    lp_a.parent.mkdir(parents=True, exist_ok=True)
    holder_a = os.open(str(lp_a), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_a, fcntl.LOCK_EX)
    try:
        # Different STAGE, same key — must not block.
        with stage_lock(cfg, stage="framing", key="src_dddddddddddd", timeout=0.2):
            pass
        # Same stage, different KEY — must not block.
        with stage_lock(cfg, stage="transcribe", key="src_eeeeeeeeeeee", timeout=0.2):
            pass
    finally:
        fcntl.flock(holder_a, fcntl.LOCK_UN)
        os.close(holder_a)


def test_lock_path_lives_under_agent_io_dot_locks(tmp_path):
    # Convention check: lockfile lives at <agent_io>/.locks/<stage>/<key>.lock so the GC sweep
    # (M4) and operator inspection have one place to look.
    cfg = Config(root=tmp_path)
    lp = _lock_path_for(cfg, stage="transcribe", key="src_ffffffffffff")
    expected = cfg.agent_io / ".locks" / "transcribe" / "src_ffffffffffff.lock"
    assert lp == expected, f"lock path drifted: {lp!s} vs {expected!s}"
