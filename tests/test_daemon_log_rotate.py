"""Keeper copytruncate of launchd daemon.out / daemon.err.

Truncation is allowed only when the pump fd for that path is O_APPEND. The suite never
opens the live logs and never calls launchctl.
"""
from __future__ import annotations
import os
import subprocess

from fanops.config import Config
from fanops import daemon


def _cfg(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True)
    return cfg


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


def test_same_file_append_requires_flag_and_inode(tmp_path):
    path = tmp_path / "daemon.err"
    path.write_bytes(b"abc")
    st = path.stat()
    assert daemon._same_file_append(os.O_APPEND, st.st_ino, st.st_dev, path) is True
    assert daemon._same_file_append(os.O_RDWR | os.O_APPEND, st.st_ino, st.st_dev, path) is True
    assert daemon._same_file_append(os.O_RDWR, st.st_ino, st.st_dev, path) is False
    assert daemon._same_file_append(os.O_APPEND, st.st_ino + 1, st.st_dev, path) is False
    assert daemon._same_file_append(os.O_APPEND, st.st_ino, st.st_dev + 1, path) is False


def test_launchd_fd_probe_is_false_off_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.sys, "platform", "linux")
    assert daemon.launchd_fd_is_append(1, 2, tmp_path / "daemon.err") is False


def test_under_cap_does_not_probe_or_rotate(tmp_path):
    cfg = _cfg(tmp_path)
    path = cfg.reports / "daemon.err"
    path.write_bytes(b"small")
    probed = []

    def probe(*args):
        probed.append(args)
        return True

    assert daemon.rotate_launchd_logs(cfg, pid=4, minimum_bytes=50, fd_is_append=probe) == []
    assert probed == []
    assert path.read_bytes() == b"small"
    assert not (cfg.reports / "daemon.err.1").exists()


def test_over_cap_without_append_leaves_the_live_file(tmp_path):
    cfg = _cfg(tmp_path)
    path = cfg.reports / "daemon.err"
    path.write_bytes(b"keep-me")
    seen = []

    def probe(pid, fd, log_path):
        seen.append(fd)
        return False

    assert daemon.rotate_launchd_logs(cfg, pid=4, minimum_bytes=1, fd_is_append=probe) == []
    assert seen == [2]
    assert path.read_bytes() == b"keep-me"
    assert not (cfg.reports / "daemon.err.1").exists()


def test_no_pid_does_not_probe(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    path = cfg.reports / "daemon.err"
    path.write_bytes(b"keep-me")
    monkeypatch.setattr(daemon, "_pump_pid_age_s", lambda: (None, None))
    probed = []

    def probe(*args):
        probed.append(args)
        return True

    assert daemon.rotate_launchd_logs(cfg, minimum_bytes=1, fd_is_append=probe) == []
    assert probed == []
    assert path.read_bytes() == b"keep-me"


def test_copytruncate_when_append_proven(tmp_path):
    cfg = _cfg(tmp_path)
    err = cfg.reports / "daemon.err"
    out = cfg.reports / "daemon.out"
    err.write_bytes(b"ERR-BEFORE\n")
    out.write_bytes(b"tiny")
    fds = []

    def probe(pid, fd, log_path):
        fds.append((fd, log_path.name))
        return fd == 2

    rotated = daemon.rotate_launchd_logs(cfg, pid=4, minimum_bytes=8, fd_is_append=probe)
    assert rotated == ["daemon.err"]
    assert fds == [(2, "daemon.err")]
    assert err.read_bytes() == b""
    assert (cfg.reports / "daemon.err.1").read_bytes() == b"ERR-BEFORE\n"
    assert out.read_bytes() == b"tiny"
    assert not (cfg.reports / "daemon.out.1").exists()


def test_symlink_log_is_not_truncated(tmp_path):
    cfg = _cfg(tmp_path)
    target = tmp_path / "real-log"
    target.write_bytes(b"do-not-touch")
    (cfg.reports / "daemon.err").symlink_to(target)

    def probe(*args):
        raise AssertionError("symlink must not be probed")

    assert daemon.rotate_launchd_logs(cfg, pid=4, minimum_bytes=1, fd_is_append=probe) == []
    assert target.read_bytes() == b"do-not-touch"


def test_ensure_continues_when_rotate_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")

    def boom(cfg, **kwargs):
        raise OSError("full")

    monkeypatch.setattr(daemon, "rotate_launchd_logs", boom)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(**{main_print: (0, "")}))
    res = daemon.ensure(Config(root=tmp_path))
    assert res == {"label": daemon.LABEL, "loaded": True, "action": "none"}
