# tests/test_pipeline_run.py
"""Per-workspace run lease — serializes the respond→advance converge loop across drivers."""
import fcntl
import json
import os
import time

import pytest

from fanops.config import Config
from fanops.errors import RunBusyError
from fanops.pipeline_run import run_lease, run_held, run_status_line, _lock_path


def test_orphaned_run_lock_does_not_wedge_acquire(tmp_path):
    cfg = Config(root=tmp_path)
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text('{"pid": 99999, "started": "2020-01-01T00:00:00Z"}')
    t0 = time.monotonic()
    with run_lease(cfg):
        pass
    assert time.monotonic() - t0 < 2.0


def test_live_holder_excludes_second_acquirer_with_typed_error(tmp_path):
    cfg = Config(root=tmp_path)
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(RunBusyError, match="run busy"):
            with run_lease(cfg):
                pass
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_run_held_probes_flock_not_file_existence(tmp_path):
    cfg = Config(root=tmp_path)
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text('{"pid": 1}')                    # stale body, nobody holds flock
    assert run_held(cfg) is False
    assert run_status_line(cfg) == "run=idle"


def test_run_status_shows_pid_and_age_when_held(tmp_path):
    cfg = Config(root=tmp_path)
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    started = "2020-01-01T00:00:00Z"
    os.write(holder_fd, json.dumps({"pid": 4242, "started": started}).encode())
    try:
        assert run_held(cfg) is True
        line = run_status_line(cfg)
        assert "run=4242" in line and "age=" in line
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_run_lease_writes_pid_body_on_acquire(tmp_path):
    cfg = Config(root=tmp_path)
    with run_lease(cfg):
        body = json.loads(_lock_path(cfg).read_text())
        assert body["pid"] == os.getpid()
        assert "started" in body
