"""MOL-352: fanops run --loop outer sleep loop over advance()."""
import json
import pytest
from fanops.cli import main


def _setup_accounts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "0")
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))


def test_loop_rejects_sub_minute_interval(tmp_path, monkeypatch, capsys):
    """B11: bad --interval exits 2 like cmd_daemon, never a traceback."""
    _setup_accounts(tmp_path, monkeypatch)
    assert main(["run", "--loop", "--interval", "5x"]) == 2
    assert "interval" in capsys.readouterr().err.lower()


def test_loop_refreshes_snapshots_before_pass(tmp_path, monkeypatch):
    """D3: --loop writes FRESH snapshots before the pass, then after a non-None status."""
    _setup_accounts(tmp_path, monkeypatch)
    order = []
    monkeypatch.setattr("fanops.health.refresh_runtime_snapshots", lambda _cfg: order.append("refresh"))
    monkeypatch.setattr("fanops.cli._cmd_run_pass", lambda _cfg, _base: (order.append("pass") or {"ok": True}))
    monkeypatch.setattr("fanops.cli._heartbeat", lambda *_a, **_k: None)
    monkeypatch.setattr("fanops.cli.time.sleep", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError, match="stop"):
        main(["run", "--loop", "--interval", "60s"])
    assert order == ["refresh", "pass", "refresh"]


def test_oneshot_without_loop_unchanged(tmp_path, monkeypatch, capsys):
    _setup_accounts(tmp_path, monkeypatch)
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    out = capsys.readouterr().out
    assert '"heartbeat"' in out
