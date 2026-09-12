"""Keeper-adopts-pump: the pump's in-process os.execv self-adopt is GONE. The resident `fanops run
--loop` records its running-HEAD SHA in every loop heartbeat (`_heartbeat(code=...)`), and the EXTERNAL
keeper (com.fanops.keeper, StartInterval 120s, `fanops daemon ensure`) compares that SHA to the SHA on
disk and kickstarts the PUMP when they drift.

These tests drive `daemon.ensure(cfg)` with only the launchctl/ps/git process edge stubbed. Decision
inputs are real: heartbeat lines on disk, real `_version_signal` via git pass-through, real run flock."""
from __future__ import annotations
import json
import os
import pathlib
import subprocess
import sys

from fanops.config import Config
from fanops import daemon
from fanops.pipeline_run import _lock_path
import fanops


def _fake_ensure_run(monkeypatch, tmp_path, *, list_out="", ps_etime=None):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    uid = os.getuid()
    calls: list[list[str]] = []
    real = subprocess.run

    def run(cmd, *a, **k):
        cmd = list(cmd)
        calls.append(cmd)
        if cmd[:1] == ["git"]:
            return real(cmd, *a, **k)
        if cmd[:1] == ["ps"]:
            out = "" if ps_etime is None else ps_etime
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if cmd[:1] == ["launchctl"]:
            verb = cmd[1] if len(cmd) > 1 else ""
            if verb == "list":
                return subprocess.CompletedProcess(cmd, 0, stdout=list_out, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(daemon.subprocess, "run", run)
    return Config(root=tmp_path), calls, uid


def _kickstart_argv(uid):
    return ["launchctl", "kickstart", "-k", f"gui/{uid}/{daemon.LABEL}"]


def _studio_kickstart_argv(uid):
    return ["launchctl", "kickstart", "-k", f"gui/{uid}/{daemon.STUDIO_LABEL}"]


def _loop_hb(cfg, code: str) -> None:
    cfg.reports.mkdir(parents=True, exist_ok=True)
    rec = {"ts": "2026-01-01T00:00:00+00:00", "level": "info", "stage": "heartbeat", "unit_id": "-",
           "outcome": "ok", "origin": "loop", "heartbeat": "2026-01-01T00:00:00+00:00",
           "fanops_version": "0.3.0", "published_in_run": "0", "code": code}
    cfg.log_path.write_text(json.dumps(rec, separators=(",", ":")) + "\n")


def _deployed_sha():
    code_root = pathlib.Path(fanops.__file__).resolve().parent
    r = subprocess.run(["git", "-C", str(code_root), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return (r.stdout or "").strip() or None


def _plant_studio_plist(tmp_path):
    p = tmp_path / "Library" / "LaunchAgents" / f"{daemon.STUDIO_LABEL}.plist"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("studio")
    return p


def test_kickstarts_pump_on_drift(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    _loop_hb(cfg, "aaa")
    _plant_studio_plist(tmp_path)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in calls
    assert res["action"] == "kickstart_stale_code"
    assert _studio_kickstart_argv(uid) in calls


def test_no_kickstart_when_shas_match(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    sha = _deployed_sha()
    assert sha
    _loop_hb(cfg, sha)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in calls
    assert res["action"] == "none"


def test_no_kickstart_when_running_sha_absent(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in calls
    assert res["action"] == "none"


def test_storm_guard_holds(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(
        monkeypatch, tmp_path,
        list_out='\t"PID" = 4321;\n\t"LastExitStatus" = 0;\n',
        ps_etime="00:10\n")
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    _loop_hb(cfg, "aaa")
    _plant_studio_plist(tmp_path)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in calls
    assert any(c[:1] == ["ps"] for c in calls)
    assert _studio_kickstart_argv(uid) not in calls
    assert res["action"] == "none"


def test_storm_guard_lets_settled_pump_through(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(
        monkeypatch, tmp_path,
        list_out='\t"PID" = 4321;\n',
        ps_etime="27:46:39\n")
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    _loop_hb(cfg, "aaa")

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in calls
    assert res["action"] == "kickstart_stale_code"


def test_no_kickstart_while_run_flock_held(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(
        monkeypatch, tmp_path,
        list_out='\t"PID" = 4321;\n',
        ps_etime="27:46:39\n")
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    _loop_hb(cfg, "aaa")
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        res = daemon.ensure(cfg)
        assert _kickstart_argv(uid) not in calls
        assert res["action"] == "none"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_ensure_does_not_refresh_daemon_strip_snapshot(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "0")
    daemon.ensure(cfg)
    assert not cfg.daemon_strip_path.exists()
    assert _kickstart_argv(uid) not in calls


def test_kill_switch_blocks_drift_kickstart(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "0")
    _loop_hb(cfg, "aaa")

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in calls
    assert res["action"] == "none"


def test_version_signal_reads_head_from_code_tree_not_cfg_root(tmp_path):
    code_root = pathlib.Path(fanops.__file__).resolve().parent
    want = subprocess.run(["git", "-C", str(code_root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    sig, src = daemon._version_signal(Config(tmp_path))
    assert (sig, src) == (want, "git-head")
    assert not (tmp_path / ".git").exists()
