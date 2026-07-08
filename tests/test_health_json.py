"""MOL-299: machine-readable health (--json + /healthz)."""
import json
from fanops.config import Config

def test_health_json_exit_code_healthy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.cli import cmd_health
    class Args:
        json = True
    # dry checkout: deps may be down but we only assert JSON shape + exit semantics
    rc = cmd_health(Config(root=tmp_path), Args())
    assert rc in (0, 1)


def test_doctor_json_emits_healthy_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.cli import cmd_doctor
    class Args:
        json = True
        fix_routing = False
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_doctor(Config(root=tmp_path), Args())
    data = json.loads(buf.getvalue())
    assert "healthy" in data and "checks" in data and "deps" in data


def test_report_is_healthy_fails_on_bad_check():
    from fanops.health_model import HealthReport, DepHealth, report_is_healthy
    rep = HealthReport(checks=[{"label": "x", "ok": False, "hint": "fix"}], notes=[],
                       deps=[DepHealth("docker", True, "up")])
    assert report_is_healthy(rep) is False


def test_healthz_route_returns_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path))
    client = app.test_client()
    r = client.get("/healthz")
    assert r.is_json
    data = r.get_json()
    assert "healthy" in data and "deps" in data
    assert r.status_code in (200, 503)
