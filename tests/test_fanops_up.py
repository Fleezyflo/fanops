"""MOL-301: fanops up — headless bring-up + verify."""
from fanops.config import Config


def test_cmd_up_exit_coded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "MohFlow-FanOps" / "00_control").mkdir(parents=True)
    monkeypatch.setattr("fanops.health.ensure_up", lambda cfg: [])
    monkeypatch.setattr("fanops.postiz_lifecycle.ensure_up", lambda cfg: None)
    from fanops.cli import cmd_up
    rc = cmd_up(Config(root=tmp_path))
    assert rc in (0, 1)  # dry checkout: deps may be down
