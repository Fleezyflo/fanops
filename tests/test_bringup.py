"""`fanops up` — one-step self-healing bring-up composer (brief docs/design/briefs/16-one-step-bring-up.md).

The composer chains four planes in dependency order — git (advisory) -> postiz (gate) -> daemon
(gate) -> studio (report) — and ends in ONE honest READY / NOT-READY verdict. Every external plane
is MOCKED here: the suite NEVER shells real docker / launchctl / git-network (CLAUDE.md forbids
speculative live CLI, and parallel local suites crash the host). We drive the seams the composer
delegates to (`postiz-ondemand.sh ensure`, `daemon.ensure`, `_launchctl kickstart`, the socket
probe) and assert the ORDER, the SHORT-CIRCUIT, the git-advisory-never-mutates invariant, the
daemon freshness restart, the honest verdict, idempotent re-run, and the non-darwin typed skip.

os.environ / HOME are sandboxed per test via monkeypatch (clean teardown; no leak — tests/CLAUDE.md)."""
from __future__ import annotations
import os, socket, subprocess
from datetime import datetime, timedelta, timezone

from fanops.config import Config
from fanops import daemon


def _listen_studio():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((daemon.STUDIO_DEFAULT_HOST, daemon.STUDIO_DEFAULT_PORT))
    srv.listen(1)
    return srv


# ── mock helpers (mirror tests/test_daemon_keeper.py::_fake_launchctl) ────────────────────────

def _fake_launchctl(**spec):
    """Fake launchctl: per-verb (rc, stdout); `print gui/<uid>/<label>` keyed by the domain string.
    Records every argv so a test can assert kickstart fired / a mutating verb did NOT."""
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


def _write_heartbeat(cfg: Config, *, ts: datetime, version: str = "0.3.0") -> None:
    """Append one JSON loop-heartbeat line to cfg.log_path (what daemon freshness reads)."""
    import json
    cfg.reports.mkdir(parents=True, exist_ok=True)
    rec = {"stage": "heartbeat", "origin": "loop", "ts": ts.isoformat(),
           "heartbeat": ts.isoformat(), "fanops_version": version}
    with cfg.log_path.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def _point_ondemand_at_real_file(tmp_path, monkeypatch):
    """Create a real on-demand script file and point FANOPS_POSTIZ_ONDEMAND at it, so the plane's
    `script.exists()` guard passes. The subprocess result (`bash <script> ensure`) is controlled by
    the test's own monkeypatch on daemon.subprocess.run, not by the file's contents."""
    script = tmp_path / "postiz-ondemand.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("FANOPS_POSTIZ_ONDEMAND", str(script))
    return script


# ── plane resolution: the on-demand script path override ──────────────────────────────────────

def test_resolve_ondemand_script_env_override(tmp_path, monkeypatch):
    script = tmp_path / "custom-ondemand.sh"
    script.write_text("#!/usr/bin/env bash\n")
    monkeypatch.setenv("FANOPS_POSTIZ_ONDEMAND", str(script))
    assert daemon._ondemand_script() == script


def test_resolve_ondemand_script_defaults_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FANOPS_POSTIZ_ONDEMAND", raising=False)
    assert daemon._ondemand_script() == tmp_path / "postiz-selfhost" / "postiz-ondemand.sh"


# ── git plane: ADVISORY, non-mutating ─────────────────────────────────────────────────────────

