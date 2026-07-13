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
import os, subprocess
from datetime import datetime, timedelta, timezone

from fanops.config import Config
from fanops import daemon


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
    # a FRESH heartbeat lands after the restart instant
    monkeypatch.setattr(daemon, "_heartbeat_fresh_since",
                        lambda cfg, since, **k: True)

    plane = daemon._plane_daemon(cfg, kickstart=True)

    assert plane["ok"] is True
    assert plane["restarted"] is True
    assert any(c[1] == "kickstart" and c[-1] == main_print for c in fake.calls), \
        f"expected kickstart -k on the running daemon, calls={fake.calls}"


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
    monkeypatch.setattr(daemon, "_heartbeat_fresh_since", lambda cfg, since, **k: True)

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
    # No plist installed -> report-only branch (plist absence forced so the operator's real
    # com.fanops.studio.plist can't flip this test onto the kickstart branch).
    monkeypatch.setattr(daemon, "studio_plist_path", lambda: tmp_path / "com.fanops.studio.plist")
    monkeypatch.setattr(daemon, "_studio_port_answers", lambda *a, **k: True)
    cfg = Config(root=tmp_path)
    plane = daemon._plane_studio(cfg)
    assert plane["ok"] is True
    assert plane["report_only"] is True


def test_studio_plane_reports_down_with_launch_command(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "studio_plist_path", lambda: tmp_path / "com.fanops.studio.plist")
    monkeypatch.setattr(daemon, "_studio_port_answers", lambda *a, **k: False)
    cfg = Config(root=tmp_path)
    plane = daemon._plane_studio(cfg)
    assert plane["ok"] is False
    assert plane["report_only"] is True                 # never fails the overall verdict
    assert "fanops studio" in plane["detail"]            # the exact command to run


def test_studio_plane_kickstarts_when_plist_present(tmp_path, monkeypatch):
    # Plist installed -> REAL restart: kickstart -k the studio label, then the port gate confirms.
    plist = tmp_path / "com.fanops.studio.plist"; plist.write_text("<plist/>")
    monkeypatch.setattr(daemon, "studio_plist_path", lambda: plist)
    fake = _fake_launchctl(kickstart=(0, ""))
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    monkeypatch.setattr(daemon, "_studio_port_answers", lambda *a, **k: True)
    cfg = Config(root=tmp_path)
    plane = daemon._plane_studio(cfg)
    assert plane["ok"] is True and plane["report_only"] is True
    assert "cycled onto current code" in plane["detail"]
    # kickstart -k gui/<uid>/com.fanops.studio actually fired
    assert any(c[:3] == ["launchctl", "kickstart", "-k"] and c[-1].endswith(daemon.STUDIO_LABEL)
               for c in fake.calls)


def test_studio_plane_reports_not_answering_when_restart_fails_port(tmp_path, monkeypatch):
    # Plist present, kickstart ok, but the port never answers -> not-answering (still non-gating).
    plist = tmp_path / "com.fanops.studio.plist"; plist.write_text("<plist/>")
    monkeypatch.setattr(daemon, "studio_plist_path", lambda: plist)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(kickstart=(0, "")))
    monkeypatch.setattr(daemon, "_studio_port_answers", lambda *a, **k: False)
    cfg = Config(root=tmp_path)
    plane = daemon._plane_studio(cfg)
    assert plane["ok"] is False and plane["report_only"] is True
    assert "not answering" in plane["detail"]


# ── composer: order + short-circuit + honest verdict ──────────────────────────────────────────

def _stub_planes(monkeypatch, *, git=True, postiz=True, daemon_ok=True, studio=True,
                 order_sink=None):
    """Replace each plane helper with a stub that records call order and returns a fixed verdict."""
    def mk(name, ok, extra=None):
        def _p(cfg, *a, **k):
            if order_sink is not None:
                order_sink.append(name)
            d = {"plane": name, "ok": ok, "detail": f"{name} {'ok' if ok else 'bad'}"}
            if extra:
                d.update(extra)
            return d
        return _p
    monkeypatch.setattr(daemon, "_plane_git", mk("git", git, {"behind": 0}))
    monkeypatch.setattr(daemon, "_plane_postiz", mk("postiz", postiz))
    monkeypatch.setattr(daemon, "_plane_daemon", mk("daemon", daemon_ok, {"restarted": False}))
    monkeypatch.setattr(daemon, "_plane_studio", mk("studio", studio, {"report_only": True}))


def test_up_composes_planes_in_dependency_order(tmp_path, monkeypatch):
    order: list[str] = []
    _stub_planes(monkeypatch, order_sink=order)
    cfg = Config(root=tmp_path)
    res = daemon.up(cfg, kickstart=True)
    assert order == ["git", "postiz", "daemon", "studio"]
    assert res["ready"] is True


def test_up_short_circuits_at_first_failing_gate_postiz(tmp_path, monkeypatch):
    order: list[str] = []
    _stub_planes(monkeypatch, postiz=False, order_sink=order)
    cfg = Config(root=tmp_path)
    res = daemon.up(cfg, kickstart=True)
    assert res["ready"] is False
    assert res["first_fail"] == "postiz"
    assert order == ["git", "postiz"]                    # daemon + studio never ran
    assert "postiz" in res["verdict"].lower()


def test_up_honest_verdict_postiz_backend_dead_is_not_ready(tmp_path, monkeypatch):
    # The honesty principle: a dead Postiz backend behind nginx -> NOT-READY, never READY.
    _stub_planes(monkeypatch, postiz=False)
    cfg = Config(root=tmp_path)
    res = daemon.up(cfg, kickstart=True)
    assert res["ready"] is False
    assert "READY" not in res["verdict"] or res["verdict"].startswith("NOT-READY")


