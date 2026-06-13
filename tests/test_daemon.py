"""Tests for `fanops daemon` (durable unattended run via launchd). The renderers are pure and
asserted by parsing the plist back with plistlib (robust to formatting). The side-effecting
install/status/stop are tested with `subprocess.run` mocked — NO real launchctl, NO real
~/Library/LaunchAgents write (HOME is repointed at tmp_path so every home-derived path is
sandboxed). The heartbeat parse is pinned against the REAL run.log line shape (log.py)."""
from __future__ import annotations
import os, plistlib, subprocess
from datetime import datetime, timedelta, timezone

import pytest

from fanops.config import Config
from fanops.errors import ToolchainMissingError
from fanops import daemon


def _heartbeat_line(ts: str) -> str:
    # EXACT shape get_logger writes for _heartbeat: <iso>\theartbeat\t-\tok\t<kv...> (log.py:14).
    return f"{ts}\theartbeat\t-\tok\theartbeat={ts} fanops_version=0.3.0 published_in_run=0\n"


def _fake_launchctl(**spec):
    """Recorder for subprocess.run. `spec` maps the launchctl sub-verb (cmd[1]) to (rc, stdout);
    unmapped verbs default to (0, ""). Records every cmd list in `.calls`."""
    calls: list[list[str]] = []
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    run.calls = calls
    return run


# ── Task 1: pure renderers + path helpers ───────────────────────────────────────────────────

def test_render_plist_pins_working_dir_to_root_not_base(tmp_path):
    # THE core gotcha: launchd's default cwd is /, and Config(root=cwd) re-derives base=root/MohFlow-FanOps.
    # WorkingDirectory MUST be root; point it at base and you build .../MohFlow-FanOps/MohFlow-FanOps.
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    assert pl["WorkingDirectory"] == str(tmp_path)                 # root, NOT cfg.base
    assert pl["WorkingDirectory"] != str(cfg.base)


def test_render_plist_sets_label_interval_runatload_and_program(tmp_path):
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    assert pl["Label"] == daemon.LABEL == "com.fanops.run"
    assert pl["StartInterval"] == 600
    assert pl["RunAtLoad"] is True
    assert pl["ProgramArguments"] == ["/bin/bash", str(daemon.wrapper_path(cfg))]
    assert pl["StandardOutPath"] == str(cfg.reports / "daemon.out")
    assert pl["StandardErrorPath"] == str(cfg.reports / "daemon.err")
    # launchd sources NO shell profile — PATH+HOME must be baked into EnvironmentVariables.
    assert "PATH" in pl["EnvironmentVariables"] and "HOME" in pl["EnvironmentVariables"]


def test_render_wrapper_uses_venv_fanops_cd_root_now_base_time_and_responder(tmp_path):
    cfg = Config(root=tmp_path)
    w = daemon.render_wrapper(cfg, responder="llm", interval=600)
    assert w.startswith("#!/bin/bash")
    assert daemon._fanops_bin() in w                              # the SAME venv that installed it
    assert f'cd "{cfg.root}"' in w                                # not base, not /
    assert '--base-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in w    # a FRESH now each fire, not a frozen past date
    assert 'FANOPS_RESPONDER="llm"' in w
    assert "export PATH=" in w