def test_git_plane_reports_behind_without_failing_and_never_mutates(tmp_path, monkeypatch):
    # The real argv is `git -C <root> <subcommand> …` — match the subcommand anywhere in argv, not
    # a fixed slot, so the assertion tracks the actual command shape.
    calls: list[list[str]] = []
    def fake_git(cmd, *a, **k):
        calls.append(list(cmd))
        if "rev-list" in cmd:                        # left-right count: ahead \t behind
            return subprocess.CompletedProcess(cmd, 0, stdout="0\t7\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")   # fetch (+ any other)
    monkeypatch.setattr(daemon.subprocess, "run", fake_git)
    cfg = Config(root=tmp_path)

    plane = daemon._plane_git(cfg)

    assert plane["ok"] is True                       # advisory -> never fails the run
    assert plane["behind"] == 7
    assert "7" in plane["detail"]
    # the non-goal that MATTERS: bring-up must NEVER mutate the tree — no mutating verb in ANY argv
    mutating = {"merge", "reset", "checkout", "rebase", "pull"}
    assert not any(mutating & set(c) for c in calls), f"git plane mutated: {calls}"


def test_git_plane_fetch_failure_is_still_advisory(tmp_path, monkeypatch):
    def fake_git(cmd, *a, **k):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="network down")
    monkeypatch.setattr(daemon.subprocess, "run", fake_git)
    cfg = Config(root=tmp_path)
    plane = daemon._plane_git(cfg)
    assert plane["ok"] is True                       # a fetch failure never blocks bring-up


# ── postiz plane: shells out to the on-demand script; honest gate ─────────────────────────────

def test_postiz_plane_ready_on_script_exit_0(tmp_path, monkeypatch):
    _point_ondemand_at_real_file(tmp_path, monkeypatch)
    ran: list[list[str]] = []
    def fake_run(cmd, *a, **k):
        ran.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="postiz: up", stderr="")
    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    cfg = Config(root=tmp_path)

    plane = daemon._plane_postiz(cfg)

    assert plane["ok"] is True
    assert any(c[:1] == ["bash"] and c[-1] == "ensure" for c in ran)   # reused verbatim, not reimplemented


def test_postiz_plane_notready_on_nonzero_surfaces_stderr_tail(tmp_path, monkeypatch):
    _point_ondemand_at_real_file(tmp_path, monkeypatch)
    def fake_run(cmd, *a, **k):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="postiz: WARNING backend did not answer\nMASTRA...\n")
    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    cfg = Config(root=tmp_path)

    plane = daemon._plane_postiz(cfg)

    assert plane["ok"] is False
    assert "did not answer" in plane["detail"] or "MASTRA" in plane["detail"]


def test_postiz_plane_notready_when_script_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FANOPS_POSTIZ_ONDEMAND", raising=False)   # default path, which does not exist
    cfg = Config(root=tmp_path)
    plane = daemon._plane_postiz(cfg)
    assert plane["ok"] is False
    assert "postiz-ondemand.sh" in plane["detail"]                # names the missing script, does not crash


# ── daemon plane: freshness restart when already running ──────────────────────────────────────

def test_daemon_plane_kickstarts_running_daemon_and_confirms_fresh_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    cfg = Config(root=tmp_path)
    daemon.plist_path().parent.mkdir(parents=True, exist_ok=True)
    daemon.plist_path().write_text(daemon.render_plist(cfg, interval=600))
    fake = _fake_launchctl(**{main_print: (0, '\t"PID" = 4321;\n')})   # already loaded + running
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    _write_heartbeat(cfg, ts=datetime.now(timezone.utc) + timedelta(hours=1))

    plane = daemon._plane_daemon(cfg, kickstart=True)

    assert plane["ok"] is True
    assert plane["restarted"] is True
    assert any(c[1] == "kickstart" and c[-1] == main_print for c in fake.calls), \
        f"expected kickstart -k on the running daemon, calls={fake.calls}"


