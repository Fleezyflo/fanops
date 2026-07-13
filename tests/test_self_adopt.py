"""Self-adopting deploy: the resident `fanops run --loop` re-execs at the QUIESCENT loop top when the
running-code signal (git HEAD / version) changes from the baseline captured once before the loop.

Boundary safety is the whole point: os.execv fires at the loop TOP, BEFORE `_cmd_run_pass` acquires the
run lease, so it abandons no in-flight pass (flock self-heals on exec). These tests drive `main(["run",
"--loop", ...])` with every external seam faked — the version signal, the account/preflight guards,
`_cmd_run_pass`, the Studio kickstart — and break the otherwise-infinite loop deterministically by
monkeypatching `os.execv` / `time.sleep` to raise a sentinel.

os.environ / cwd are sandboxed per test (chdir tmp_path); FANOPS_AUTO_ADOPT is set explicitly so a
leaked repo .env value can't decide the test (tests/CLAUDE.md _LEAKY_ENV gotcha)."""
from __future__ import annotations
import os

import pytest

from fanops import cli, daemon


class _Sentinel(Exception):
    """Raised from a faked os.execv / time.sleep to break the resident loop deterministically."""


def _disarm_guards(monkeypatch, tmp_path):
    """Neutralize everything the run loop touches EXCEPT the self-adopt decision under test."""
    monkeypatch.chdir(tmp_path)                              # Config() roots at tmp
    monkeypatch.setattr(cli, "_check_accounts", lambda cfg: 0)
    monkeypatch.setattr(cli, "_check_preflight", lambda cfg: 0)
    monkeypatch.setattr(cli, "_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(daemon, "_kickstart_studio_if_present", lambda cfg: None)
    # sleep is the loop's tail — raise the sentinel there so a tick that does NOT adopt still terminates.
    monkeypatch.setattr(cli.time, "sleep", lambda _s: (_ for _ in ()).throw(_Sentinel("sleep")))


def _version_sequence(*values):
    """A fake _version_signal that yields `values` in order (last value repeats) — each a (sig, src)."""
    calls = {"n": 0}
    def fake(cfg):
        i = min(calls["n"], len(values) - 1)
        calls["n"] += 1
        return values[i]
    fake.calls = calls
    return fake


def test_reexec_on_version_change_second_tick(tmp_path, monkeypatch):
    # baseline = ("aaa","git-head"); tick 1 sees "aaa" (no change) -> runs pass -> sleep sentinel breaks?
    # No: we want to reach tick 2 where the signal flips to "bbb" and execv fires. So tick-1 _cmd_run_pass
    # must NOT raise; the loop proceeds to time.sleep -> sentinel. We therefore let execv (tick 2) win by
    # making the FIRST poll already return the changed value is wrong; instead: baseline captured first,
    # then each loop-top poll. Sequence: [baseline capture]="aaa", [tick1 top]="aaa", [tick2 top]="bbb".
    _disarm_guards(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    seq = _version_sequence(("aaa", "git-head"), ("aaa", "git-head"), ("bbb", "git-head"))
    monkeypatch.setattr(daemon, "_version_signal", seq)
    # tick 1 runs a normal pass; DO NOT let sleep end the test on tick 1 — instead make _cmd_run_pass a
    # no-op that returns None so nothing is printed, and let the loop continue to sleep... which raises.
    # To reach tick 2 we must swallow the tick-1 sentinel: raise it only from execv, not sleep.
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)  # tick 1 sleeps quietly, loop continues
    monkeypatch.setattr(cli, "_cmd_run_pass", lambda cfg, bt: None)
    execv_calls = []
    def fake_execv(path, argv):
        execv_calls.append((path, list(argv)))
        raise _Sentinel("execv")                             # break the loop the instant adoption fires
    monkeypatch.setattr(os, "execv", fake_execv)

    with pytest.raises(_Sentinel):
        cli.main(["run", "--loop", "--interval", "60s"])

    assert len(execv_calls) == 1                             # adopted exactly once
    path, argv = execv_calls[0]
    import sys
    assert path == sys.executable and argv == [sys.executable, *sys.argv]   # re-exec self, same argv


def test_no_reexec_when_signal_unchanged(tmp_path, monkeypatch):
    # Signal never changes -> never adopts; the loop terminates via the tick-1 sleep sentinel instead.
    _disarm_guards(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: ("same", "git-head"))
    monkeypatch.setattr(cli, "_cmd_run_pass", lambda cfg, bt: None)
    execv_calls = []
    monkeypatch.setattr(os, "execv", lambda p, a: execv_calls.append((p, a)))

    with pytest.raises(_Sentinel):                           # sleep sentinel ends tick 1
        cli.main(["run", "--loop", "--interval", "60s"])
    assert execv_calls == []                                 # unchanged signal -> no re-exec


def test_no_reexec_when_auto_adopt_disabled(tmp_path, monkeypatch):
    # FANOPS_AUTO_ADOPT=0 disables self-adopt even when the signal changes.
    _disarm_guards(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "0")
    monkeypatch.setattr(daemon, "_version_signal",
                        _version_sequence(("aaa", "git-head"), ("bbb", "git-head")))
    monkeypatch.setattr(cli, "_cmd_run_pass", lambda cfg, bt: None)
    execv_calls = []
    monkeypatch.setattr(os, "execv", lambda p, a: execv_calls.append((p, a)))

    with pytest.raises(_Sentinel):                           # sleep sentinel ends tick 1
        cli.main(["run", "--loop", "--interval", "60s"])
    assert execv_calls == []                                 # kill switch honored


def test_no_reexec_when_signal_none_and_degraded_line_logged(tmp_path, monkeypatch):
    # _version_signal -> (None, "unavailable"): self-adopt is DISARMED (never re-exec on an absent
    # signal — fail-safe) AND the startup log carries the DEGRADED breadcrumb (the silent-no-signal
    # -forever regression guard). We capture the log by patching cli.get_logger.
    _disarm_guards(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: (None, "unavailable"))
    monkeypatch.setattr(cli, "_cmd_run_pass", lambda cfg, bt: None)
    logged = []
    monkeypatch.setattr(cli, "get_logger",
                        lambda cfg: (lambda *a, **k: logged.append((a, k))))
    execv_calls = []
    monkeypatch.setattr(os, "execv", lambda p, a: execv_calls.append((p, a)))

    with pytest.raises(_Sentinel):                           # sleep sentinel ends tick 1
        cli.main(["run", "--loop", "--interval", "60s"])
    assert execv_calls == []                                 # None signal short-circuits -> never re-exec
    # the DEGRADED breadcrumb was emitted at startup (outcome + a detail naming the disarm)
    degraded = [(a, k) for (a, k) in logged if a[:3] == ("adopt", "-", "degraded")]
    assert degraded, f"expected an 'adopt/-/degraded' log line, got {logged}"
    assert "disarmed" in str(degraded[0][1])


def test_baseline_log_names_source_and_value(tmp_path, monkeypatch):
    # The happy-path startup line names the SOURCE + the signal so an operator can SEE adoption is armed.
    _disarm_guards(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_version_signal", lambda cfg: ("abc123", "git-head"))
    monkeypatch.setattr(cli, "_cmd_run_pass", lambda cfg, bt: None)
    logged = []
    monkeypatch.setattr(cli, "get_logger",
                        lambda cfg: (lambda *a, **k: logged.append((a, k))))
    monkeypatch.setattr(os, "execv", lambda p, a: None)

    with pytest.raises(_Sentinel):                           # sleep sentinel ends tick 1 (signal unchanged)
        cli.main(["run", "--loop", "--interval", "60s"])
    baseline = [(a, k) for (a, k) in logged if a[:3] == ("adopt", "-", "baseline")]
    assert baseline, f"expected an 'adopt/-/baseline' log line, got {logged}"
    assert baseline[0][1].get("source") == "git-head" and baseline[0][1].get("signal") == "abc123"


def test_reexec_fires_before_lease_never_mid_pass(tmp_path, monkeypatch):
    # Boundary proof: adoption fires at the loop TOP, BEFORE _cmd_run_pass (which holds the run lease).
    # We assert _cmd_run_pass is NEVER entered on the adopting tick — execv wins first, so no lease is
    # ever held when we re-exec.
    _disarm_guards(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)  # tick 1 continues to tick 2
    monkeypatch.setattr(daemon, "_version_signal",
                        _version_sequence(("aaa", "git-head"), ("aaa", "git-head"), ("bbb", "git-head")))
    pass_ticks = {"n": 0}
    monkeypatch.setattr(cli, "_cmd_run_pass",
                        lambda cfg, bt: pass_ticks.__setitem__("n", pass_ticks["n"] + 1) or None)
    def fake_execv(p, a):
        raise _Sentinel("execv")
    monkeypatch.setattr(os, "execv", fake_execv)

    with pytest.raises(_Sentinel):
        cli.main(["run", "--loop", "--interval", "60s"])
    # tick 1 ran the pass (signal unchanged); tick 2 adopted at the TOP and never ran the pass.
    assert pass_ticks["n"] == 1                              # exactly one pass ran; the adopting tick did not


# ── real _version_signal (NOT monkeypatched) — proves the signal follows the CODE tree, not cfg.root ──

def test_version_signal_reads_head_from_code_tree_not_cfg_root(tmp_path, monkeypatch):
    # The fix: _version_signal must `git rev-parse HEAD` in the tree holding the running fanops package
    # (fanops.__file__'s parent), NOT cfg.root (the DATA workspace, which by the FANOPS_ROOT split has no
    # .git). We point fanops.__file__ into a fresh hermetic git tree and assert the git-head arm fires.
    import shutil, subprocess
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")                       # belt-and-suspenders; git is standard in CI
    import fanops
    from fanops.config import Config
    # a fresh, self-contained git tree with one commit (hermetic — never touches the enclosing repo)
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
    assert (sig, src) == (want, "git-head")                  # armed from the CODE tree's HEAD


def test_version_signal_falls_back_to_version_when_no_git(tmp_path, monkeypatch):
    # A real pip install (no .git anywhere above the package) must be byte-identical to today: the version
    # fallback, source "version" — which DISARMS self-adopt (the cli loop arms only on "git-head"). Point
    # fanops.__file__ into a git-less tmp dir and assert (__version__, "version").
    import fanops
    from fanops.config import Config
    pkg = tmp_path / "fake_pkg"; pkg.mkdir()                  # no `git init` anywhere above tmp_path
    (pkg / "__init__.py").write_text("")
    monkeypatch.setattr(fanops, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(fanops, "__version__", "9.9.9")      # explicit non-empty string for determinism
    sig, src = daemon._version_signal(Config(tmp_path))
    assert (sig, src) == ("9.9.9", "version")                # git-less install -> version source -> DISARMS
