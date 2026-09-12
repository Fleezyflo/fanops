"""MOL-352: fanops run --loop outer sleep loop over advance()."""
import json
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


def test_oneshot_without_loop_unchanged(tmp_path, monkeypatch, capsys):
    _setup_accounts(tmp_path, monkeypatch)
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    out = capsys.readouterr().out
    assert '"heartbeat"' in out
