"""MOL-353: KeepAlive plist shape + resident --loop direct exec (replaces StartInterval one-shot)."""
from __future__ import annotations
import os, plistlib

from fanops.config import Config
from fanops import daemon


def test_render_plist_keepalive_no_start_interval(tmp_path):
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    assert pl["KeepAlive"] == {"SuccessfulExit": False}
    assert "StartInterval" not in pl
    assert pl["RunAtLoad"] is True
    assert pl["Label"] == daemon.LABEL == "com.fanops.run"
    assert pl["ProgramArguments"] == [daemon._fanops_bin(), "run", "--loop", "--interval", "600"]
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


def test_daemon_path_prefers_stable_local_bin_over_which_pin(tmp_path, monkeypatch):
    # 2026-07-12 incident: the plist PATH baked the nvm dir which() saw at install time; that dir's
    # claude (2.0.30) predates --json-schema and every gate call failed — and the keeper re-derived
    # the SAME stale pin from its own baked PATH forever. ~/.local/bin/claude (the native-install
    # symlink) tracks the operator's CURRENT claude and its existence check is PATH-independent, so
    # it must come BEFORE any which()-derived parent.
    monkeypatch.setenv("HOME", str(tmp_path))
    stable = tmp_path / ".local" / "bin"; stable.mkdir(parents=True)
    (stable / "claude").write_text("#!/bin/sh\n")
    stale = tmp_path / "nvm" / "v18" / "bin"; stale.mkdir(parents=True)
    (stale / "claude").write_text("#!/bin/sh\n")
    monkeypatch.setattr(daemon.shutil, "which", lambda b: str(stale / b) if b == "claude" else None)
    parts = daemon._daemon_path().split(":")
    assert str(stable) in parts and str(stale) in parts
    assert parts.index(str(stable)) < parts.index(str(stale))


def test_daemon_path_without_stable_claude_is_unchanged(tmp_path, monkeypatch):
    # no ~/.local/bin/claude -> the dir is NOT added (no speculative PATH entries).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.shutil, "which", lambda b: None)
    assert str(tmp_path / ".local" / "bin") not in daemon._daemon_path().split(":")


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

    assert not (cfg.control / "fanops-run.sh").exists()
    assert daemon.plist_path().exists()
    pl = plistlib.loads(daemon.plist_path().read_bytes())
    assert pl["KeepAlive"] == {"SuccessfulExit": False}
    assert "StartInterval" not in pl
    assert pl["WorkingDirectory"] == str(tmp_path)
    assert pl["ProgramArguments"][0] == daemon._fanops_bin()
    assert daemon.installed_interval(cfg) == 600
    uid = os.getuid()
    assert ["launchctl", "bootout", f"gui/{uid}/{daemon.LABEL}"] in calls
    assert ["launchctl", "bootstrap", f"gui/{uid}", str(daemon.plist_path())] in calls
    assert res["loaded"] is True
