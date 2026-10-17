"""MOL-965 WP1: severity is the public health contract; exits read report_is_healthy."""
from __future__ import annotations

from fanops.config import Config
from fanops.health_model import (
    DepHealth,
    HealthReport,
    Severity,
    check_severity,
    overall_severity,
    report_is_healthy,
)
from fanops.doctor import _check


def test_check_constructor_emits_mandatory_severity():
    ok = _check("toolchain", True)
    assert ok["severity"] == "ok" and ok["ok"] is True and ok["hint"] == ""
    fail = _check("toolchain", False, "install it")
    assert fail["severity"] == "fail" and fail["ok"] is False and "install" in fail["hint"]
    warn = _check("auth", severity=Severity.WARN, hint="PATH ≠ login")
    assert warn["severity"] == "warn" and warn["ok"] is True and "PATH" in warn["hint"]
    unknown = _check("sensor", severity=Severity.UNKNOWN, hint="unreadable")
    assert unknown["severity"] == "unknown" and unknown["ok"] is False
    # Public contract is severity — _check must not mint warn/warn_hint soft-lie keys
    assert "warn" not in warn and "warn_hint" not in warn


def test_dep_health_severity_derived_from_ok():
    up = DepHealth("docker", True, "daemon up")
    assert up.severity is Severity.OK
    down = DepHealth("docker", False, "daemon down")
    assert down.severity is Severity.FAIL
    skipped = DepHealth("postiz", True, "skipped (not configured)", severity=Severity.INFO)
    assert skipped.severity is Severity.INFO


def test_report_is_healthy_severity_table():
    ok_rep = HealthReport(checks=[_check("a", True)], notes=[], deps=[DepHealth("d", True, "up")])
    assert report_is_healthy(ok_rep) and overall_severity(ok_rep) is Severity.OK

    warn_rep = HealthReport(
        checks=[_check("path", severity=Severity.WARN, hint="not proof of login")],
        notes=[],
        deps=[DepHealth("d", True, "up")],
    )
    assert report_is_healthy(warn_rep) is True  # non-blocking WARN → exit 0
    assert overall_severity(warn_rep) is Severity.WARN

    fail_rep = HealthReport(
        checks=[_check("gates", severity=Severity.FAIL, hint="stuck")],
        notes=[],
        deps=[DepHealth("d", True, "up")],
    )
    assert report_is_healthy(fail_rep) is False

    unk_rep = HealthReport(
        checks=[_check("sensor", severity=Severity.UNKNOWN, hint="unreadable")],
        notes=[],
        deps=[DepHealth("d", True, "up")],
    )
    assert report_is_healthy(unk_rep) is False  # required UNKNOWN → unhealthy

    dep_down = HealthReport(checks=[_check("a", True)], notes=[], deps=[DepHealth("postiz", False, "down")])
    assert report_is_healthy(dep_down) is False
    assert overall_severity(dep_down) is Severity.FAIL


def test_cmd_doctor_and_health_exit_agree_on_unhealthy(tmp_path, monkeypatch):
    from fanops.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["doctor"]) == 1
    assert main(["health"]) == 1


def test_cmd_init_doctor_clean_tracks_report_is_healthy(tmp_path, monkeypatch):
    from fanops.cli import main
    from fanops.init_flow import run_init

    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    res = run_init(cfg)
    assert res["doctor_clean"] is False and res["failed_checks"] >= 1
    assert main(["init"]) == 1


def test_to_json_dict_includes_severity():
    rep = HealthReport(
        checks=[_check("x", severity=Severity.WARN, hint="attention")],
        notes=[],
        deps=[DepHealth("docker", True, "up")],
    )
    payload = rep.to_json_dict()
    assert payload["healthy"] is True
    assert payload["severity"] == "warn"
    assert payload["checks"][0]["severity"] == "warn"
    assert payload["deps"][0]["severity"] == "ok"


def test_check_severity_helper():
    assert check_severity({"severity": "fail", "ok": False}) is Severity.FAIL
    assert check_severity({"ok": True, "warn": True}) is Severity.WARN  # legacy derive only


def test_as_dict_deps_are_json_safe():
    """doctor_report / as_dict must never embed raw DepHealth (json.dumps TypeError)."""
    import json
    rep = HealthReport(
        checks=[_check("x", True)],
        notes=[],
        deps=[DepHealth("docker", True, "up")],
    )
    payload = rep.as_dict()
    assert isinstance(payload["deps"][0], dict)
    assert payload["deps"][0]["severity"] == "ok"
    json.dumps(payload)  # must not raise

