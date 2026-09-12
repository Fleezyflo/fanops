"""MOL-965 WP2: strip / Go-Live / daemon / metrics project from one HealthReport."""
from __future__ import annotations

from fanops.config import Config
from fanops.doctor import _check
from fanops.health_model import (
    HALF_LIVE_CHECK_LABEL,
    DepHealth,
    HealthReport,
    Severity,
    half_live_state,
    project_daemon_slice,
    project_daemon_strip,
    project_deps_from_rows,
    project_golive_readiness,
    project_half_live,
    project_prometheus_health,
    project_strip_health,
)


def test_half_live_state_is_the_one_compute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postizz")
    (tmp_path / "accounts.json").write_text(
        '{"accounts":[{"handle":"@ig","account_id":"1","platforms":["instagram"],"status":"active"}]}'
    )
    cfg = Config(root=tmp_path)
    hl = half_live_state(cfg)
    assert hl.is_half_live is True and "postizz" in hl.hint


def test_project_half_live_from_report_matches_check():
    fail = HealthReport(
        checks=[_check(HALF_LIVE_CHECK_LABEL, False, "LIVE flag set but nothing routes live — FANOPS_POSTER=x")],
        notes=[],
    )
    half, hint = project_half_live(fail)
    assert half is True and "FANOPS_POSTER=x" in hint

    ok = HealthReport(checks=[_check(HALF_LIVE_CHECK_LABEL, True)], notes=[])
    assert project_half_live(ok) == (False, "")

    dry = HealthReport(checks=[_check("accounts valid", True)], notes=[])
    assert project_half_live(dry) == (False, "")  # no live-route check → not half-live


def test_project_golive_readiness_is_pure_of_report():
    rep = HealthReport(
        checks=[
            _check(HALF_LIVE_CHECK_LABEL, False, "half live hint"),
            _check("publish daemon alive + queue draining (heartbeat + past-due backlog)", False, "dead"),
        ],
        notes=["note-a"],
        deps=[DepHealth("docker", True, "up")],
    )
    ready = project_golive_readiness(rep)
    assert ready["checks"] is rep.checks
    assert ready["notes"] == ["note-a"]
    assert ready["half_live"] is True and ready["half_live_hint"] == "half live hint"
    assert ready["healthy"] is False
    assert ready["severity"] is Severity.FAIL
    assert ready["daemon_slice"]["ok"] is False
    assert ready["daemon_slice"]["severity"] == "fail"


def test_project_strip_health_shares_half_live_with_golive():
    rep = HealthReport(
        checks=[_check(HALF_LIVE_CHECK_LABEL, False, "shared hint")],
        notes=[],
    )
    assert project_strip_health(rep)["half_live_hint"] == project_golive_readiness(rep)["half_live_hint"]


def test_project_daemon_strip_fresh_heartbeat_is_alive():
    snap = {"loaded": True, "installed": True, "interval": 600, "verdict": "stale-seed"}
    out = project_daemon_strip(snap, age=12.0, stale=False, pending_gates=2, run_line="run=idle")
    assert out["verdict"] == "alive"
    assert out["heartbeat_age_s"] == 12.0
    assert out["pending_gates"] == 2
    assert "run_line" not in out


def test_project_daemon_strip_live_activity_wins_stale_heartbeat():
    snap = {"loaded": True, "installed": True, "interval": 600, "verdict": "stale-seed"}
    mid = project_daemon_strip(
        snap, age=9999.0, stale=True, pending_gates=None,
        run_line="run=1600 stage=transcribe", alive_mid=True,
    )
    assert mid["verdict"] == "alive"
    assert mid["run_line"] == "run=1600 stage=transcribe"
    assert mid["heartbeat_age_s"] == 9999.0
    empty = project_daemon_strip({}, age=9999.0, stale=True, run_line="run=1 stage=x", alive_mid=True)
    assert empty["loaded"] is True and empty["verdict"] == "alive"