def test_daemon_plane_kickstart_waits_past_throttle_interval(tmp_path, monkeypatch):
    # MOL-697: launchd ThrottleInterval=_MIN_INTERVAL (60s). A 30s subprocess wrapper on
    # `kickstart -k` returns rc 124 while state is still "spawn scheduled" — false NOT-READY.
    # Kickstart must use timeout >= ThrottleInterval + slack so bring-up survives the delay.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    cfg = Config(root=tmp_path)
    daemon.plist_path().parent.mkdir(parents=True, exist_ok=True)
    daemon.plist_path().write_text(daemon.render_plist(cfg, interval=600))
    seen_kickstart_timeout: list[float] = []

    def run(cmd, *a, **k):
        timeout = k.get("timeout")
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "kickstart":
            seen_kickstart_timeout.append(float(timeout if timeout is not None else 0))
            if timeout is None or float(timeout) < daemon._MIN_INTERVAL:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or 0)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if verb == "print" and len(cmd) > 2 and cmd[2] == main_print:
            return subprocess.CompletedProcess(cmd, 0, stdout='\t"PID" = 4321;\n', stderr="")
        if verb == "list":
            return subprocess.CompletedProcess(cmd, 0, stdout='\t"PID" = 4321;\n', stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(daemon.subprocess, "run", run)
    _write_heartbeat(cfg, ts=datetime.now(timezone.utc) + timedelta(hours=1))

    plane = daemon._plane_daemon(cfg, kickstart=True)

    assert plane["ok"] is True, plane
    assert plane["restarted"] is True
    assert seen_kickstart_timeout, "kickstart never invoked"
    assert seen_kickstart_timeout[0] >= daemon._MIN_INTERVAL, \
        f"kickstart timeout {seen_kickstart_timeout[0]} must cover ThrottleInterval={daemon._MIN_INTERVAL}"


def test_daemon_plane_kickstart_real_failure_still_not_ready(tmp_path, monkeypatch):
    # Real launchctl non-zero still surfaces NOT-READY (MOL-697 must not swallow failures).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    cfg = Config(root=tmp_path)
    daemon.plist_path().parent.mkdir(parents=True, exist_ok=True)
    daemon.plist_path().write_text(daemon.render_plist(cfg, interval=600))
    fake = _fake_launchctl(**{main_print: (0, '\t"PID" = 4321;\n'), "kickstart": (1, "boom")})
    monkeypatch.setattr(daemon.subprocess, "run", fake)

    plane = daemon._plane_daemon(cfg, kickstart=True)

    assert plane["ok"] is False
    assert plane["restarted"] is False
    assert "kickstart failed" in plane["detail"]


def test_daemon_plane_ensures_then_loads_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    cfg = Config(root=tmp_path)
    daemon.plist_path().parent.mkdir(parents=True, exist_ok=True)
    daemon.plist_path().write_text(daemon.render_plist(cfg, interval=600))
    # print says NOT loaded until a bootstrap happens; ensure() bootstraps it
    def run(cmd, *a, **k):
        if len(cmd) > 1 and cmd[1] == "print" and cmd[2] == main_print:
            loaded = any(c[1:3] == ["bootstrap", f"gui/{uid}"] for c in run.calls)
            return subprocess.CompletedProcess(cmd, 0 if loaded else 1, stdout="", stderr="")
        run.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    run.calls = []
    monkeypatch.setattr(daemon.subprocess, "run", run)

    plane = daemon._plane_daemon(cfg, kickstart=True)

    assert plane["ok"] is True
    # a not-yet-running daemon is brought up via ensure's bootstrap, NOT kickstarted
    assert plane["restarted"] is False
    assert any(c[1] == "bootstrap" for c in run.calls)


def test_daemon_ensure_signature_and_behavior_unchanged(tmp_path, monkeypatch):
    # PIN daemon.ensure: bring-up must not have altered its aliveness contract (the keeper depends on it).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(**{main_print: (0, "")}))
    cfg = Config(root=tmp_path)
    res = daemon.ensure(cfg)
    assert res == {"label": daemon.LABEL, "loaded": True, "action": "none"}


def test_daemon_plane_off_darwin_typed_skip_no_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "linux")
    cfg = Config(root=tmp_path)
    plane = daemon._plane_daemon(cfg, kickstart=True)   # must NOT raise
    assert plane["ok"] is False
    assert plane["skipped"] is True
    assert "macOS" in plane["detail"] or "launchd" in plane["detail"]


