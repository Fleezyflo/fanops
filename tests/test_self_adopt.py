"""Keeper-adopts-pump: the pump's in-process os.execv self-adopt is GONE. The resident `fanops run
--loop` records its running-HEAD SHA in every loop heartbeat (`_heartbeat(code=...)`), and the EXTERNAL
keeper (com.fanops.keeper, StartInterval 120s, `fanops daemon ensure`) compares that SHA to the SHA on
disk and kickstarts the PUMP when they drift. Adoption thus survives a wedged pump (the detector no
longer lives inside the thing that must restart).

These tests drive `daemon.ensure(cfg)` directly and assert the kickstart behavior against a recorded
launchctl. Setup mirrors test_daemon_keeper.py: HOME repointed at tmp_path, sys.platform forced darwin,
the pump `print` returns (0,"") so ensure sees it LOADED (no bootstrap) and no plist is written so
installed_interval is None (no plist-drift rewrite) — every run lands directly in the code-drift branch.

STORM GUARD: the deleted execv re-captured its baseline per re-exec, so it fired ONCE per deploy. The
keeper is stateless across 120s fires and the pump's stale heartbeat keeps the OLD SHA until its first
post-restart pass finishes — so ensure must NOT re-kickstart while the pump PID is younger than one
keeper interval. test_storm_guard_holds is the regression guard for that property.

FANOPS_AUTO_ADOPT is set explicitly in every test so a leaked repo .env value can't decide it
(tests/CLAUDE.md _LEAKY_ENV gotcha)."""
from __future__ import annotations
import os, subprocess

import pytest

from fanops.config import Config
from fanops import daemon


def _fake_launchctl(**spec):
    """Recorder for subprocess.run. `spec` maps a launchctl sub-verb (cmd[1]) to (rc, stdout); a
    `gui/.../<label>` key overrides the `print` verb for that label. Records every argv in `.calls`."""
    calls: list[list[str]] = []
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "print" and len(cmd) > 2:
            key = cmd[2]
            rc, out = spec.get(key, spec.get("print", (0, "")))
        else:
            rc, out = spec.get(verb, (0, ""))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    run.calls = calls
    return run


