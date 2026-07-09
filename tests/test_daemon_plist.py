"""MOL-353: KeepAlive plist shape + resident --loop wrapper (replaces StartInterval one-shot)."""
from __future__ import annotations
import os, plistlib, shlex

from fanops.config import Config
from fanops import daemon


def test_render_plist_keepalive_no_start_interval(tmp_path):
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    assert pl["KeepAlive"] == {"SuccessfulExit": False}
    assert "StartInterval" not in pl
    assert pl["RunAtLoad"] is True
    assert pl["Label"] == daemon.LABEL == "com.fanops.run"
    assert pl["ProgramArguments"] == ["/bin/bash", str(daemon.wrapper_path(cfg))]
    assert pl["StandardOutPath"] == str(cfg.reports / "daemon.out")
    assert pl["StandardErrorPath"] == str(cfg.reports / "daemon.err")
    assert pl["EnvironmentVariables"]["FANOPS_DAEMON_INTERVAL"] == "600"
    assert "PATH" in pl["EnvironmentVariables"] and "HOME" in pl["EnvironmentVariables"]


def test_render_plist_pins_working_dir_to_root_not_base(tmp_path):
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    assert pl["WorkingDirectory"] == str(tmp_path)
    assert pl["WorkingDirectory"] != str(cfg.base)


def test_render_plist_prohibits_multiple_instances(tmp_path):
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    assert pl.get("LSMultipleInstancesProhibited") is True
    assert pl["RunAtLoad"] is True
    assert pl["ThrottleInterval"] == daemon._MIN_INTERVAL


def test_render_wrapper_uses_loop_interval(tmp_path):
    cfg = Config(root=tmp_path)
    w = daemon.render_wrapper(cfg, interval=600)
    assert w.startswith("#!/bin/bash")
    assert daemon._fanops_bin() in w
    assert f"cd {shlex.quote(str(cfg.root))}" in w
    assert "run --loop --interval" in w
    assert "--base-time" not in w
    assert "FANOPS_RESPONDER" not in w
    assert "export PATH=" in w


def test_render_wrapper_shell_quotes_paths_with_metacharacters(tmp_path):
    weird = tmp_path / 'a b"c$d'
    weird.mkdir()
    cfg = Config(root=weird)
    w = daemon.render_wrapper(cfg, interval=600)
    assert f"cd {shlex.quote(str(weird))}" in w
    assert f'cd "{weird}"' not in w


def test_installed_interval_reads_wrapper_cadence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    daemon.write_wrapper_atomic(daemon.wrapper_path(cfg), daemon.render_wrapper(cfg, interval=90))
    assert daemon.installed_interval(cfg) == 90


def test_installed_interval_falls_back_to_plist_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(root=tmp_path)
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({
        "Label": daemon.LABEL,
        "EnvironmentVariables": {"FANOPS_DAEMON_INTERVAL": "120"},
    }))
    assert daemon.installed_interval(cfg) == 120


def test_installed_interval_legacy_start_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(root=tmp_path)
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({"Label": daemon.LABEL, "StartInterval": 600}))
    assert daemon.installed_interval(cfg) == 600


def test_installed_interval_missing_or_corrupt_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(root=tmp_path)
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({"Label": daemon.LABEL}))
    assert daemon.installed_interval(cfg) is None
    pp.write_bytes(b"not a plist at all")
    assert daemon.installed_interval(cfg) is None


def test_install_bootstraps_idempotently(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    calls: list[list[str]] = []
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        rc = 1 if verb == "bootout" else 0
        import subprocess
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")
    monkeypatch.setattr(daemon.subprocess, "run", run)
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600, responder="inherit")

    assert daemon.wrapper_path(cfg).exists() and os.access(daemon.wrapper_path(cfg), os.X_OK)
    assert daemon.plist_path().exists()
    pl = plistlib.loads(daemon.plist_path().read_bytes())
    assert pl["KeepAlive"] == {"SuccessfulExit": False}
    assert "StartInterval" not in pl
    assert pl["WorkingDirectory"] == str(tmp_path)
    assert daemon.installed_interval(cfg) == 600
    uid = os.getuid()
    assert ["launchctl", "bootout", f"gui/{uid}/{daemon.LABEL}"] in calls
    assert ["launchctl", "bootstrap", f"gui/{uid}", str(daemon.plist_path())] in calls
    assert res["loaded"] is True
