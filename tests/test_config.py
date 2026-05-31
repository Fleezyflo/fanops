# tests/test_config.py
from fanops.config import Config

def test_dirs(tmp_path):
    c = Config(root=tmp_path)
    assert c.inbox == tmp_path / "MohFlow-FanOps" / "01_inbox"
    assert c.agent_io == tmp_path / "MohFlow-FanOps" / "04_agent_io"
    assert c.ledger_path == tmp_path / "MohFlow-FanOps" / "00_control" / "ledger.json"
    assert c.reports == tmp_path / "MohFlow-FanOps" / "07_reports"

def test_poster_default_dryrun(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    assert Config(root=tmp_path).poster_backend == "dryrun"

def test_poster_env_and_key_trimmed(monkeypatch, tmp_path):
    monkeypatch.setenv("FANOPS_POSTER", "rest")
    monkeypatch.setenv("BLOTATO_API_KEY", "  abc123\n")   # surrounding ws only
    c = Config(root=tmp_path)
    assert c.poster_backend == "rest" and c.blotato_api_key == "abc123"

def test_budget_and_responder_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("FANOPS_ESCALATION_BUDGET_USD", raising=False)
    monkeypatch.delenv("FANOPS_RESPONDER", raising=False)
    c = Config(root=tmp_path)
    assert c.escalation_budget_usd == 0.0 and c.responder_mode == "manual"
