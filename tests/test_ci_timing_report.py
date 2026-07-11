"""Unit tests for scripts/ci_timing_report.py — pytest stdout parser + JSON merge."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_timing_report.py"


def _import():
    sys.path.insert(0, str(ROOT / "scripts"))
    import ci_timing_report as m  # noqa: PLC0415
    return m


def test_parse_passed_only():
    m = _import()
    out = "bringing up nodes...\n\n3944 passed in 115.23s\n"
    assert m.parse_pytest_summary(out) == (3944, 115.23)


def test_parse_passed_with_skipped():
    m = _import()
    out = "64 passed, 2 skipped in 91.0s\n"
    assert m.parse_pytest_summary(out) == (64, 91.0)


def test_parse_skipped_only():
    m = _import()
    out = "s                                                                        [100%]\n1 skipped in 0.26s\n"
    assert m.parse_pytest_summary(out) == (0, 0.26)


def test_parse_xdist_noise():
    m = _import()
    out = """bringing up nodes...
bringing up nodes...

................................................................         [100%]
============================= slowest 25 durations =============================

(25 durations < 1s hidden.)
64 passed in 0.57s
"""
    assert m.parse_pytest_summary(out) == (64, 0.57)


def test_parse_passed_with_warning_and_paren_time():
    m = _import()
    out = "4226 passed, 1 warning in 83.07s (0:01:23)\n"
    assert m.parse_pytest_summary(out) == (4226, 83.07)


def test_parse_passed_with_deselected():
    m = _import()
    out = "17 passed, 4234 deselected in 35.91s\n"
    assert m.parse_pytest_summary(out) == (17, 35.91)


def test_step_fields_unit():
    m = _import()
    assert m.step_fields("unit", 3944, 115.23, xdist=True) == {"unit_pytest_s": 115.23, "test_count": 3944, "xdist": True}


def test_step_fields_e2e_integration():
    m = _import()
    assert m.step_fields("e2e_integration", 12, 27.5) == {"e2e_integration_s": 27.5}


def test_step_fields_e2e_slow():
    m = _import()
    assert m.step_fields("e2e_slow", 3, 6.1) == {"e2e_slow_s": 6.1}


def test_merge_timing_parts(tmp_path):
    m = _import()
    (tmp_path / "unit").mkdir()
    (tmp_path / "e2e").mkdir()
    (tmp_path / "unit" / "ci-timing.partial.json").write_text(json.dumps({"sha": "abc", "unit_pytest_s": 91.0, "test_count": 100, "xdist": True}))
    (tmp_path / "e2e" / "ci-timing.partial.json").write_text(json.dumps({"e2e_integration_s": 27.0, "e2e_slow_s": 6.0}))
    merged = m.merge_timing_parts(tmp_path, sha="def")
    assert merged == {"sha": "def", "unit_pytest_s": 91.0, "test_count": 100, "xdist": True,
                      "e2e_integration_s": 27.0, "e2e_slow_s": 6.0}


def test_cli_report_writes_summary_and_json(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("3944 passed in 115.23s\n")
    summary = tmp_path / "summary.md"
    jout = tmp_path / "timing.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--step", "unit", "--log", str(log), "--sha", "abc123",
         "--xdist", "--summary-file", str(summary), "--json-out", str(jout)],
        check=True, cwd=ROOT,
    )
    assert "115.23" in summary.read_text()
    assert "3944" in summary.read_text()
    data = json.loads(jout.read_text())
    assert data["unit_pytest_s"] == 115.23
    assert data["test_count"] == 3944
    assert data["sha"] == "abc123"
    assert data["xdist"] is True
