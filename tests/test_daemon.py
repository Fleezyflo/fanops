"""Tests for `fanops daemon` (durable unattended run via launchd). The renderers are pure and
asserted by parsing the plist back with plistlib (robust to formatting). The side-effecting
install/status/stop are tested with `subprocess.run` mocked — NO real launchctl, NO real
~/Library/LaunchAgents write (HOME is repointed at tmp_path so every home-derived path is
sandboxed). The heartbeat parse is pinned against the REAL run.log line shape (log.py)."""
from __future__ import annotations
import os, plistlib, shlex, subprocess
from datetime import datetime, timedelta, timezone

import pytest

from fanops.config import Config
from fanops.errors import ToolchainMissingError
from fanops import daemon


def _heartbeat_line(ts: str) -> str:
    # EXACT shape get_logger writes for _heartbeat (JSON; daemon._heartbeat_age_s reads stage+ts).
    import json
    rec = {"ts": ts, "level": "info", "stage": "heartbeat", "unit_id": "-", "outcome": "ok",
           "heartbeat": ts, "fanops_version": "0.3.0", "published_in_run": "0"}
    return json.dumps(rec, separators=(",", ":")) + "\n"


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


def test_render_wrapper_uses_venv_fanops_cd_root_now_base_time(tmp_path):
    cfg = Config(root=tmp_path)
    w = daemon.render_wrapper(cfg, interval=600)
    assert w.startswith("#!/bin/bash")
    assert daemon._fanops_bin() in w                              # the SAME venv that installed it
    assert f"cd {shlex.quote(str(cfg.root))}" in w               # shell-quoted; not base, not /
    assert '--base-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in w    # a FRESH now each fire, not a frozen past date
    assert "FANOPS_RESPONDER" not in w                           # decoupled: .env/Config resolves the responder at fire time
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
    assert not daemon.wrapper_path(Config(root=tmp_path)).exists()                  # nothing written on rejection


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

def test_render_wrapper_shell_quotes_paths_with_metacharacters(tmp_path):
    # A workspace path with a space/quote/$ must be shell-safe in the generated wrapper: an unquoted
    # `cd "<path>"` would break out or let bash expand `$x`, silently running from the WRONG cwd and
    # defeating the daemon's #1 invariant (correct working directory). shlex.quote each interpolated path.
    weird = tmp_path / 'a b"c$d'
    weird.mkdir()
    cfg = Config(root=weird)
    w = daemon.render_wrapper(cfg, interval=600)
    assert f"cd {shlex.quote(str(weird))}" in w        # the cd target is shell-quoted
    assert f'cd "{weird}"' not in w                     # NOT the naive double-quoted form


def test_installed_interval_missing_key_or_corrupt_returns_none(tmp_path, monkeypatch):
    # A plist missing StartInterval must NOT crash (the old int(None) raised TypeError, masked by a
    # blanket catch); a corrupt plist must degrade to None (best-effort, like Config.tuning()).
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config(root=tmp_path)
    pp = daemon.plist_path(); pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({"Label": daemon.LABEL}))      # no StartInterval key
    assert daemon.installed_interval(cfg) is None
    pp.write_bytes(b"not a plist at all")                        # corrupt
    assert daemon.installed_interval(cfg) is None


@pytest.mark.parametrize("raw", ["m", "h", "", "abc", "10x"])
def test_parse_interval_rejects_malformed(raw):
    # Pure-suffix / non-numeric inputs raise ValueError (-> cmd_daemon catches -> exit 2, never a trace).
    with pytest.raises(ValueError):
        daemon.parse_interval(raw)


# ── MOL-81 criterion 1: atomic wrapper write (torn-write hardening) ───────────────────────────

