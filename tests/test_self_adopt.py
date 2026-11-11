"""Keeper-adopts-pump: the pump's in-process os.execv self-adopt is GONE. The resident `fanops run
--loop` records its running-HEAD SHA in every loop heartbeat (`_heartbeat(code=...)`), and the EXTERNAL
keeper (com.fanops.keeper, StartInterval 120s, `fanops daemon ensure`) compares that SHA to the SHA on
disk and kickstarts the PUMP when they drift.

These tests drive `daemon.ensure(cfg)` with only the launchctl/ps/git process edge stubbed. Decision
inputs are real: heartbeat lines on disk, real `_version_signal` over a fake git state (HEAD starts at
the real checkout SHA; a successful `merge --ff-only` moves it to origin/main), real run flock. No test
fetches or merges a real checkout."""
from __future__ import annotations
import fcntl
import json
import os
import pathlib
import subprocess
import sys

import pytest

from fanops.config import Config
from fanops import daemon, doctor
from fanops.pipeline_run import _lock_path
import fanops

_MERGE = [*daemon._DEPLOY_FF_NO_HOOKS, "merge", "--ff-only", "origin/main"]


def _fake_git(cmd, git, *, ancestor, merge_rc, merge_stderr=None):
    """`git -C <root> <args>` against a two-ref state; any other git argv fails the test."""
    args = cmd[3:]

    def ok(out=""):
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    if args == ["fetch", "origin"]:
        return ok()
    if args == ["rev-parse", "HEAD"]:
        return ok(f"{git['head']}\n")
    if args == ["rev-parse", "HEAD", "origin/main"]:
        return ok(f"{git['head']}\n{git['origin']}\n")
    if args == ["merge-base", "--is-ancestor", "HEAD", "origin/main"]:
        return subprocess.CompletedProcess(cmd, 0 if ancestor else 1, stdout="", stderr="")
    if args == _MERGE:
        if merge_rc == 0:
            git["head"] = git["origin"]
            return ok()
        err = merge_stderr if merge_stderr is not None else (
            "fatal: Not possible to fast-forward, aborting.\n")
        return subprocess.CompletedProcess(cmd, merge_rc, stdout="", stderr=err)
    raise AssertionError(f"unexpected git argv: {cmd}")


def _fake_ensure_run(monkeypatch, tmp_path, *, list_out="", ps_etime=None,
                     head=None, origin=None, ancestor=True, merge_rc=0, merge_stderr=None):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    uid = os.getuid()
    calls: list[list[str]] = []
    git = {"head": head or _deployed_sha()}
    git["origin"] = origin or git["head"]

    def run(cmd, *a, **k):
        cmd = list(cmd)
        calls.append(cmd)
        if cmd[:1] == ["git"]:
            return _fake_git(cmd, git, ancestor=ancestor, merge_rc=merge_rc, merge_stderr=merge_stderr)
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


def _stub_studio_sha(monkeypatch, sha):
    """/_fingerprint answer. None means the resident did not answer — not a code change."""
    monkeypatch.setattr(
        "fanops.daemon_studio._studio_get_fingerprint",
        lambda *a, **k: None if sha is None else {"sha": sha, "pid": 9},
    )