def test_project_daemon_strip_ignores_frozen_snapshot_verdict():
    snap = {"loaded": True, "installed": True, "interval": 600, "verdict": "alive"}
    dead = project_daemon_strip(snap, age=9999.0, stale=True, run_line=None, alive_mid=False)
    assert "stale" in dead["verdict"]


def test_project_daemon_strip_stale_without_activity():
    snap = {"loaded": True, "installed": True, "interval": 600, "verdict": "stale-seed"}
    dead = project_daemon_strip(snap, age=9999.0, stale=True, pending_gates=None, run_line=None)
    assert "stale" in dead["verdict"]


def test_project_daemon_slice_from_report():
    rep = HealthReport(
        checks=[_check("publish daemon alive + queue draining (heartbeat + past-due backlog)", True)],
        notes=[],
    )
    assert project_daemon_slice(rep)["ok"] is True
    assert project_daemon_slice(HealthReport(checks=[], notes=[])) is None


def test_project_deps_from_rows():
    rows = [{"name": "postiz", "ok": False, "detail": "down", "severity": "fail"}]
    deps = project_deps_from_rows(rows)
    assert len(deps) == 1 and deps[0].name == "postiz" and deps[0].ok is False
    assert deps[0].severity is Severity.FAIL


def test_project_prometheus_health_gauges():
    rep = HealthReport(
        checks=[],
        notes=[],
        deps=[DepHealth("docker", True, "up"), DepHealth("postiz", False, "down")],
    )
    lines = project_prometheus_health(rep, heartbeat=(3.5, False, 600))
    body = "\n".join(lines)
    assert 'fanops_dep_up{dep="docker"} 1' in body
    assert 'fanops_dep_up{dep="postiz"} 0' in body
    assert "fanops_daemon_heartbeat_age_seconds 3.5" in body
    assert "fanops_daemon_heartbeat_stale 0" in body


def test_golive_status_unhealthy_on_empty_root(tmp_path, monkeypatch):
    """Go-Live readiness uses the real constructor — empty root is not doctor-clean."""
    monkeypatch.chdir(tmp_path)
    from fanops.studio import views
    st = views.golive_status(Config(root=tmp_path))
    assert st.checks and any(not c.get("ok", True) for c in st.checks)


def test_build_system_strip_half_live_from_live_flag_without_route(tmp_path, monkeypatch):
    """Strip half_live comes from half_live_state on real cfg (LIVE + nothing routes)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("FANOPS_POSTER", "postizz")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts":[{"handle":"@ig","account_id":"1","platforms":["instagram"],"status":"active"}]}'
    )
    from fanops.studio import views
    strip = views.build_system_strip(cfg)
    assert strip["half_live"] is True
    assert "postizz" in (strip.get("half_live_hint") or "")


def test_observe_build_health_report_skips_live_postiz_and_daemon_status(tmp_path, monkeypatch):
    """probe_policy=observe must not HTTP or launchctl — snapshot + local cfg only."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("POSTIZ_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(
        '{"accounts":[{"handle":"@ig","account_id":"1","platforms":["instagram"],"status":"active"}]}'
    )
    cfg.control.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    import json
    from fanops.timeutil import iso_z
    cfg.deps_health_path.write_text(json.dumps({
        "checked_at": iso_z(datetime.now(timezone.utc)),
        "deps": [
            {"name": "docker", "ok": True, "detail": "up", "status_code": None},
            {"name": "postiz", "ok": False, "detail": "down", "status_code": 502},
            {"name": "zernio", "ok": True, "detail": "skipped", "status_code": None},
        ],
    }))
    http = []

    def fake_get(*a, **k):
        http.append(a)
        raise AssertionError("observe must not HTTP")

    monkeypatch.setattr("requests.get", fake_get)
    from fanops.health_model import build_health_report, project_strip_health
    rep = build_health_report(cfg, probe_policy="observe")
    strip = project_strip_health(rep)
    assert http == []
    assert "half_live" in strip

