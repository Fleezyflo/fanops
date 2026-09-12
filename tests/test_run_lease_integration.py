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


def _write_run_accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}')


def test_run_prepare_completes_while_holding_lease(tmp_path, monkeypatch):
    """Idle ledger: real respond→advance under the lease; no deadlock, no fanops.pipeline.advance patch."""
    monkeypatch.chdir(tmp_path)
    from fanops.studio import actions_run
    cfg = Config(root=tmp_path)
    _write_run_accounts(cfg)
    res = actions_run.run_prepare(cfg)
    assert res.ok is True
    assert (res.detail or {}).get("errors", 0) == 0


def test_run_prepare_competing_flock_is_busy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.studio import actions_run
    cfg = Config(root=tmp_path)
    _write_run_accounts(cfg)
    lp = cfg.control / ".run.lock"
    lp.parent.mkdir(parents=True, exist_ok=True)
    holder_fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        res = actions_run.run_prepare(cfg)
        assert res.ok is False
        assert "busy" in (res.error or "").lower()
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_run_prepare_errors_are_not_ok(tmp_path, monkeypatch):
    """errors>0 must not report ok=True — a source in error is not a green prepare."""
    monkeypatch.chdir(tmp_path)
    from fanops.ledger import Ledger
    from fanops.models import Source, SourceState
    from fanops.studio import actions_run
    cfg = Config(root=tmp_path)
    _write_run_accounts(cfg)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(tmp_path / "a.mp4"),
                          state=SourceState.error, error_reason="boom"))
    led.save()
    res = actions_run.run_prepare(cfg)
    assert (res.detail or {}).get("errors", 0) > 0
    assert res.ok is False


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
