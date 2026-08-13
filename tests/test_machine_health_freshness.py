"""MOL-965 WP3: snapshot TTL/UNKNOWN folds into HealthReport — never green zeros / hidden banners."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fanops.config import Config
from fanops.doctor import _check, _daemon_liveness_check
from fanops.health import SnapshotFreshness
from fanops.health_model import (
    STRIP_METRICS_CHECK_LABEL,
    HealthReport,
    Severity,
    build_health_report,
    check_severity,
    deps_from_snapshot,
    overall_severity,
    postiz_doctor_check,
    project_strip_health,
    report_is_healthy,
    snapshot_daemon_status,
    snapshot_postiz_probe,
    strip_metrics_freshness_check,
)
from fanops.timeutil import iso_z


def _cfg(tmp_path, monkeypatch) -> Config:
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    return cfg


def test_strip_metrics_freshness_check_missing_is_unknown(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    c = strip_metrics_freshness_check(cfg)
    assert c["label"] == STRIP_METRICS_CHECK_LABEL
    assert check_severity(c) is Severity.UNKNOWN
    assert "missing" in (c.get("hint") or "")


def test_strip_metrics_freshness_check_stale_is_unknown(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.strip_metrics_path.write_text(json.dumps({
        "checked_at": "2020-01-01T00:00:00Z",
        "blocked_gates": 0,
        "recoverable_sources": 0,
        "errored_first_id": None,
    }))
    c = strip_metrics_freshness_check(cfg)
    assert check_severity(c) is Severity.UNKNOWN
    assert "stale" in (c.get("hint") or "")


def test_project_strip_health_surfaces_strip_metrics_unknown():
    unk = HealthReport(
        checks=[_check(STRIP_METRICS_CHECK_LABEL, severity="unknown", hint="strip metrics snapshot missing")],
        notes=[],
    )
    out = project_strip_health(unk)
    assert out["strip_metrics_unknown"] is True
    assert out["healthy"] is False
    assert overall_severity(unk) is Severity.UNKNOWN

    ok = HealthReport(checks=[_check(STRIP_METRICS_CHECK_LABEL, True)], notes=[])
    assert project_strip_health(ok)["strip_metrics_unknown"] is False


def test_deps_from_snapshot_missing_is_unknown_severity(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    deps = deps_from_snapshot(cfg)
    assert len(deps) == 1
    assert deps[0].severity is Severity.UNKNOWN
    assert "unknown" in deps[0].detail


def test_snapshot_daemon_status_marks_freshness(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    st = snapshot_daemon_status(cfg, 600)
    assert st["snapshot_freshness"] == SnapshotFreshness.MISSING.value
    assert st["verdict"].startswith("snapshot ")


def test_daemon_liveness_snapshot_unknown_is_severity_unknown_not_no_heartbeat(tmp_path, monkeypatch):
    """WP3: stale/missing daemon snapshot → UNKNOWN, not FAIL 'no heartbeat' lie."""
    cfg = _cfg(tmp_path, monkeypatch)

    def _reader(_c, _iv):
        return snapshot_daemon_status(cfg, 600)

    c = _daemon_liveness_check(cfg, status_reader=_reader)
    assert check_severity(c) is Severity.UNKNOWN
    hint = (c.get("hint") or "").lower()
    assert "snapshot" in hint
    assert "no daemon heartbeat" not in hint


def test_postiz_snapshot_unknown_is_severity_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = _cfg(tmp_path, monkeypatch)
    # No deps snapshot → snapshot_postiz_probe unknown
    h = snapshot_postiz_probe(cfg)
    assert h.healthy is False and "snapshot" in (h.hint or "").lower()
    chk = postiz_doctor_check(cfg, probe=snapshot_postiz_probe)
    assert chk is not None
    assert check_severity(chk) is Severity.UNKNOWN


def test_observe_missing_snapshots_report_unhealthy_not_green(tmp_path, monkeypatch):
    """Required observe signals missing → overall UNKNOWN/FAIL class; strip never healthy+zeros."""
    cfg = _cfg(tmp_path, monkeypatch)
    (tmp_path / "accounts.json").write_text('{"accounts":[]}')
    rep = build_health_report(cfg, probe_policy="observe")
    strip = project_strip_health(rep)
    assert strip["strip_metrics_unknown"] is True
    assert report_is_healthy(rep) is False
    assert overall_severity(rep) in (Severity.UNKNOWN, Severity.FAIL)
    # deps UNKNOWN contribution present
    assert any(d.severity is Severity.UNKNOWN for d in rep.deps)
    # strip metrics check present as UNKNOWN
    assert any(
        c.get("label") == STRIP_METRICS_CHECK_LABEL and check_severity(c) is Severity.UNKNOWN
        for c in rep.checks
    )


def test_observe_fresh_strip_metrics_clears_unknown_flag(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    (tmp_path / "accounts.json").write_text('{"accounts":[]}')
    now = iso_z(datetime.now(timezone.utc))
    cfg.strip_metrics_path.write_text(json.dumps({
        "checked_at": now,
        "blocked_gates": 2,
        "recoverable_sources": 0,
        "errored_first_id": None,
    }))
    cfg.deps_health_path.write_text(json.dumps({
        "checked_at": now,
        "deps": [{"name": "docker", "ok": True, "detail": "up"}],
    }))
    cfg.daemon_strip_path.write_text(json.dumps({
        "checked_at": now,
        "installed": False, "loaded": False, "verdict": "not installed",
        "heartbeat_age_s": None, "interval": 600,
    }))
    rep = build_health_report(cfg, probe_policy="observe")
    strip = project_strip_health(rep)
    assert strip["strip_metrics_unknown"] is False
    assert any(
        c.get("label") == STRIP_METRICS_CHECK_LABEL and check_severity(c) is Severity.OK
        for c in rep.checks
    )


def test_build_system_strip_constructor_freshness_authoritative(tmp_path, monkeypatch):
    """Soft steer: HealthReport freshness wins — missing metrics → unknown, not calm blocked=0."""
    cfg = _cfg(tmp_path, monkeypatch)
    from fanops.studio import views
    strip = views.build_system_strip(cfg)
    assert strip["strip_metrics_unknown"] is True
    assert strip["blocked_gates"] is None
