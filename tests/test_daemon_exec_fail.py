"""MOL-468: pre-Python exec failure — status probes plist ProgramArguments[0], not the live interpreter."""
from __future__ import annotations
import os
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


def test_status_surfaces_non_executable_plist_binary(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    stale = str(tmp_path / "old-venv" / "bin" / "fanops")
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({
        "Label": daemon.LABEL,
        "ProgramArguments": [stale, "run", "--loop", "--interval", "600"],
        "EnvironmentVariables": {"FANOPS_DAEMON_INTERVAL": "600"},
    }))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = -1;\n\t"LastExitStatus" = 127;\n')))
    real_access = os.access
    monkeypatch.setattr(daemon.os, "access", lambda path, mode: False if path == stale else real_access(path, mode))

    rep = daemon.status(cfg, interval=600)

    assert rep["loaded"] is True
    assert rep["exec_fail"]["target"] == stale
    assert stale in rep["verdict"]
    assert "interpreter not executable" in rep["verdict"]


def test_status_alive_unchanged_when_plist_binary_executable(tmp_path, monkeypatch):
    import json as _json
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    fb = tmp_path / "bin" / "fanops"
    fb.parent.mkdir(parents=True, exist_ok=True)
    fb.write_text("#!/bin/sh\n")
    fb.chmod(0o755)
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({
        "Label": daemon.LABEL,
        "ProgramArguments": [str(fb), "run", "--loop", "--interval", "600"],
        "EnvironmentVariables": {"FANOPS_DAEMON_INTERVAL": "600"},
    }))
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
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({
        "Label": daemon.LABEL,
        "ProgramArguments": [target, "run", "--loop", "--interval", "600"],
        "EnvironmentVariables": {"FANOPS_DAEMON_INTERVAL": "600"},
    }))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"LastExitStatus" = 127;\n')))
    monkeypatch.setattr(daemon.os, "access", lambda _path, _mode: False)

    rep = doctor.doctor_report(cfg)
    chk = next(c for c in rep["checks"] if "publish daemon alive" in c["label"])

    assert chk["ok"] is False
    assert target in chk["hint"]


def test_studio_daemon_health_surfaces_exec_fail(tmp_path, monkeypatch):
    from fanops.studio.app import create_app

    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    target = "/missing/fanops"
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({
        "Label": daemon.LABEL,
        "ProgramArguments": [target, "run", "--loop", "--interval", "600"],
        "EnvironmentVariables": {"FANOPS_DAEMON_INTERVAL": "600"},
    }))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = -1;\n')))
    monkeypatch.setattr(daemon.os, "access", lambda _path, _mode: False)
    app = create_app(cfg)
    with app.test_client() as client:
        html = client.get("/home/daemon-health").data.decode()
    assert "data-daemon-warn" in html
    assert "interpreter not executable" in html
