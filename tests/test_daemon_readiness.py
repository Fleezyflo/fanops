"""MOL-354: readiness alarm — plist on disk but launchctl not loaded is ALARM, not neutral off."""
from __future__ import annotations
import plistlib, subprocess
from datetime import datetime, timedelta, timezone

from fanops.config import Config
from fanops import daemon, doctor


def _heartbeat_line(ts: str) -> str:
    return f"{ts}\theartbeat\t-\tok\theartbeat={ts} fanops_version=0.3.0 published_in_run=0\n"


def _fake_launchctl(**spec):
    calls: list[list[str]] = []
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    run.calls = calls
    return run


def _write_plist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({"Label": daemon.LABEL, "EnvironmentVariables": {"FANOPS_DAEMON_INTERVAL": "600"}}))


def test_status_alarm_when_plist_on_disk_but_not_loaded(tmp_path, monkeypatch):
    _write_plist(tmp_path, monkeypatch)
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(1, "")))

    rep = daemon.status(cfg, interval=600)

    assert rep["installed"] is True
    assert rep["loaded"] is False
    assert rep["verdict"] == "installed but NOT loaded — should be running"


def test_status_not_installed_when_no_plist_and_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(1, "")))

    rep = daemon.status(cfg, interval=600)

    assert rep["installed"] is False
    assert rep["loaded"] is False
    assert rep["verdict"] == "not installed"


def test_status_alive_when_loaded_and_fresh_heartbeat(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    cfg.log_path.write_text(_heartbeat_line(now))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 1;\n')))

    rep = daemon.status(cfg, interval=600)

    assert rep["loaded"] is True
    assert rep["verdict"] == "alive"


def test_status_stale_when_loaded_and_old_heartbeat(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cfg.log_path.write_text(_heartbeat_line(old))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 1;\n')))

    rep = daemon.status(cfg, interval=600)

    assert rep["loaded"] is True
    assert "stale" in rep["verdict"]


def test_doctor_fails_on_unloaded_plist_alarm(tmp_path, monkeypatch):
    _write_plist(tmp_path, monkeypatch)
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(1, "")))

    rep = doctor.doctor_report(cfg)
    chk = next((c for c in rep["checks"] if "daemon" in c["label"].lower() or "pump" in c["label"].lower()), None)

    assert chk is not None and chk["ok"] is False
    assert "NOT loaded" in chk["hint"]


def test_studio_daemon_health_surfaces_unloaded_alarm(tmp_path, monkeypatch):
    from fanops.studio.app import create_app

    _write_plist(tmp_path, monkeypatch)
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(1, "")))
    app = create_app(cfg)
    with app.test_client() as client:
        html = client.get("/home/daemon-health").data.decode()
    assert "data-daemon-warn" in html
    assert "NOT loaded" in html