def test_up_git_behind_does_not_block_ready(tmp_path, monkeypatch):
    # Git is advisory: a behind-main checkout still reaches READY when the real planes are healthy.
    def mk(name, ok, extra=None):
        def _p(cfg, *a, **k):
            d = {"plane": name, "ok": ok, "detail": f"{name}"}
            if extra: d.update(extra)
            return d
        return _p
    monkeypatch.setattr(daemon, "_plane_git", mk("git", True, {"behind": 12}))
    monkeypatch.setattr(daemon, "_plane_postiz", mk("postiz", True))
    monkeypatch.setattr(daemon, "_plane_daemon", mk("daemon", True, {"restarted": True}))
    monkeypatch.setattr(daemon, "_plane_studio", mk("studio", True, {"report_only": True}))
    cfg = Config(root=tmp_path)
    res = daemon.up(cfg, kickstart=True)
    assert res["ready"] is True
    assert res["git"]["behind"] == 12                    # surfaced, not fatal


def test_up_studio_down_does_not_block_ready(tmp_path, monkeypatch):
    # Studio is report-only: a down Studio still reaches READY (bring-up does not daemonize Studio).
    _stub_planes(monkeypatch, studio=False)
    cfg = Config(root=tmp_path)
    res = daemon.up(cfg, kickstart=True)
    assert res["ready"] is True                          # studio report-only never blocks


def test_up_idempotent_rerun_all_healthy_stays_ready(tmp_path, monkeypatch):
    _stub_planes(monkeypatch)
    cfg = Config(root=tmp_path)
    first = daemon.up(cfg, kickstart=True)
    second = daemon.up(cfg, kickstart=True)
    assert first["ready"] is True and second["ready"] is True


# ── CLI wiring: `fanops up` ───────────────────────────────────────────────────────────────────

def test_main_up_returns_0_and_prints_ready(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _stub_planes(monkeypatch)
    from fanops.cli import main
    rc = main(["up"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "READY" in out
    # the 4-line plane status is printed
    for name in ("git", "postiz", "daemon", "studio"):
        assert name in out.lower()


def test_main_up_nonzero_exit_on_not_ready(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _stub_planes(monkeypatch, postiz=False)
    from fanops.cli import main
    rc = main(["up"])
    out = capsys.readouterr().out
    assert rc != 0
    assert "NOT-READY" in out


def test_main_up_off_darwin_daemon_skip_is_not_a_crash(tmp_path, monkeypatch, capsys):
    # Non-darwin: the daemon plane returns a typed skip; the CLI must exit cleanly (no traceback),
    # honestly NOT-READY (daemon freshness unproven), never a raw exception.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(daemon.sys, "platform", "linux")
    # let git/postiz/studio pass, daemon plane runs for real (off-darwin -> skip)
    def mk(name, ok, extra=None):
        def _p(cfg, *a, **k):
            d = {"plane": name, "ok": ok, "detail": name}
            if extra: d.update(extra)
            return d
        return _p
    monkeypatch.setattr(daemon, "_plane_git", mk("git", True, {"behind": 0}))
    monkeypatch.setattr(daemon, "_plane_postiz", mk("postiz", True))
    monkeypatch.setattr(daemon, "_plane_studio", mk("studio", True, {"report_only": True}))
    from fanops.cli import main
    rc = main(["up"])          # must not raise
    out = capsys.readouterr().out
    assert rc != 0
    assert "NOT-READY" in out


# ── heartbeat freshness helper ────────────────────────────────────────────────────────────────

def test_heartbeat_fresh_since_true_when_newer(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    cfg = Config(root=tmp_path)
    since = datetime.now(timezone.utc)
    _write_heartbeat(cfg, ts=since + timedelta(seconds=5))
    assert daemon._heartbeat_fresh_since(cfg, since, tries=2, step=0.0) is True


def test_heartbeat_fresh_since_false_when_only_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    cfg = Config(root=tmp_path)
    since = datetime.now(timezone.utc)
    _write_heartbeat(cfg, ts=since - timedelta(seconds=30))   # older than the restart instant
    assert daemon._heartbeat_fresh_since(cfg, since, tries=2, step=0.0) is False


def test_heartbeat_fresh_since_true_on_any_new_line_not_only_loop_heartbeat(tmp_path, monkeypatch):
    # Change 1e: the freshness proof now polls _newest_activity_ts (ANY run.log line), so a restarted
    # daemon proves healthy on its FIRST stage line — not only after a whole pass finishes (a loop
    # heartbeat lands only then). A non-heartbeat line newer than the restart instant is enough.
    import json
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    cfg = Config(root=tmp_path)
    cfg.reports.mkdir(parents=True, exist_ok=True)
    since = datetime.now(timezone.utc)
    rec = {"ts": (since + timedelta(seconds=3)).isoformat(), "level": "info",
           "stage": "transcribe", "unit_id": "src-1", "outcome": "ok"}   # a NON-heartbeat stage line
    cfg.log_path.write_text(json.dumps(rec) + "\n")
    assert daemon._heartbeat_fresh_since(cfg, since, tries=2, step=0.0) is True


def test_no_publish_side_effect_in_bringup(tmp_path, monkeypatch):
    # Bring-up must never publish / flip live. Assert the composer touches no ledger/publish seam:
    # every plane it calls is git/postiz-script/launchctl/socket only. Guard by rejecting FANOPS_LIVE.
    _stub_planes(monkeypatch)
    monkeypatch.delenv("FANOPS_LIVE", raising=False)
    cfg = Config(root=tmp_path)
    res = daemon.up(cfg, kickstart=True)
    assert res["ready"] is True
    assert os.getenv("FANOPS_LIVE") in (None, "")          # never set by bring-up
