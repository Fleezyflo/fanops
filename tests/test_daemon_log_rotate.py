"""Keeper copytruncate of launchd daemon.out / daemon.err / studio.err.

Truncation is allowed only when that process's fd for the path is O_APPEND. studio.err is
the Studio resident's fd 2, not the pump's. The suite never opens the live logs and never
calls launchctl.
"""
from __future__ import annotations
import fcntl
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


def test_crash_loop_tail_survives_rotation_and_the_next_write_starts_the_file(tmp_path):
    cfg = _cfg(tmp_path)
    err = cfg.reports / "daemon.err"
    refusal = b"preflight: refusing to run\n  - `grok` is not on PATH\n"
    writer = os.open(err, os.O_WRONLY | os.O_CREAT | os.O_APPEND)   # how launchd holds StandardErrorPath
    try:
        os.write(writer, b"older output\n" * 64 + refusal)
        before = err.read_bytes()
        rotated = daemon.rotate_launchd_logs(cfg, pid=4, minimum_bytes=len(before),
                                             fd_is_append=lambda *a: True)
        assert rotated == ["daemon.err"]
        archived = (cfg.reports / "daemon.err.1").read_bytes()
        assert archived == before
        assert archived.endswith(refusal)
        os.write(writer, refusal)                                   # the next crash, same descriptor
        assert err.read_bytes() == refusal                          # offset 0: no hole before it
    finally:
        os.close(writer)


def test_second_rotator_leaves_the_log_to_the_flock_holder(tmp_path):
    cfg = _cfg(tmp_path)
    err = cfg.reports / "daemon.err"
    err.write_bytes(b"keep-me")
    in_flight = cfg.reports / "daemon.err.1.tmp"
    in_flight.write_bytes(b"keep")
    held = os.open(err, os.O_RDONLY)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert daemon.rotate_launchd_logs(cfg, pid=4, minimum_bytes=1, fd_is_append=lambda *a: True) == []
        assert err.read_bytes() == b"keep-me"
        assert in_flight.read_bytes() == b"keep"
        assert not (cfg.reports / "daemon.err.1").exists()
    finally:
        os.close(held)
    assert daemon.rotate_launchd_logs(cfg, pid=4, minimum_bytes=1, fd_is_append=lambda *a: True) == ["daemon.err"]
    assert (cfg.reports / "daemon.err.1").read_bytes() == b"keep-me"
    assert err.read_bytes() == b""
    assert not in_flight.exists()


def test_symlink_log_is_not_truncated(tmp_path):
    cfg = _cfg(tmp_path)
    target = tmp_path / "real-log"
    target.write_bytes(b"do-not-touch")
    (cfg.reports / "daemon.err").symlink_to(target)

    def probe(*args):
        raise AssertionError("symlink must not be probed")

    assert daemon.rotate_launchd_logs(cfg, pid=4, minimum_bytes=1, fd_is_append=probe) == []
    assert target.read_bytes() == b"do-not-touch"


def test_studio_err_rotates_on_its_own_pid_when_append_proven(tmp_path):
    cfg = _cfg(tmp_path)
    err = cfg.reports / "studio.err"
    pump = cfg.reports / "daemon.err"
    err.write_bytes(b"STUDIO-BEFORE\n")
    pump.write_bytes(b"tiny")
    seen = []

    def probe(pid, fd, log_path):
        seen.append((pid, fd, log_path.name))
        return pid == 77 and fd == 2 and log_path.name == "studio.err"

    rotated = daemon.rotate_launchd_logs(cfg, pid=4, studio_pid=77, minimum_bytes=8, fd_is_append=probe)
    assert rotated == ["studio.err"]
    assert seen == [(77, 2, "studio.err")]
    assert err.read_bytes() == b""
    assert (cfg.reports / "studio.err.1").read_bytes() == b"STUDIO-BEFORE\n"
    assert pump.read_bytes() == b"tiny"