def test_install_leaves_no_torn_wrapper_when_write_interrupted(tmp_path, monkeypatch):
    # MOL-81 instance 1: launchd's plist names fanops-run.sh as its ProgramArguments target; bash reads
    # a script via buffered reads AS it executes, so a non-atomic overwrite that crashes mid-write can be
    # read torn by an in-flight tick. The write MUST be temp-file + os.replace (mirroring
    # controlio.write_json_atomic / autopilot.set_env_var), so a crash mid-write leaves the ORIGINAL
    # intact wrapper at the real path — never a partial one. Pre-seed a valid wrapper, then make the temp
    # write blow up partway; assert the real path is byte-identical to the good original (never torn).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    wp = daemon.wrapper_path(cfg)
    good = "#!/bin/bash\n# prior good wrapper\nexec true\n"
    wp.write_text(good)                                          # a valid wrapper already in place

    # Simulate a crash at the atomic swap-in: the new content is fully written to a same-dir temp, then
    # os.replace dies before it lands. With an in-place write_text this class of failure torns the real
    # path; with temp+os.replace the ORIGINAL wrapper at wp is untouched (that is the whole point).
    real_replace = os.replace
    def boom_replace(src, dst, *a, **k):
        if str(dst) == str(wp):
            raise OSError("crash mid-write (os.replace interrupted)")
        return real_replace(src, dst, *a, **k)
    monkeypatch.setattr(daemon.os, "replace", boom_replace)

    with pytest.raises(OSError):
        daemon.install(cfg, interval=600, responder="inherit")

    assert wp.read_text() == good                               # ORIGINAL intact — never a torn/partial wrapper
    # no leaked temp files left behind in the wrapper dir (best-effort cleanup unlinked the temp)
    leaked = [p.name for p in wp.parent.iterdir() if p.name != wp.name and p.name.startswith(wp.name)]
    assert leaked == []


def test_install_wrapper_write_is_atomic_replace_not_inplace(tmp_path, monkeypatch):
    # Pin the MECHANISM: the wrapper reaches its final path via os.replace of a same-dir temp (atomic),
    # not a direct write_text into place. Record the os.replace call whose destination is the wrapper.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(bootout=(1, ""), bootstrap=(0, "")))
    cfg = Config(root=tmp_path)
    wp = daemon.wrapper_path(cfg)

    replaces: list[tuple[str, str]] = []
    real_replace = os.replace
    def rec_replace(src, dst, *a, **k):
        replaces.append((str(src), str(dst))); return real_replace(src, dst, *a, **k)
    monkeypatch.setattr(daemon.os, "replace", rec_replace)

    daemon.install(cfg, interval=600, responder="inherit")

    wrapper_replaces = [(s, d) for s, d in replaces if d == str(wp)]
    assert wrapper_replaces, "wrapper must be os.replace'd into place (atomic), not written in-place"
    src, _ = wrapper_replaces[0]
    assert os.path.dirname(src) == os.path.dirname(str(wp))      # same-dir temp -> os.replace stays atomic
    assert daemon.wrapper_path(cfg).exists() and os.access(wp, os.X_OK)   # still 0755 after the atomic swap


# ── MOL-81 criterion 2: overlap / single-flight prevention (no concurrent ticks) ──────────────

def test_render_plist_prohibits_multiple_instances(tmp_path):
    # MOL-81 instance 2: a slow LLM/network tick can still be running when launchd's StartInterval
    # re-fires; launchd's default permits a SECOND overlapping fanops-run process, which then contends
    # for the ledger flock and silently wastes a full respond-and-advance cycle. LSMultipleInstancesProhibited
    # delegates de-duplication to launchd itself (the key that exists for exactly this) — the next fire is
    # skipped while the prior instance is alive. ThrottleInterval only floors RESTART cadence, not overlap.
    cfg = Config(root=tmp_path)
    pl = plistlib.loads(daemon.render_plist(cfg, interval=600).encode())
    assert pl.get("LSMultipleInstancesProhibited") is True
    # crash-restart semantics (a different, already-correct concern) must be UNCHANGED by this fix
    assert pl["RunAtLoad"] is True
    assert pl["ThrottleInterval"] == daemon._MIN_INTERVAL


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
