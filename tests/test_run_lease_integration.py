# tests/test_run_lease_integration.py
"""Acceptance: concurrent drivers refuse cleanly; inner advance never re-acquires."""
import fcntl
import os

from fanops.config import Config
from fanops.cli import main


def test_concurrent_run_gets_run_busy_error(tmp_path, monkeypatch, capsys):
    cfg = Config(root=tmp_path)
    monkeypatch.chdir(tmp_path)
    lp = cfg.control / ".run.lock"
    lp.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        rc = main(["run"])
        assert rc == 1
        assert "run busy" in capsys.readouterr().err
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_run_prepare_completes_while_holding_lease(tmp_path, monkeypatch):
    """No same-process deadlock: run_prepare holds the lease across respond→advance."""
    monkeypatch.chdir(tmp_path)
    from fanops.studio import actions_run
    cfg = Config(root=tmp_path)

    def fake_advance(c, *, base_time):
        return {"awaiting": {"moments": 0, "captions": 0, "moment_hooks": 0}}

    class NoopResponder:
        def answer_pending(self, c):
            return 0

    monkeypatch.setattr("fanops.pipeline.advance", fake_advance)
    monkeypatch.setattr("fanops.responder.get_responder", lambda c: NoopResponder())
    res = actions_run.run_prepare(cfg)
    assert res.ok is True


def test_concurrent_respond_gets_run_busy_error(tmp_path, monkeypatch, capsys):
    # M23: cmd_respond must acquire run_lease like _cmd_run_pass — refuse when another driver holds it.
    cfg = Config(root=tmp_path)
    monkeypatch.chdir(tmp_path)
    lp = cfg.control / ".run.lock"
    lp.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        rc = main(["respond"])
        assert rc == 1
        assert "run busy" in capsys.readouterr().err
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)
