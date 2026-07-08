"""MOL-298: unified health model — one owner, thin views."""
import json
from fanops.config import Config
from fanops.health_model import HealthReport, build_health_report, dep_health_list, postiz_dep_health
from fanops import health


def test_health_report_composes_checks_deps_and_field_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("POSTIZ_URL=http://localhost:4007/api\nPOSTIZ_API_KEY=k\nFANOPS_POSTER=postiz\n")
    cfg = Config(root=tmp_path)
    rep = build_health_report(cfg, postiz_probe=lambda c: type("H", (), {"healthy": True, "status_code": 200, "hint": ""})())
    assert isinstance(rep, HealthReport)
    assert rep.checks and rep.notes
    assert [d.name for d in rep.deps] == ["docker", "postiz", "zernio"]
    assert rep.field_shape is not None
    assert rep.field_shape["verdict"] == "NO-DATA"


def test_system_health_matches_dep_health_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(health, "dep_health_list", lambda c, **kw: dep_health_list(c))
    assert health.system_health(cfg) == dep_health_list(cfg)


def test_postiz_health_uses_unified_probe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    seen = []
    def probe(c):
        seen.append(1)
        return type("H", (), {"healthy": True, "status_code": 200, "hint": ""})()
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007/api")
    cfg2 = Config(root=tmp_path)
    h = postiz_dep_health(cfg2, probe=probe)
    assert seen and h.ok is True


def test_doctor_report_includes_deps_key(tmp_path, monkeypatch):
    from fanops.doctor import doctor_report
    monkeypatch.chdir(tmp_path)
    rep = doctor_report(Config(root=tmp_path))
    assert "checks" in rep and "notes" in rep
    assert "deps" in rep
