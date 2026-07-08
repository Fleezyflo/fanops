"""MOL-300: daemon emits dep-health event on run post-loop."""
import json
from fanops.config import Config


def test_run_emits_dep_health_event(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "MohFlow-FanOps" / "00_control").mkdir(parents=True)
    (tmp_path / "MohFlow-FanOps" / "00_control" / "accounts.json").write_text(
        '{"accounts":[{"handle":"a","platforms":["instagram"],"status":"active","account_id":"1"}]}')
    cfg = Config(root=tmp_path)
    from fanops.cli import _dep_health_event
    _dep_health_event(cfg)
    lines = [json.loads(ln) for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    assert any("dep_health" in ln for ln in lines)
    evt = next(ln for ln in lines if "dep_health" in ln)
    assert "deps" in evt and isinstance(evt["deps"], list)
