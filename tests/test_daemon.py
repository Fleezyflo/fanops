"""Tests for `fanops daemon` (durable unattended run via launchd). The renderers are pure and
asserted by parsing the plist back with plistlib (robust to formatting). The side-effecting
install/status/stop are tested with `subprocess.run` mocked — NO real launchctl, NO real
~/Library/LaunchAgents write (HOME is repointed at tmp_path so every home-derived path is
sandboxed). The heartbeat parse is pinned against the REAL run.log line shape (log.py)."""
from __future__ import annotations
import json, os, plistlib, subprocess
from datetime import datetime, timedelta, timezone

import pytest

from fanops.config import Config
from fanops.errors import ToolchainMissingError
from fanops import daemon


def _heartbeat_line(ts: str) -> str:
    # EXACT shape get_logger writes for _heartbeat (JSON; daemon._heartbeat_age_s reads stage+ts+origin=loop).
    import json
    rec = {"ts": ts, "level": "info", "stage": "heartbeat", "unit_id": "-", "outcome": "ok", "origin": "loop",
           "heartbeat": ts, "fanops_version": "0.3.0", "published_in_run": "0"}
    return json.dumps(rec, separators=(",", ":")) + "\n"


def _fake_launchctl(**spec):
    """Recorder for subprocess.run. `spec` maps the launchctl sub-verb (cmd[1]) to (rc, stdout);
    unmapped verbs default to (0, ""). Records every cmd list in `.calls`."""
    calls: list[list[str]] = []
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "print" and len(cmd) > 2:
            key = cmd[2]                                          # gui/{uid}/{label}
            if key in spec:
                rc, out = spec[key]
            else:
                rc, out = spec.get("print", (0, ""))
        else:
            rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    run.calls = calls
    return run


# ── Task 1: pure renderers + path helpers (plist/wrapper specifics → test_daemon_plist.py) ───

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

    assert daemon.plist_path().exists()
    pl = plistlib.loads(daemon.plist_path().read_bytes())
    assert pl["WorkingDirectory"] == str(tmp_path)                # the gotcha, on the file actually written
    assert pl["ProgramArguments"][0] == daemon._fanops_bin()
    assert daemon.installed_interval(cfg) == 600                  # status reads cadence back from the written plist
    uid = os.getuid()
    assert ["launchctl", "bootstrap", f"gui/{uid}", str(daemon.plist_path())] in fake.calls
    assert res["loaded"] is True


def test_install_falls_back_to_load_when_bootstrap_never_confirms(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    main_print_n = [0]
    def run(cmd, *a, **k):
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "print" and len(cmd) > 2 and cmd[2] == main_print:
            main_print_n[0] += 1
            rc = 0 if main_print_n[0] >= 4 else 1          # fail 3 bootstrap confirms; succeed after load -w
        else:
            rc = 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")
    run.calls = []
    def tracked(cmd, *a, **k):
        run.calls.append(list(cmd)); return run(cmd, *a, **k)
    tracked.calls = run.calls
    monkeypatch.setattr(daemon.subprocess, "run", tracked)
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600, responder="llm")

    assert ["launchctl", "load", "-w", str(daemon.plist_path())] in tracked.calls
    assert res["loaded"] is True


def test_install_loaded_false_when_print_fails_despite_bootstrap_rc0(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    uid = os.getuid()
    fail = f"gui/{uid}/{daemon.LABEL}"
    fake = _fake_launchctl(bootout=(1, ""), bootstrap=(0, ""), load=(0, ""), print=(1, ""), **{fail: (1, "")})
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600, responder="inherit")

    assert res["loaded"] is False