def test_path_helpers_live_in_expected_locations(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(root=tmp_path)
    assert daemon.plist_path() == tmp_path / "Library/LaunchAgents/com.fanops.run.plist"
    assert daemon.wrapper_path(cfg) == cfg.control / "fanops-run.sh"


# ── parse_interval (pure; Task 5 helper, lives in daemon for testability) ────────────────────

@pytest.mark.parametrize("raw,secs", [("10m", 600), ("90s", 90), ("2h", 7200), ("600", 600), ("60s", 60)])
def test_parse_interval_units(raw, secs):
    assert daemon.parse_interval(raw) == secs


@pytest.mark.parametrize("raw", ["45", "30s", "0", "59s", "-5m"])
def test_parse_interval_rejects_sub_minute(raw):
    # launchd ThrottleInterval floors restart cadence; reject < 60s with a clean ValueError, not a silent clamp.
    with pytest.raises(ValueError):
        daemon.parse_interval(raw)


# ── Task 2: install (side-effecting, mocked launchctl) ───────────────────────────────────────

def test_install_writes_files_and_bootstraps(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    fake = _fake_launchctl(bootout=(1, ""), bootstrap=(0, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600, responder="llm")

    assert daemon.wrapper_path(cfg).exists() and os.access(daemon.wrapper_path(cfg), os.X_OK)
    assert daemon.plist_path().exists()
    pl = plistlib.loads(daemon.plist_path().read_bytes())
    assert pl["WorkingDirectory"] == str(tmp_path)                # the gotcha, on the file actually written
    assert daemon.installed_interval(cfg) == 600                  # status reads cadence back from the written plist
    uid = os.getuid()
    assert ["launchctl", "bootstrap", f"gui/{uid}", str(daemon.plist_path())] in fake.calls
    assert res["loaded"] is True


def test_install_falls_back_to_load_when_bootstrap_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    fake = _fake_launchctl(bootout=(1, ""), bootstrap=(1, ""), load=(0, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600, responder="llm")

    assert ["launchctl", "load", "-w", str(daemon.plist_path())] in fake.calls
    assert res["loaded"] is True


def test_install_raises_on_non_darwin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "linux")
    cfg = Config(root=tmp_path)
    with pytest.raises(RuntimeError, match="macOS"):
        daemon.install(cfg, interval=600, responder="llm")


def test_install_raises_clean_when_launchctl_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    def absent(cmd, *a, **k):
        raise FileNotFoundError("launchctl")
    monkeypatch.setattr(daemon.subprocess, "run", absent)
    cfg = Config(root=tmp_path)
    with pytest.raises(ToolchainMissingError, match="launchctl"):
        daemon.install(cfg, interval=600, responder="llm")


# ── Task 3: status (read-only liveness) ──────────────────────────────────────────────────────

def test_status_alive_on_fresh_heartbeat(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    cfg.log_path.write_text(_heartbeat_line(now))
    fake = _fake_launchctl(list=(0, '\t"PID" = 4321;\n\t"LastExitStatus" = 0;\n'))
    monkeypatch.setattr(daemon.subprocess, "run", fake)

    rep = daemon.status(cfg, interval=600)

    assert rep["loaded"] is True
    assert rep["pid"] == 4321
    assert rep["verdict"] == "alive"


def test_status_stale_on_old_heartbeat(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()    # 3600s >> 3*600
    cfg.log_path.write_text(_heartbeat_line(old))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 7;\n')))

    rep = daemon.status(cfg, interval=600)

    assert rep["loaded"] is True
    assert "stale" in rep["verdict"]


def test_status_not_installed_when_list_fails(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(1, "")))
    rep = daemon.status(cfg, interval=600)
    assert rep["loaded"] is False
    assert "not installed" in rep["verdict"]


def test_status_no_heartbeat_handles_empty_log(tmp_path, monkeypatch):
    # No run.log at all -> age None, no raise (the daemon may be loaded but never fired yet).
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 9;\n')))
    rep = daemon.status(cfg, interval=600)
    assert rep["heartbeat_age_s"] is None


# ── Task 4: stop + tail_logs ─────────────────────────────────────────────────────────────────

def test_stop_boots_out_label(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    fake = _fake_launchctl(bootout=(0, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)

    daemon.stop(cfg)

    uid = os.getuid()
    assert ["launchctl", "bootout", f"gui/{uid}/{daemon.LABEL}"] in fake.calls


def test_stop_idempotent_when_not_loaded(tmp_path, monkeypatch):
    # Booting out an already-stopped label returns rc != 0 — that is "already stopped", not an error.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), unload=(1, "")))
    cfg = Config(root=tmp_path)
    res = daemon.stop(cfg)                                         # must not raise
    assert res["stopped"] is True


def test_tail_logs_returns_last_n_lines(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    cfg.log_path.write_text("".join(f"line{i}\n" for i in range(100)))
    out = daemon.tail_logs(cfg, n=5)
    assert "line99" in out and "line95" in out and "line94" not in out


def test_tail_logs_missing_file_message(tmp_path):
    cfg = Config(root=tmp_path)
    assert "no logs yet" in daemon.tail_logs(cfg, n=40)


# ── Task 5: CLI wiring (main(["daemon", ...]) idiom) ─────────────────────────────────────────

def test_main_daemon_status_returns_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(1, "")))   # not loaded, host-independent
    from fanops.cli import main
    assert main(["daemon", "status"]) == 0
    assert "not installed" in capsys.readouterr().out


def test_main_daemon_install_rejects_sub_minute_interval(tmp_path, monkeypatch, capsys):
    # The bad interval is parsed BEFORE install touches launchctl/disk, so this is host-independent:
    # no launchctl call, no plist write — a clean exit 2 with the reason, never a traceback.
    monkeypatch.chdir(tmp_path)
    from fanops.cli import main
    assert main(["daemon", "install", "--interval", "30s"]) == 2
    assert "interval" in capsys.readouterr().err
    assert not daemon.wrapper_path(Config(root=tmp_path)).exists()                  # nothing written on rejection


def test_main_daemon_logs_returns_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.cli import main
    assert main(["daemon", "logs"]) == 0
    assert "no logs yet" in capsys.readouterr().out
