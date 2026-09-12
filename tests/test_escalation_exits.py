"""MOL-960 Wave B: unattended exit contract — stuck gates → 1; pause → 0; empty converge → 0."""
import json

from fanops.config import Config
from fanops.ledger import Ledger


def _ready_run_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))
    Ledger.load(cfg).save()
    return cfg


def test_cmd_run_gates_blocked_exits_1(tmp_path, monkeypatch):
    from fanops import cli
    from fanops.agentstep import write_request
    cfg = _ready_run_tree(tmp_path, monkeypatch)
    write_request(cfg, kind="moments", key="s1", payload={
        "source_id": "s1", "duration": 10.0,
        "transcript": [{"start": 0.0, "end": 2.0, "text": "yo"}],
        "signal_peaks": [{"t": 1.0, "score": 0.9}], "language": "en"})
    assert cli.main(["run"]) == 1


def test_cmd_run_pause_exits_0(tmp_path, monkeypatch):
    from fanops import cli
    from fanops.pipeline_run import set_paused
    cfg = _ready_run_tree(tmp_path, monkeypatch)
    set_paused(cfg, True)
    assert cli.main(["run"]) == 0


def test_cmd_run_converged_exits_0(tmp_path, monkeypatch):
    from fanops import cli
    _ready_run_tree(tmp_path, monkeypatch)
    assert cli.main(["run"]) == 0