def test_install_retries_bootstrap_until_print_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    sleeps: list[float] = []
    monkeypatch.setattr(daemon.time, "sleep", lambda s: sleeps.append(s))
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    main_tries = [0]
    def run(cmd, *a, **k):
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "print":
            key = cmd[2] if len(cmd) > 2 else ""
            if key == main_print:
                main_tries[0] += 1
                rc = 0 if main_tries[0] >= 3 else 1
            else:
                rc = 0
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    run.calls = []
    orig = run
    def tracked(cmd, *a, **k):
        run.calls.append(list(cmd)); return orig(cmd, *a, **k)
    tracked.calls = run.calls
    monkeypatch.setattr(daemon.subprocess, "run", tracked)
    cfg = Config(root=tmp_path)

    res = daemon.install(cfg, interval=600, responder="inherit")

    assert res["loaded"] is True
    assert main_tries[0] == 3
    assert sleeps == [2.0, 2.0]
    bootstraps = [c for c in tracked.calls if c[1:3] == ["bootstrap", f"gui/{uid}"]]
    assert len(bootstraps) >= 3


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


def _hold_lock(cfg, body: dict | None = None):
    import fcntl
    from fanops.pipeline_run import _lock_path
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    if body is not None:
        os.ftruncate(fd, 0); os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(body).encode())
    return fd


def _append_log_line(cfg, *, stage="stage", ts=None):
    """Append one run.log line at `ts` (any stage) — the activity signal status now keys ALIVE off."""
    cfg.reports.mkdir(parents=True, exist_ok=True)
    ts = ts or datetime.now(timezone.utc).isoformat()
    rec = {"ts": ts, "level": "info", "stage": stage, "unit_id": "-", "outcome": "ok"}
    with cfg.log_path.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def test_status_alive_on_fresh_activity_no_loop_heartbeat(tmp_path, monkeypatch):
    # THE FIX: a live PID + a fresh run.log line of ANY kind (NO loop heartbeat — the first pass hasn't
    # finished) reads ALIVE, not "loaded but stale". This is the false-banner case the plan targets.
    cfg = Config(root=tmp_path)
    _append_log_line(cfg, stage="llm")                      # fresh activity, but no stage=heartbeat/origin=loop line
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 7;\n')))
    rep = daemon.status(cfg, interval=600)
    assert rep["loaded"] is True
    assert rep["verdict"] == "alive"                        # activity governs — loop heartbeat absent
    assert rep.get("run_line") is not None and "active" in rep["run_line"]


