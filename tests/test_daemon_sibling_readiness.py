"""MOL-355: poll-timer siblings (postiz-reaper, media-sync) share M2-C readiness-alarm coverage."""
from __future__ import annotations
import plistlib, subprocess

import pytest

from fanops.config import Config
from fanops import daemon, doctor


def _fake_launchctl(**spec):
    def run(cmd, *a, **k):
        label = cmd[2] if len(cmd) > 2 and cmd[1] == "list" else ""
        verb = cmd[1] if len(cmd) > 1 else ""
        key = label if label in spec else verb
        rc, out = spec.get(key, spec.get(verb, (0, "")))
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
    return run


def _write_sibling_plist(tmp_path, monkeypatch, label: str):
    monkeypatch.setenv("HOME", str(tmp_path))
    pp = daemon.sibling_plist_path(label)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps({"Label": label, "StartInterval": daemon.SIBLING_POLL_INTERVAL_S}))


@pytest.mark.parametrize("label,short", [(s["label"], s["short"]) for s in daemon.SIBLING_POLL_AGENTS])
def test_sibling_status_alarm_when_plist_on_disk_but_not_loaded(tmp_path, monkeypatch, label, short):
    _write_sibling_plist(tmp_path, monkeypatch, label)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(**{label: (1, "")}))

    rep = daemon.sibling_agent_status(label, short=short)

    assert rep["installed"] is True
    assert rep["loaded"] is False
    assert rep["alarm"] is True
    assert rep["verdict"] == daemon._VERDICT_UNLOADED_ALARM
    assert rep["poll_interval_s"] == 300


@pytest.mark.parametrize("label,short", [(s["label"], s["short"]) for s in daemon.SIBLING_POLL_AGENTS])
def test_sibling_status_not_installed_when_no_plist(tmp_path, monkeypatch, label, short):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(**{label: (1, "")}))

    rep = daemon.sibling_agent_status(label, short=short)

    assert rep["installed"] is False
    assert rep["loaded"] is False
    assert rep["alarm"] is False
    assert rep["verdict"] == "not installed"


@pytest.mark.parametrize("label,short", [(s["label"], s["short"]) for s in daemon.SIBLING_POLL_AGENTS])
def test_sibling_status_loaded_when_plist_and_launchctl_ok(tmp_path, monkeypatch, label, short):
    _write_sibling_plist(tmp_path, monkeypatch, label)
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(**{label: (0, '\t"PID" = 9;\n')}))

    rep = daemon.sibling_agent_status(label, short=short)

    assert rep["installed"] is True
    assert rep["loaded"] is True
    assert rep["alarm"] is False
    assert rep["verdict"] == "loaded"


def test_sibling_agents_status_covers_fleet(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    labels = {s["label"] for s in daemon.SIBLING_POLL_AGENTS}
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl())

    reps = daemon.sibling_agents_status()

    assert {r["label"] for r in reps} == labels
    assert all(r["poll_interval_s"] == 300 for r in reps)


def test_poll_timer_rationale_documents_why_not_keepalive():
    assert "poll-timer" in daemon.SIBLING_POLL_TIMERS_RATIONALE.lower()
    assert "postiz-reaper" in daemon.SIBLING_POLL_TIMERS_RATIONALE
    assert "media-sync" in daemon.SIBLING_POLL_TIMERS_RATIONALE
    assert "cron-style" in daemon.SIBLING_POLL_TIMERS_RATIONALE
    assert "300s" in daemon.SIBLING_POLL_TIMERS_RATIONALE


def test_doctor_fails_on_unloaded_sibling_plist_alarm(tmp_path, monkeypatch):
    label = "com.fanops.postiz-reaper"
    _write_sibling_plist(tmp_path, monkeypatch, label)
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(**{label: (1, ""), "list": (1, "")}))

    rep = doctor.doctor_report(cfg)
    chk = next((c for c in rep["checks"] if "Postiz reaper" in c["label"]), None)

    assert chk is not None and chk["ok"] is False
    assert "NOT loaded" in chk["hint"]


def test_studio_daemon_health_surfaces_unloaded_sibling_alarm(tmp_path, monkeypatch):
    from fanops.studio.app import create_app

    label = "com.fanops.media-sync"
    _write_sibling_plist(tmp_path, monkeypatch, label)
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(daemon.sys, "platform", "darwin")
    monkeypatch.setattr(daemon.subprocess, "run", _fake_launchctl(**{label: (1, ""), "list": (1, "")}))
    app = create_app(cfg)
    with app.test_client() as client:
        html = client.get("/home/daemon-health").data.decode()
    assert "media-sync" in html
    assert "NOT loaded" in html
    assert "poll-timer" in html