# ── studio plane: report-only ─────────────────────────────────────────────────────────────────

def test_studio_plane_reports_up_when_port_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    srv = _listen_studio()
    try:
        plane = daemon._plane_studio(Config(root=tmp_path))
        assert plane["ok"] is True
        assert plane["report_only"] is True
    finally:
        srv.close()


def test_studio_plane_reports_down_with_launch_command(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    plane = daemon._plane_studio(Config(root=tmp_path))
    assert plane["ok"] is False
    assert plane["report_only"] is True
    assert "fanops studio" in plane["detail"]


def test_studio_plane_kickstarts_when_plist_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.studio_plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text("<plist/>")
    fake = _fake_launchctl(kickstart=(0, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    srv = _listen_studio()
    try:
        plane = daemon._plane_studio(Config(root=tmp_path))
        assert plane["ok"] is True and plane["report_only"] is True
        assert "cycled onto current code" in plane["detail"]
        assert any(c[:3] == ["launchctl", "kickstart", "-k"] and c[-1].endswith(daemon.STUDIO_LABEL)
                   for c in fake.calls)
    finally:
        srv.close()


def test_studio_plane_waits_for_port_to_come_back_after_kickstart(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.studio_plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text("<plist/>")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(kickstart=(0, "")))
    srv = {"s": None}
    n = {"i": 0}

    def sleep(_s):
        n["i"] += 1
        if n["i"] == 2 and srv["s"] is None:
            srv["s"] = _listen_studio()

    monkeypatch.setattr(daemon.time, "sleep", sleep)
    try:
        plane = daemon._plane_studio(Config(root=tmp_path))
        assert plane["ok"] is True and plane["report_only"] is True
        assert "cycled onto current code" in plane["detail"]
        assert n["i"] >= 2
    finally:
        if srv["s"] is not None:
            srv["s"].close()


def test_keeper_studio_redeploy_probes_once_and_never_polls(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.studio_plist_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text("<plist/>")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(kickstart=(0, "")))
    slept: list[float] = []
    monkeypatch.setattr(daemon.time, "sleep", lambda s: slept.append(s))
    daemon._kickstart_studio_if_present(Config(root=tmp_path))
    assert slept == []


# ── heartbeat freshness helper ────────────────────────────────────────────────────────────────

def test_heartbeat_fresh_since_true_when_newer(tmp_path):
    cfg = Config(root=tmp_path)
    since = datetime.now(timezone.utc)
    _write_heartbeat(cfg, ts=since + timedelta(seconds=5))
    assert daemon._heartbeat_fresh_since(cfg, since, tries=2, step=0.0) is True


def test_heartbeat_fresh_since_false_when_only_stale(tmp_path):
    cfg = Config(root=tmp_path)
    since = datetime.now(timezone.utc)
    _write_heartbeat(cfg, ts=since - timedelta(seconds=30))   # older than the restart instant
    assert daemon._heartbeat_fresh_since(cfg, since, tries=2, step=0.0) is False


def test_heartbeat_fresh_since_true_on_any_new_line_not_only_loop_heartbeat(tmp_path):
    # Change 1e: the freshness proof now polls _newest_activity_ts (ANY run.log line), so a restarted
    # daemon proves healthy on its FIRST stage line — not only after a whole pass finishes (a loop
    # heartbeat lands only then). A non-heartbeat line newer than the restart instant is enough.
    import json
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    since = datetime.now(timezone.utc)
    rec = {"ts": (since + timedelta(seconds=3)).isoformat(), "level": "info",
           "stage": "transcribe", "unit_id": "src-1", "outcome": "ok"}   # a NON-heartbeat stage line
    cfg.log_path.write_text(json.dumps(rec) + "\n")
    assert daemon._heartbeat_fresh_since(cfg, since, tries=2, step=0.0) is True