def _base_ensure_env(monkeypatch, tmp_path):
    """The shared setup that lands ensure() in the code-drift branch: pump LOADED (no bootstrap), no
    plist on disk (installed_interval None -> no plist-drift). Returns (cfg, fake, uid)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon, "_require_darwin", lambda: None)   # CI runs Linux; skip the darwin guard
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    fake = _fake_launchctl(**{main_print: (0, "")})
    monkeypatch.setattr(daemon.subprocess, "run", fake)
    return Config(root=tmp_path), fake, uid


def _kickstart_argv(uid):
    return ["launchctl", "kickstart", "-k", f"gui/{uid}/{daemon.LABEL}"]


def test_kickstarts_pump_on_drift(tmp_path, monkeypatch):
    # running SHA "aaa" != deployed SHA "bbb", storm guard permits (no pump PID) -> ensure kickstarts the
    # PUMP and returns action "kickstart_stale_code"; Studio is cycled too (its only adopter now).
    cfg, fake, uid = _base_ensure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda cfg: "aaa")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: ("bbb", "git-head"))
    monkeypatch.setattr(daemon, "_pump_pid_age_s", lambda: (None, None))   # no PID -> nothing to storm; kickstart proceeds
    studio_calls = []
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: studio_calls.append(cfg))

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in fake.calls                # the exact PUMP kickstart argv fired
    assert res["action"] == "kickstart_stale_code"
    assert studio_calls, "Studio must be cycled onto new code too (execv path deleted)"


def test_no_kickstart_when_shas_match(tmp_path, monkeypatch):
    # running == deployed -> no drift -> no kickstart; action stays whatever load/plist self-heals set ("none").
    cfg, fake, uid = _base_ensure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda cfg: "same")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: ("same", "git-head"))
    monkeypatch.setattr(daemon, "_pump_pid_age_s", lambda: (None, None))
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: None)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in fake.calls
    assert res["action"] == "none"


def test_no_kickstart_when_running_sha_absent(tmp_path, monkeypatch):
    # _last_heartbeat_code -> None (no log / pre-upgrade heartbeat with no `code`) DISARMS the drift branch.
    cfg, fake, uid = _base_ensure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda cfg: None)
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: ("bbb", "git-head"))
    monkeypatch.setattr(daemon, "_pump_pid_age_s", lambda: (None, None))
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: None)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in fake.calls           # absent running SHA -> never kickstart
    assert res["action"] == "none"


def test_no_kickstart_when_deployed_sha_absent(tmp_path, monkeypatch):
    # _version_signal -> (None,"unavailable") (git-less install) DISARMS the drift branch.
    cfg, fake, uid = _base_ensure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda cfg: "aaa")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: (None, "unavailable"))
    monkeypatch.setattr(daemon, "_pump_pid_age_s", lambda: (None, None))
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: None)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in fake.calls           # absent deployed SHA -> never kickstart
    assert res["action"] == "none"


def test_storm_guard_holds(tmp_path, monkeypatch):
    # THE CRITICAL REGRESSION GUARD. Drift is present (running "aaa" != deployed "bbb") BUT the pump PID
    # is younger than KEEPER_POLL_INTERVAL_S — it was just kickstarted and hasn't written its fresh-SHA
    # heartbeat yet. ensure MUST NOT re-kickstart (else a storm every 120s). Fake _launchctl("list",..)
    # to return a PID and fake `ps -o etimes=` to a small number so _pump_pid_age_s reports a young pump.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon, "_require_darwin", lambda: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    # `list <LABEL>` returns a live PID; every other verb (incl. the pump `print` -> loaded) defaults via spec.
    fake = _fake_launchctl(list=(0, '\t"PID" = 4321;\n\t"LastExitStatus" = 0;\n'), **{main_print: (0, "")})
    real_run = fake
    ps_calls = []
    def run(cmd, *a, **k):
        if cmd[:1] == ["ps"]:                                # the etimes probe -> young pump (10s)
            ps_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="10\n", stderr="")
        return real_run(cmd, *a, **k)
    run.calls = fake.calls
    monkeypatch.setattr(daemon.subprocess, "run", run)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda cfg: "aaa")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: ("bbb", "git-head"))
    studio_calls = []
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: studio_calls.append(cfg))
    cfg = Config(root=tmp_path)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in fake.calls           # young pump -> guard SKIPS the re-kickstart
    assert ps_calls, "the storm guard must actually probe pump PID age via `ps -o etimes=`"
    assert not studio_calls                                 # skipped path does not cycle Studio either
    assert res["action"] == "none"                          # no drift action set on a guarded skip


def test_storm_guard_lets_settled_pump_through(tmp_path, monkeypatch):
    # Complement to the guard: a pump whose PID is OLDER than one interval is settled — a genuine drift
    # on it (its heartbeat SHOULD have refreshed by now but hasn't) is a real stale-code case -> kickstart.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon, "_require_darwin", lambda: None)
    uid = os.getuid()
    main_print = f"gui/{uid}/{daemon.LABEL}"
    fake = _fake_launchctl(list=(0, '\t"PID" = 4321;\n'), **{main_print: (0, "")})
    def run(cmd, *a, **k):
        if cmd[:1] == ["ps"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="99999\n", stderr="")   # old pump
        return fake(cmd, *a, **k)
    run.calls = fake.calls
    monkeypatch.setattr(daemon.subprocess, "run", run)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda cfg: "aaa")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: ("bbb", "git-head"))
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: None)
    cfg = Config(root=tmp_path)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in fake.calls               # settled pump + drift -> kickstart fires
    assert res["action"] == "kickstart_stale_code"


def test_kill_switch_blocks_drift_kickstart(tmp_path, monkeypatch):
    # FANOPS_AUTO_ADOPT=0 -> the whole drift branch is skipped even with drift present.
    cfg, fake, uid = _base_ensure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "0")
    monkeypatch.setattr(daemon, "_last_heartbeat_code", lambda cfg: "aaa")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: ("bbb", "git-head"))
    monkeypatch.setattr(daemon, "_pump_pid_age_s", lambda: (None, None))
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: None)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in fake.calls           # kill switch honored
    assert res["action"] == "none"


# ── real _version_signal (NOT monkeypatched) — proves the signal follows the CODE tree, not cfg.root ──
# (retained from the pre-keeper suite: _version_signal is still the deployed-SHA source the keeper reads.)

def test_version_signal_reads_head_from_code_tree_not_cfg_root(tmp_path, monkeypatch):
    # _version_signal must `git rev-parse HEAD` in the tree holding the running fanops package
    # (fanops.__file__'s parent), NOT cfg.root (the DATA workspace, which by the FANOPS_ROOT split has no
    # .git). We point fanops.__file__ into a fresh hermetic git tree and assert the git-head arm fires.
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")                       # belt-and-suspenders; git is standard in CI
    import fanops
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "seed.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "seed"], check=True)
    pkg = tmp_path / "fake_pkg"; pkg.mkdir()                  # real dir so .resolve().parent lands in the tree
    (pkg / "__init__.py").write_text("")
    monkeypatch.setattr(fanops, "__file__", str(pkg / "__init__.py"))   # auto-restored on teardown
    want = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    sig, src = daemon._version_signal(Config(tmp_path))       # cfg.root is IRRELEVANT now — code tree wins
    assert (sig, src) == (want, "git-head")                  # deployed SHA read from the CODE tree's HEAD


def test_version_signal_falls_back_to_version_when_no_git(tmp_path, monkeypatch):
    # A real pip install (no .git anywhere above the package): the version fallback, source "version" —
    # which the keeper's drift branch treats as (None-flavored) deployed SHA is a string, not a git-head;
    # here we only pin that the fallback shape is unchanged. Point fanops.__file__ into a git-less tmp dir.
    import fanops
    pkg = tmp_path / "fake_pkg"; pkg.mkdir()                  # no `git init` anywhere above tmp_path
    (pkg / "__init__.py").write_text("")
    monkeypatch.setattr(fanops, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(fanops, "__version__", "9.9.9")      # explicit non-empty string for determinism
    sig, src = daemon._version_signal(Config(tmp_path))
    assert (sig, src) == ("9.9.9", "version")                # git-less install -> version source
