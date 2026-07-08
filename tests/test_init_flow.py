"""MOL-303: fanops init thin orchestrator."""
from fanops.config import Config
from fanops.init_flow import run_init, write_context_template


def test_init_writes_context_template(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "MohFlow-FanOps" / "00_control").mkdir(parents=True)
    cfg = Config(root=tmp_path)
    assert write_context_template(cfg) is True
    assert cfg.context_path.exists() and cfg.context_path.read_text().strip()


def test_run_init_reports_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "MohFlow-FanOps" / "00_control").mkdir(parents=True)
    cfg = Config(root=tmp_path)
    res = run_init(cfg)
    assert res["state"] in ("NOT_CONFIGURED", "CONFIGURED")
    assert res["next"]