def test_status_alive_mid_pass_despite_stale_heartbeat(tmp_path, monkeypatch):
    from fanops.pipeline_run import note_stage
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cfg.log_path.write_text(_heartbeat_line(old))
    _append_log_line(cfg, stage="llm")                      # fresh activity: the held stage IS still emitting
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 7;\n')))
    fd = _hold_lock(cfg, {"pid": 4242, "started": "2020-01-01T00:00:00Z"})
    try:
        note_stage(cfg, "transcribe", "src-1")
        rep = daemon.status(cfg, interval=600)
        assert rep["verdict"] == "alive"
        assert rep.get("run_line") is not None
        assert "mid-pass: transcribe" in rep["run_line"]
    finally:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_status_stage_stuck_when_stage_held_and_log_silent(tmp_path, monkeypatch):
    # WEDGED: a stage IS held AND the newest run.log line is SILENT past the ceiling. Verdict names the
    # stage + the SILENCE (not stage_age — the word is "SILENT").
    from fanops.health_model import _STAGE_HANG_CEILING_S
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    silent_ts = (datetime.now(timezone.utc) - timedelta(seconds=_STAGE_HANG_CEILING_S + 120))
    cfg.log_path.write_text("")                             # only the silent line below is in run.log
    _append_log_line(cfg, stage="transcribe", ts=silent_ts.isoformat())
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 7;\n')))
    stage_old = silent_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    fd = _hold_lock(cfg, {"pid": 4242, "started": stage_old, "stage": "transcribe", "unit": "src-1",
                          "stage_started": stage_old})
    try:
        rep = daemon.status(cfg, interval=600)
        assert rep["verdict"] != "alive"
        assert "stage stuck" in rep["verdict"]
        assert "transcribe" in rep["verdict"] and "SILENT" in rep["verdict"]
    finally:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def test_status_stale_without_lease_unchanged(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cfg.log_path.write_text(_heartbeat_line(old))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 7;\n')))
    rep = daemon.status(cfg, interval=600)
    assert "loaded but stale" in rep["verdict"]
    assert rep.get("run_line") is None


def _status_alive(rep) -> bool:
    return rep["verdict"] == "alive"


@pytest.mark.parametrize("scenario", ["fresh_activity", "stage_silent", "old_no_lease"])
def test_status_and_doctor_agree_no_split_brain(tmp_path, monkeypatch, scenario):
    """THE anti-split-brain assertion (the test that would have caught the old bug): daemon.status and
    doctor._daemon_liveness_check derive alive/dead from the SAME liveness owner (daemon_progress), so
    they MUST agree on every fixture. Backlog is kept empty so the only signal is liveness."""
    from fanops.health_model import _STAGE_HANG_CEILING_S
    from fanops.doctor import _daemon_liveness_check
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 7;\n')))
    fd = None
    if scenario == "fresh_activity":
        _append_log_line(cfg, stage="llm")                 # live PID + fresh line -> both ALIVE
        expect_alive = True
    elif scenario == "stage_silent":
        silent_ts = (datetime.now(timezone.utc) - timedelta(seconds=_STAGE_HANG_CEILING_S + 120))
        _append_log_line(cfg, stage="transcribe", ts=silent_ts.isoformat())
        s = silent_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        fd = _hold_lock(cfg, {"pid": 4242, "started": s, "stage": "transcribe", "unit": "src-1",
                              "stage_started": s})
        expect_alive = False                               # stage held + silent -> both DEAD
    else:  # old_no_lease
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        cfg.log_path.write_text(_heartbeat_line(old))      # old heartbeat, no lease -> both DEAD
        expect_alive = False
    try:
        st = daemon.status(cfg, interval=600)
        chk = _daemon_liveness_check(cfg)
        assert _status_alive(st) is expect_alive
        assert chk["ok"] is expect_alive                   # doctor's verdict tracks status' verdict
    finally:
        if fd is not None:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


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

def test_stop_boots_out_keeper_before_main(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    fake = _fake_launchctl(bootout=(0, ""), list=(1, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)
    uid = os.getuid()
    daemon.stop(cfg)
    bootouts = [c for c in fake.calls if len(c) > 1 and c[1] == "bootout"]
    keeper_idx = next(i for i, c in enumerate(bootouts) if c[-1] == f"gui/{uid}/{daemon.KEEPER_LABEL}")
    main_idx = next(i for i, c in enumerate(bootouts) if c[-1] == f"gui/{uid}/{daemon.LABEL}")
    assert keeper_idx < main_idx


def test_stop_reports_keeper_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    lists = {daemon.KEEPER_LABEL: (1, ""), daemon.LABEL: (1, "")}
    def run(cmd, *a, **k):
        fake.calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "list" and len(cmd) > 2:
            label = cmd[2]
            rc, out = lists.get(label, (1, ""))
        else:
            rc, out = (0, "")
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    fake = run
    fake.calls = []
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)
    res = daemon.stop(cfg)
    assert res["keeper_stopped"] is True


def test_stop_remove_unlinks_keeper_plist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(0, ""), list=(1, "")))
    cfg = Config(root=tmp_path)
    kp = daemon.keeper_plist_path()
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_text("<plist/>")
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text("<plist/>")
    daemon.stop(cfg, remove=True)
    assert not kp.exists()
    assert not pp.exists()


def test_install_refuses_cross_checkout_working_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    other = tmp_path / "other-root"
    other.mkdir()
    pp = daemon.plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({"Label": daemon.LABEL, "WorkingDirectory": str(other)}))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(0, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)
    with pytest.raises(ValueError, match="WorkingDirectory"):
        daemon.install(cfg, interval=600)


