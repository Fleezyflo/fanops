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


def test_project_daemon_strip_overlays_heartbeat():
    snap = {"loaded": True, "installed": True, "interval": 600, "verdict": "stale-seed"}
    out = project_daemon_strip(snap, age=12.0, stale=False, pending_gates=2, run_line="run=idle")
    assert out["verdict"] == "alive"
    assert out["heartbeat_age_s"] == 12.0
    assert out["pending_gates"] == 2
    assert "run_line" not in out

    stale = project_daemon_strip(snap, age=9999.0, stale=True, pending_gates=None, run_line="run=stage")
    assert "stale" in stale["verdict"]
    assert stale["run_line"] == "run=stage"


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


def test_golive_status_uses_build_health_report_not_doctor_report(tmp_path, monkeypatch):
    """Go-Live readiness must call the one constructor + projector — not doctor_report."""
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    import fanops.health_model as hm
    from fanops.studio import views

    seen = {"build": 0}

    def _fake_build(*a, **k):
        seen["build"] += 1
        return HealthReport(
            checks=[_check("accounts valid", True), _check(HALF_LIVE_CHECK_LABEL, True)],
            notes=["from-constructor"],
            deps=[],
        )

    monkeypatch.setattr(hm, "build_health_report", _fake_build)
    # If golive still imported doctor_report as the assembly path, this would not matter —
    # assert the constructor was hit and notes flowed through the projector.
    st = views.golive_status(cfg)
    assert seen["build"] == 1
    assert st.notes == ["from-constructor"]
    assert st.half_live is False
