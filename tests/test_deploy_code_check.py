# OPS-SHA: a loaded pump fails the deploy gate when the running SHA is missing.
# Equal SHAs stay a pass. The reader is injected so this never calls launchctl.
# daemon.ensure uses the same miss-or-mismatch rule. These tests fake launchctl;
# they do not kickstart a real agent.
import os
import subprocess

from fanops.config import Config
from fanops import daemon, doctor


def _loaded(_cfg, _interval):
    return {"loaded": True}


def test_deploy_code_check_fails_when_running_sha_missing(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    disk = "bbb222cafef00ddeadbeef"
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda c: None)
    monkeypatch.setattr(daemon, "_version_signal", lambda c: (disk, "git-head"))
    row = doctor._deploy_code_check(cfg, daemon_status=_loaded)
    assert row is not None and row["ok"] is False
    assert "heartbeat SHA" in row["hint"] and "missing" in row["hint"]
    assert disk not in row["hint"]


def test_deploy_code_check_passes_when_shas_equal(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    sha = "abc123def4567890"
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda c: sha)
    monkeypatch.setattr(daemon, "_version_signal", lambda c: (sha, "git-head"))
    row = doctor._deploy_code_check(cfg, daemon_status=_loaded)
    assert row is not None and row["ok"] is True and row["hint"] == ""


def _fake_launchctl(**spec):
    calls: list[list[str]] = []

    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "print" and len(cmd) > 2:
            rc, out = spec.get(cmd[2], spec.get("print", (0, "")))
        else:
            rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")

    run.calls = calls
    return run


def _kickstart_argv(uid):
    return ["launchctl", "kickstart", "-k", f"gui/{uid}/{daemon.LABEL}"]


def _ensure_env(monkeypatch, tmp_path, *, loaded: bool):
    """Pump print rc follows `loaded`. No plist, so an unloaded pump stays unloaded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon, "_require_darwin", lambda: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    fake = _fake_launchctl(**{main_print: (0 if loaded else 1, "")})
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    monkeypatch.setattr(daemon, "_pump_pid_age_s", lambda: (None, None))
    monkeypatch.setattr(daemon, "_sync_locked_deps", lambda: (True, ""))
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: None)
    return Config(root=tmp_path), fake, uid


def test_ensure_kickstarts_loaded_daemon_when_running_sha_missing(tmp_path, monkeypatch):
    """Loaded pump, heartbeat SHA missing: miss is a mismatch, same rule as _deploy_code_check."""
    cfg, fake, uid = _ensure_env(monkeypatch, tmp_path, loaded=True)
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda c: None)
    monkeypatch.setattr(daemon, "_version_signal", lambda c: ("bbb222cafef00d", "git-head"))

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in fake.calls
    assert res["loaded"] is True
    assert res["action"] == "kickstart_stale_code"


def test_ensure_skips_kickstart_when_unloaded_and_sha_missing(tmp_path, monkeypatch):
    """Unloaded pump is N/A for the deploy gate. A missing SHA does not kickstart it."""
    cfg, fake, uid = _ensure_env(monkeypatch, tmp_path, loaded=False)
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda c: None)
    monkeypatch.setattr(daemon, "_version_signal", lambda c: ("bbb222cafef00d", "git-head"))

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in fake.calls
    assert res["loaded"] is False
    assert res["action"] == "none"