def test_status_ignores_manual_heartbeat_without_loop_origin(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    manual = json.dumps({"ts": now, "level": "info", "stage": "heartbeat", "unit_id": "-", "outcome": "ok",
                         "heartbeat": now, "fanops_version": "0.3.0", "published_in_run": "0"})
    loop = json.dumps({"ts": now, "level": "info", "stage": "heartbeat", "unit_id": "-", "outcome": "ok",
                       "origin": "loop", "heartbeat": now, "fanops_version": "0.3.0", "published_in_run": "0"})
    cfg.log_path.write_text(manual + "\n" + loop + "\n")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(list=(0, '\t"PID" = 1;\n')))
    rep = daemon.status(cfg, interval=600)
    assert rep["verdict"] == "alive"


def test_stop_boots_out_label(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    fake = _fake_launchctl(bootout=(0, ""), list=(1, ""))         # after bootout the label is not loaded
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    cfg = Config(root=tmp_path)

    res = daemon.stop(cfg)

    uid = os.getuid()
    assert ["launchctl", "bootout", f"gui/{uid}/{daemon.LABEL}"] in fake.calls
    assert res["stopped"] is True                                 # list confirms it's no longer loaded


def test_stop_idempotent_when_not_loaded(tmp_path, monkeypatch):
    # Booting out an already-stopped label returns rc != 0 — that is "already stopped", not an error.
    # The list confirm finds it not loaded (rc != 0), so stopped is still True (W10: confirmed, not hardcoded).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), unload=(1, ""), list=(1, "")))
    cfg = Config(root=tmp_path)
    res = daemon.stop(cfg)                                         # must not raise
    assert res["stopped"] is True


def test_stop_reports_not_stopped_when_still_loaded(tmp_path, monkeypatch):
    # W10 (the honest part): if bootout AND the unload fallback both fail and the agent is STILL loaded
    # (list rc == 0), stopped must be False — not a hardcoded True that lies about the outcome.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run",
                        _fake_launchctl(bootout=(1, ""), unload=(1, ""), list=(0, '\t"PID" = 5;\n')))
    cfg = Config(root=tmp_path)
    res = daemon.stop(cfg)
    assert res["stopped"] is False                                # still loaded -> honestly not stopped


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
    assert not daemon.plist_path().exists()                  # nothing written on rejection


def test_main_daemon_logs_returns_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from fanops.cli import main
    assert main(["daemon", "logs"]) == 0
    assert "no logs yet" in capsys.readouterr().out


def test_main_daemon_stop_success_returns_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(0, ""), list=(1, "")))
    from fanops.cli import main
    assert main(["daemon", "stop"]) == 0
    assert "daemon stopped" in capsys.readouterr().out


def test_main_daemon_stop_reports_failure_when_still_loaded(tmp_path, monkeypatch, capsys):
    # W10 end-to-end: a stop that leaves the agent loaded exits non-zero with a clear stderr note,
    # instead of printing "daemon stopped" and returning 0 (the old hardcoded-success lie).
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run",
                        _fake_launchctl(bootout=(1, ""), unload=(1, ""), list=(0, '\t"PID" = 5;\n')))
    from fanops.cli import main
    assert main(["daemon", "stop"]) == 1
    assert "may still be loaded" in capsys.readouterr().err


# ── review hardening (python-review HIGH/MEDIUM) ─────────────────────────────────────────────



@pytest.mark.parametrize("raw", ["m", "h", "", "abc", "10x"])
def test_parse_interval_rejects_malformed(raw):
    # Pure-suffix / non-numeric inputs raise ValueError (-> cmd_daemon catches -> exit 2, never a trace).
    with pytest.raises(ValueError):
        daemon.parse_interval(raw)


def test_install_written_plist_prohibits_multiple_instances(tmp_path, monkeypatch):
    # The key must be present on the plist ACTUALLY WRITTEN to disk (what launchctl loads), not just the
    # in-memory render — mirrors test_install_writes_files_and_bootstraps asserting the gotcha on disk.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)
    daemon.install(cfg, interval=600, responder="inherit")
    pl = plistlib.loads(daemon.plist_path().read_bytes())
    assert pl.get("LSMultipleInstancesProhibited") is True