def test_studio_err_does_not_rotate_on_the_pump_pid(tmp_path):
    cfg = _cfg(tmp_path)
    err = cfg.reports / "studio.err"
    err.write_bytes(b"keep-studio")
    seen = []

    def probe(pid, fd, log_path):
        seen.append(pid)
        return pid == 4 and fd == 2

    assert daemon.rotate_launchd_logs(cfg, pid=4, studio_pid=None, minimum_bytes=1,
                                      fd_is_append=probe) == []
    assert seen == []
    assert err.read_bytes() == b"keep-studio"
    assert not (cfg.reports / "studio.err.1").exists()


def test_studio_err_without_append_stays(tmp_path):
    cfg = _cfg(tmp_path)
    err = cfg.reports / "studio.err"
    err.write_bytes(b"keep-studio")
    seen = []

    def probe(pid, fd, log_path):
        seen.append((pid, fd, log_path.name))
        return False

    assert daemon.rotate_launchd_logs(cfg, pid=4, studio_pid=77, minimum_bytes=1, fd_is_append=probe) == []
    assert seen == [(77, 2, "studio.err")]
    assert err.read_bytes() == b"keep-studio"


def test_second_rotator_leaves_studio_err_to_the_flock_holder(tmp_path):
    cfg = _cfg(tmp_path)
    err = cfg.reports / "studio.err"
    err.write_bytes(b"keep-studio")
    held = os.open(err, os.O_RDONLY)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert daemon.rotate_launchd_logs(cfg, pid=4, studio_pid=77, minimum_bytes=1,
                                          fd_is_append=lambda *a: True) == []
        assert err.read_bytes() == b"keep-studio"
        assert not (cfg.reports / "studio.err.1").exists()
    finally:
        os.close(held)
    assert daemon.rotate_launchd_logs(cfg, pid=4, studio_pid=77, minimum_bytes=1,
                                      fd_is_append=lambda *a: True) == ["studio.err"]
    assert (cfg.reports / "studio.err.1").read_bytes() == b"keep-studio"
    assert err.read_bytes() == b""


def test_ensure_continues_when_rotate_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    attempts = []

    def boom(cfg, **kwargs):
        attempts.append(cfg)
        raise OSError("full")

    monkeypatch.setattr(daemon, "rotate_launchd_logs", boom)
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda _c: "same")     # pump is on disk code:
    monkeypatch.setattr(daemon, "_version_signal", lambda _c: ("same", "git-head"))   # nothing to adopt
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(**{main_print: (0, "")}))
    res = daemon.ensure(Config(root=tmp_path))
    assert len(attempts) == 1
    assert res == {"label": daemon.LABEL, "loaded": True, "action": "none"}


def test_ensure_rotates_an_over_cap_log_and_decides_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    cfg = _cfg(tmp_path)
    err = cfg.reports / "daemon.err"
    err.write_bytes(b"ERR-BEFORE\n")
    probed = []

    def probe(pid, fd, log_path):
        probed.append((pid, fd, log_path.name))
        return True

    monkeypatch.setattr(daemon, "LAUNCHD_LOG_ROTATE_BYTES", 8)
    monkeypatch.setattr(daemon, "launchd_fd_is_append", probe)
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda _c: "same")
    monkeypatch.setattr(daemon, "_version_signal", lambda _c: ("same", "git-head"))
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    fake = _fake_launchctl(**{main_print: (0, ""), "list": (0, '"PID" = 4321;')})
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    res = daemon.ensure(cfg)
    assert probed == [(4321, 2, "daemon.err")]
    assert (cfg.reports / "daemon.err.1").read_bytes() == b"ERR-BEFORE\n"
    assert err.read_bytes() == b""
    assert res == {"label": daemon.LABEL, "loaded": True, "action": "none"}
    assert not any(c[1] in ("kickstart", "bootstrap", "bootout") for c in fake.calls)