def test_kickstarts_pump_on_drift(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    _loop_hb(cfg, "aaa")
    _plant_studio_plist(tmp_path)
    _stub_studio_sha(monkeypatch, _deployed_sha())        # Studio is already on disk code

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in calls
    assert res["action"] == "kickstart_stale_code"
    assert _studio_kickstart_argv(uid) not in calls


def test_no_kickstart_when_shas_match(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    sha = _deployed_sha()
    assert sha
    _loop_hb(cfg, sha)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in calls
    assert res["action"] == "none"


def test_missing_sha_fails_deploy_check_and_ensure_adopts(tmp_path, monkeypatch):
    """Loaded pump, either SHA missing: the deploy check fails and ensure adopts disk code."""
    cfg, calls, uid = _fake_ensure_run(
        monkeypatch, tmp_path,
        list_out='\t"PID" = 4321;\n',
        ps_etime="27:46:39\n")
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_sync_locked_deps", lambda: (True, ""))

    def loaded(_cfg, _interval):
        return {"loaded": True}

    row = doctor._deploy_code_check(cfg, daemon_status=loaded)
    assert row is not None and row["ok"] is False
    assert "heartbeat SHA" in row["hint"]

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in calls
    assert res["action"] == "kickstart_stale_code"

    monkeypatch.setattr(daemon, "_version_signal", lambda _c: (None, "unavailable"))
    _loop_hb(cfg, "abc123")
    calls.clear()
    row = doctor._deploy_code_check(cfg, daemon_status=loaded)
    assert row is not None and row["ok"] is False
    assert "disk SHA" in row["hint"]

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in calls
    assert res["action"] == "kickstart_stale_code"


def test_missing_sha_adopt_uses_settle_and_run_held(tmp_path, monkeypatch):
    """A missing SHA still waits out a young PID and a held run flock before kickstart."""
    cfg, calls, uid = _fake_ensure_run(
        monkeypatch, tmp_path,
        list_out='\t"PID" = 4321;\n',
        ps_etime="00:10\n")
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_sync_locked_deps", lambda: (True, ""))

    row = doctor._deploy_code_check(cfg, daemon_status=lambda _c, _iv: {"loaded": True})
    assert row is not None and row["ok"] is False

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in calls
    assert res["action"] == "none"

    monkeypatch.setattr(daemon, "_pump_pid_age_s", lambda: (4321, 10**9))
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        res = daemon.ensure(cfg)
        assert _kickstart_argv(uid) not in calls
        assert res["action"] == "none"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_storm_guard_holds(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(
        monkeypatch, tmp_path,
        list_out='\t"PID" = 4321;\n\t"LastExitStatus" = 0;\n',
        ps_etime="00:10\n")
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    _loop_hb(cfg, "aaa")
    _plant_studio_plist(tmp_path)
    _stub_studio_sha(monkeypatch, _deployed_sha())

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


# ── keeper fast-forward: the production checkout advances onto origin/main ─────────────────────

_OLD = "1" * 40
_NEW = "2" * 40
_FF = _MERGE
_NEVER = {"reset", "checkout", "stash", "rebase", "pull", "switch", "restore", "clean"}
_LOADED = {"label": daemon.LABEL, "loaded": True, "action": "none"}


def _git_args(calls):
    return [c[3:] for c in calls if c[:1] == ["git"]]


def _behind(monkeypatch, tmp_path, **kw):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path, head=_OLD, origin=_NEW, **kw)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    monkeypatch.setattr(daemon, "_sync_locked_deps", lambda: (True, ""))
    _loop_hb(cfg, _OLD)                               # the pump runs the checkout's current HEAD
    return cfg, calls, uid


def _hold_run_flock(cfg):
    lp = _lock_path(cfg)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_fast_forward_when_behind_then_adopts_in_same_call(tmp_path, monkeypatch):
    cfg, calls, uid = _behind(monkeypatch, tmp_path)

    res = daemon.ensure(cfg)

    git = _git_args(calls)
    assert ["fetch", "origin"] in git
    assert ["merge-base", "--is-ancestor", "HEAD", "origin/main"] in git
    merge_at = next(i for i, c in enumerate(calls) if c[3:] == _FF and c[:1] == ["git"])
    assert merge_at < calls.index(_kickstart_argv(uid))
    assert daemon._version_signal(cfg)[0] == _NEW
    assert not any(_NEVER & set(a) for a in git)
    assert res == {**_LOADED, "action": "kickstart_stale_code"}


def test_fast_forward_skipped_while_run_flock_held(tmp_path, monkeypatch):
    cfg, calls, uid = _behind(monkeypatch, tmp_path)
    fd = _hold_run_flock(cfg)
    try:
        res = daemon.ensure(cfg)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert _FF not in _git_args(calls)
    assert _kickstart_argv(uid) not in calls
    assert daemon._version_signal(cfg)[0] == _OLD
    assert res == _LOADED


def test_fast_forward_skipped_when_head_diverged(tmp_path, monkeypatch):
    cfg, calls, uid = _behind(monkeypatch, tmp_path, ancestor=False)

    res = daemon.ensure(cfg)

    git = _git_args(calls)
    assert ["merge-base", "--is-ancestor", "HEAD", "origin/main"] in git
    assert _FF not in git
    assert _kickstart_argv(uid) not in calls
    assert res == _LOADED


_BLOCKED = (
    "error: Your local changes to the following files would be overwritten by merge:\n"
    "\t.reports/architecture/derived/MANIFEST.json\n"
    "\t.reports/architecture/derived/side_effects.json\n"
    "Please commit your changes or stash them before you merge.\n"
    "error: The following untracked working tree files would be overwritten by merge:\n"
    "\tsrc/fanops/daemon.py\n"
    "Please move or remove them before you merge.\n"
    "Aborting\n"
)


def test_deploy_ff_no_hooks(tmp_path, monkeypatch):
    """The deploy merge disables dev hooks. post-merge must not regen derived/ on the live tree."""
    cfg, calls, uid = _behind(monkeypatch, tmp_path)

    daemon.ensure(cfg)

    merges = [c for c in calls if c[:1] == ["git"] and "merge" in c]
    assert merges == [["git", "-C", merges[0][2], *_MERGE]]
    assert "core.hooksPath=/dev/null" in merges[0]
    assert not any("post-merge" in arg or arg.endswith(".githooks") for c in calls for arg in c)
    assert _kickstart_argv(uid) in calls


def test_fast_forward_refusal_names_the_files(tmp_path, monkeypatch):
    cfg, calls, uid = _behind(monkeypatch, tmp_path, merge_rc=1, merge_stderr=_BLOCKED)

    with pytest.raises(daemon.DeployRefused, match="deploy refused") as caught:
        daemon.ensure(cfg)

    msg = str(caught.value)
    assert "fail-open" not in msg.lower()
    manifest = msg.index(".reports/architecture/derived/MANIFEST.json")
    side = msg.index(".reports/architecture/derived/side_effects.json")
    daemon_py = msg.index("src/fanops/daemon.py")
    assert manifest < side < daemon_py
    assert _FF in _git_args(calls)
    assert not any(_NEVER & set(a) for a in _git_args(calls))
    assert daemon._version_signal(cfg)[0] == _OLD
    assert _kickstart_argv(uid) not in calls


def test_studio_restarts_only_when_its_fingerprint_changed(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    disk = _deployed_sha()
    _loop_hb(cfg, disk)                               # pump heartbeat is current — not the reason
    _plant_studio_plist(tmp_path)
    _stub_studio_sha(monkeypatch, "0" * 40)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) not in calls
    assert _studio_kickstart_argv(uid) in calls
    assert res["action"] == "kickstart_studio"


def test_unreadable_studio_fingerprint_does_not_restart_on_stale_pump(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    _loop_hb(cfg, "aaa")                              # dead-pump shape: heartbeat is not disk code
    _plant_studio_plist(tmp_path)
    _stub_studio_sha(monkeypatch, None)

    res = daemon.ensure(cfg)

    assert _kickstart_argv(uid) in calls
    assert _studio_kickstart_argv(uid) not in calls
    assert res["action"] == "kickstart_stale_code"


def test_no_fast_forward_when_already_at_origin_main(tmp_path, monkeypatch):
    cfg, calls, uid = _fake_ensure_run(monkeypatch, tmp_path, head=_NEW, origin=_NEW)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "1")
    _loop_hb(cfg, _NEW)

    res = daemon.ensure(cfg)

    git = _git_args(calls)
    assert ["fetch", "origin"] in git
    assert _FF not in git
    assert _kickstart_argv(uid) not in calls
    assert res == _LOADED


def test_fast_forward_waits_out_a_young_pump(tmp_path, monkeypatch):
    """A pump inside the settle window would not be kickstarted, so disk must not move under it."""
    cfg, calls, uid = _behind(monkeypatch, tmp_path, list_out='\t"PID" = 4321;\n', ps_etime="00:10\n")

    res = daemon.ensure(cfg)

    assert _git_args(calls) == [["rev-parse", "HEAD"]]   # _version_signal only: no fetch, no merge
    assert _kickstart_argv(uid) not in calls
    assert res == _LOADED
