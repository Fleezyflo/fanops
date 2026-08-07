# tests/test_operator_pause.py — T1.3/MOL-751: the first-class operator brake.
#
# Before this, the only ways to stop the unattended pump were unloading launchd or flipping the AI
# responder to manual — a stop mislabelled as an AI toggle, neither of which is operator-visible or
# restart-surviving. `00_control/paused` is a control FILE: visible on disk, survives restarts, no
# go-live coupling. These tests pin the three things that are easy to regress:
#   1. the marker is honored BEFORE the run lease (so a paused pump never contends for the flock),
#   2. a paused tick STILL heartbeats (a silent pause would freeze the recorded code SHA and the
#      keeper would SIGTERM-kickstart the pump every ~720s after any code change), and
#   3. daemon.status() reports the pause honestly instead of "no successful pass in Ns".
from __future__ import annotations
import json
import subprocess
from datetime import datetime, timezone

import pytest

from fanops.config import Config
from fanops import daemon
from fanops.pipeline_run import paused, set_paused, _paused_path
import fanops.cli as cli


def _records(cfg, stage: str) -> list[dict]:
    """Every run.log record for `stage`. NB get_logger stringifies EVERY field (log.py `_san`), so a
    log-record value is always str — the same reason the pre-existing shape carries published_in_run="0"."""
    if not cfg.log_path.exists():
        return []
    out = []
    for line in cfg.log_path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("stage") == stage:
                out.append(rec)
    return out


# ── the predicate + its writer ───────────────────────────────────────────────────────────────

def test_set_paused_roundtrip_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    assert paused(cfg) is False                      # 00_control does not even exist yet
    set_paused(cfg, True); set_paused(cfg, True)     # twice: proves the mkdir + overwrite path
    assert paused(cfg) is True and _paused_path(cfg).exists()
    set_paused(cfg, False); set_paused(cfg, False)   # twice: proves missing_ok=True, no exception
    assert paused(cfg) is False and not _paused_path(cfg).exists()


# ── the firewall: honored BEFORE the lease ───────────────────────────────────────────────────

def test_run_pass_returns_early_when_paused(tmp_path, mocker):
    # THE firewall. A paused tick must do NO work: not the responder, not advance, and — because the
    # check precedes `with run_lease(cfg)` — not even take the run flock, so `fanops advance` by hand
    # stays unblocked while paused.
    cfg = Config(root=tmp_path)
    adv = mocker.patch.object(cli, "advance")
    resp = mocker.patch.object(cli, "get_responder")
    lease = mocker.patch("fanops.pipeline_run.run_lease")
    set_paused(cfg, True)

    s = cli._cmd_run_pass(cfg, "2026-01-01T00:00:00Z")

    assert s == {"paused": True, "awaiting": {}}     # a dict, NOT None: a pause is not a failure
    adv.assert_not_called()
    resp.assert_not_called()
    lease.assert_not_called()                        # the lease was never even taken
    assert [r["outcome"] for r in _records(cfg, "run")] == ["paused"]


def test_run_pass_runs_normally_when_not_paused(tmp_path, mocker):
    # The negative control for the firewall above: with no marker the pass proceeds as before.
    cfg = Config(root=tmp_path)
    adv = mocker.patch.object(cli, "advance", return_value={"awaiting": {"moments": 0}, "published_in_run": 0})
    mocker.patch.object(cli, "get_responder")

    s = cli._cmd_run_pass(cfg, "2026-01-01T00:00:00Z")

    adv.assert_called()
    assert not (s or {}).get("paused")


def test_paused_return_keeps_the_blocked_gates_note_quiet(tmp_path):
    # `awaiting: {}` is load-bearing: _gates_blocked_note must stay silent on a paused tick, or every
    # pause would emit the LOUD "gates STILL BLOCKED" alarm the operator deliberately caused.
    assert cli._gates_blocked_note({"paused": True, "awaiting": {}}) is None


# ── the anti-kickstart-storm invariant ───────────────────────────────────────────────────────

def test_paused_tick_still_emits_a_loop_heartbeat(tmp_path, capsys):
    # WHY the paused return is a dict and not None: the --loop path only heartbeats on a non-None
    # return. A silent pause would freeze the recorded `code` SHA forever, so after any code change
    # daemon.ensure's drift branch would SIGTERM-kickstart the pump every ~720s for the whole pause.
    cfg = Config(root=tmp_path)
    cli._heartbeat(cfg, {"paused": True}, origin="loop")

    hb = json.loads(capsys.readouterr().out.strip())
    assert hb["paused"] is True and hb["code"]        # stdout carries REAL JSON booleans
    rec = _records(cfg, "heartbeat")[-1]
    assert rec["origin"] == "loop" and rec["code"]
    assert rec["paused"] == "True"                    # run.log values are strings (get_logger._san)
    assert daemon._last_heartbeat_code(cfg) is not None   # the keeper can still read a running SHA


