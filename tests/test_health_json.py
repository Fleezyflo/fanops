"""MOL-299: machine-readable health (--json + /healthz)."""
import json
from fanops.config import Config

def test_health_json_exit_code_healthy(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    cfg.context_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.context_path.write_text("brand")
    mocker.patch("fanops.doctor.shutil.which", return_value="/bin/tool")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    mocker.patch("fanops.doctor._daemon_liveness_check",
                 return_value={"label": "daemon", "ok": True, "hint": ""})
    from fanops.cli import cmd_health
    class Args:
        json = True
    # B11: dryrun skips unconfigured deps — healthy exit 0, not ambiguous 0/1
    assert cmd_health(cfg, Args()) == 0


def test_health_dryrun_makes_no_http_requests(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    get_spy = mocker.patch("requests.get")
    from fanops.cli import cmd_health
    cmd_health(Config(root=tmp_path), None)
    get_spy.assert_not_called()


def test_doctor_text_and_json_exit_parity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fanops.cli import cmd_doctor
    cfg = Config(root=tmp_path)
    class JsonArgs:
        json = True
        fix_routing = False
    class TextArgs:
        json = False
        fix_routing = False
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_json = cmd_doctor(cfg, JsonArgs())
    rc_text = cmd_doctor(cfg, TextArgs())
    assert rc_text == rc_json


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
