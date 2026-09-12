"""MOL-299: machine-readable health (--json + /healthz). Empty root is not a PASS."""
import json
from fanops.cli import main
from fanops.config import Config


def test_health_json_exit_code_unhealthy_on_empty_root(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["health", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["healthy"] is False
    assert any(not c.get("ok", True) for c in data["checks"])


def test_health_dryrun_makes_no_http_requests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []

    def fake_get(*a, **k):
        seen.append(a)
        raise AssertionError("health must not HTTP on unconfigured dryrun")

    monkeypatch.setattr("requests.get", fake_get)
    from fanops.cli import cmd_health
    cmd_health(Config(root=tmp_path), None)
    assert seen == []


def test_doctor_text_and_json_exit_parity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc_json = main(["doctor", "--json"])
    rc_text = main(["doctor"])
    assert rc_text == rc_json == 1


def test_doctor_json_emits_healthy_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["healthy"] is False
    assert "checks" in data and "deps" in data


def test_report_is_healthy_fails_on_bad_check():
    from fanops.health_model import HealthReport, DepHealth, report_is_healthy
    rep = HealthReport(checks=[{"label": "x", "ok": False, "hint": "fix"}], notes=[],
                       deps=[DepHealth("docker", True, "up")])
    assert report_is_healthy(rep) is False


def test_healthz_route_returns_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.studio.app import create_app
    app = create_app(Config(root=tmp_path))
    r = app.test_client().get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