def test_heartbeat_carries_paused_false_when_running(tmp_path, capsys):
    # UNCONDITIONAL key, unlike `origin`: a monitor telling "paused" from "dead" needs it on EVERY
    # line — present only when true would make absence ambiguous between "not paused" and "old code".
    # NB the run.log value is the STRING "False", which is TRUTHY in Python: asserting `not rec["paused"]`
    # here would pass vacuously and prove nothing, so both surfaces are pinned by exact equality.
    cfg = Config(root=tmp_path)
    cli._heartbeat(cfg, {"published_in_run": 0}, origin="loop")

    hb = json.loads(capsys.readouterr().out.strip())
    assert "paused" in hb and hb["paused"] is False
    rec = _records(cfg, "heartbeat")[-1]
    assert rec["paused"] == "False"


# ── the operator verbs ───────────────────────────────────────────────────────────────────────

def test_cmd_pause_and_resume_toggle_the_marker(tmp_path, monkeypatch, capsys):
    cfg = Config(root=tmp_path)
    monkeypatch.setenv("FANOPS_ROOT", str(tmp_path))

    assert cli.main(["pause"]) == 0
    assert _paused_path(cfg).exists()
    assert cli.main(["resume"]) == 0
    assert not _paused_path(cfg).exists()

    # The confirmation routes through get_logger (-> run.log + stderr), NOT a new print(): cli.py's
    # exact-equality _CLI_PRINT_COUNT budget is shared across slices and only one open PR may move it.
    assert [r["outcome"] for r in _records(cfg, "pause")] == ["paused", "resumed"]
    assert "paused" not in capsys.readouterr().out


def test_status_reports_paused_state(tmp_path, capsys):
    cfg = Config(root=tmp_path)
    cli.cmd_status(cfg)
    assert "paused=false" in capsys.readouterr().out
    set_paused(cfg, True)
    cli.cmd_status(cfg)
    assert "paused=true" in capsys.readouterr().out


# ── the daemon verdict ───────────────────────────────────────────────────────────────────────

def _loop_heartbeat(cfg, ts: str) -> None:
    """A fresh loop-origin heartbeat in run.log — mirrors test_daemon.py's rc6 fixture shape."""
    cfg.reports.mkdir(parents=True, exist_ok=True)
    rec = {"ts": ts, "level": "info", "stage": "heartbeat", "unit_id": "-", "outcome": "ok",
           "origin": "loop", "heartbeat": ts, "fanops_version": "0.3.0", "published_in_run": "0",
           "code": "deadbeef"}
    cfg.log_path.write_text(json.dumps(rec, separators=(",", ":")) + "\n")


@pytest.fixture
def _no_launchctl(monkeypatch, tmp_path):
    """No real launchctl, no real ~/Library/LaunchAgents (HOME is repointed) — mirrors test_daemon.py."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(daemon.subprocess, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(
        cmd, 0, '\t"PID" = 4321;\n\t"LastExitStatus" = 0;\n', ""))


def test_daemon_status_reports_paused_verdict(tmp_path, _no_launchctl):
    # test_daemon.py pins the other pass_verdict strings by EXACT equality, so the new first branch
    # needs its own pin or it ships untested. A paused pump completes no passes by design; reporting
    # that as "no successful pass in Ns" is a lie the operator would page on.
    cfg = Config(root=tmp_path)
    _loop_heartbeat(cfg, datetime.now(timezone.utc).isoformat())

    assert daemon.status(cfg, interval=600)["pass_verdict"] == "passes completing"   # control
    set_paused(cfg, True)
    assert daemon.status(cfg, interval=600)["pass_verdict"] == "paused by operator"


def test_paused_verdict_beats_a_stale_heartbeat(tmp_path, _no_launchctl):
    # The branch is FIRST for a reason: a long pause makes the last completed pass arbitrarily old,
    # and "no successful pass in 7200s" would send the operator hunting a crash-loop they caused.
    cfg = Config(root=tmp_path)
    old = (datetime.now(timezone.utc).timestamp() - 7200)
    _loop_heartbeat(cfg, datetime.fromtimestamp(old, timezone.utc).isoformat())

    assert daemon.status(cfg, interval=600)["pass_verdict"].startswith("no successful pass in")   # control
    set_paused(cfg, True)
    assert daemon.status(cfg, interval=600)["pass_verdict"] == "paused by operator"
