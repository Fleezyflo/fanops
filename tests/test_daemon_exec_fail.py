"""MOL-468: pre-Python exec failure — wrapper writes marker, daemon.status surfaces interpreter alarm."""
from __future__ import annotations
import plistlib
import subprocess
from datetime import datetime, timezone

from fanops.config import Config
from fanops import daemon, doctor


def _fake_launchctl(**spec):
    def run(cmd, *a, **k):
        verb = cmd[1] if len(cmd) > 1 else ""
        rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    return run


def test_render_wrapper_checks_executable_and_writes_marker(tmp_path):
    cfg = Config(root=tmp_path)
    w = daemon.render_wrapper(cfg, interval=600)
    assert "EXEC_FAIL_MARKER=" in w
    assert 'if [ ! -x "$FANOPS_BIN" ]; then' in w
    assert "interpreter_not_executable" in w
    assert 'rm -f "$EXEC_FAIL_MARKER"' in w
    assert "run --loop --interval" in w


def test_status_surfaces_exec_fail_marker_when_loaded(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    target = str(tmp_path / ".venv" / "bin" / "fanops")
    daemon.write_exec_fail_marker(cfg, target=target, reason="interpreter_not_executable")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = -1;\n\t"LastExitStatus" = 127;\n')))

    rep = daemon.status(cfg, interval=600)

    assert rep["loaded"] is True
    assert rep["exec_fail"]["target"] == target
    assert "interpreter not executable" in rep["verdict"]


def test_status_alive_unchanged_when_marker_absent(tmp_path, monkeypatch):
    import json as _json
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    rec = {"ts": now, "level": "info", "stage": "heartbeat", "unit_id": "-", "outcome": "ok",
           "heartbeat": now, "fanops_version": "0.3.0", "published_in_run": "0"}
    cfg.log_path.write_text(_json.dumps(rec, separators=(",", ":")) + "\n")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 1;\n')))

    rep = daemon.status(cfg, interval=600)

    assert rep["verdict"] == "alive"
    assert rep.get("exec_fail") is None


def test_doctor_daemon_check_names_exec_fail_target(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    target = "/broken/.venv/bin/fanops"
    daemon.write_exec_fail_marker(cfg, target=target)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"LastExitStatus" = 127;\n')))

    rep = doctor.doctor_report(cfg)
    chk = next(c for c in rep["checks"] if "publish daemon alive" in c["label"])

    assert chk["ok"] is False
    assert target in chk["hint"]


def test_studio_daemon_health_surfaces_exec_fail(tmp_path, monkeypatch):
    from fanops.studio.app import create_app

    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    daemon.write_exec_fail_marker(cfg, target="/missing/fanops")
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({"Label": daemon.LABEL}))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = -1;\n')))
    app = create_app(cfg)
    with app.test_client() as client:
        html = client.get("/home/daemon-health").data.decode()
    assert "data-daemon-warn" in html
    assert "interpreter not executable" in html
