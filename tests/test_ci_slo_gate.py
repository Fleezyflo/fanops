"""Unit tests for scripts/ci_slo_gate.py — blocking unit pytest SLO gate."""
from __future__ import annotations
import io
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_slo_gate.py"


def _import():
    sys.path.insert(0, str(ROOT / "scripts"))
    import ci_slo_gate as m  # noqa: PLC0415
    return m


def test_within_budget_passes():
    m = _import()
    assert m.check_budget(115.23, 135.0) is None


def test_over_budget_returns_message():
    m = _import()
    msg = m.check_budget(136.0, 135.0)
    assert msg is not None
    assert "136" in msg
    assert "135" in msg
    assert "budget" in msg.lower()


def test_parse_log_file(tmp_path):
    m = _import()
    log = tmp_path / "pytest.log"
    log.write_text("3944 passed in 115.23s\n")
    assert m.parse_wall_seconds(log) == 115.23


def test_parse_stdin():
    m = _import()
    assert m.parse_wall_seconds(io.StringIO("64 passed in 91.0s\n")) == 91.0


def test_run_gate_passes_within_budget(tmp_path, monkeypatch):
    m = _import()
    log = tmp_path / "pytest.log"
    log.write_text("3944 passed in 115.23s\n")
    monkeypatch.setenv("CI_UNIT_PYTEST_BUDGET_S", "135")
    assert m.run_gate(log, budget_s=135.0) == 0


def test_run_gate_fails_over_budget(tmp_path, monkeypatch):
    m = _import()
    log = tmp_path / "pytest.log"
    log.write_text("3944 passed in 140.01s\n")
    monkeypatch.setenv("CI_UNIT_PYTEST_BUDGET_S", "135")
    assert m.run_gate(log, budget_s=135.0) == 1


def test_cli_passes(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("3944 passed in 115.23s\n")
    env = {**os.environ, "CI_UNIT_PYTEST_BUDGET_S": "135"}
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--log", str(log)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_cli_fails_over_budget(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("3944 passed in 150.0s\n")
    env = {**os.environ, "CI_UNIT_PYTEST_BUDGET_S": "140"}
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--log", str(log)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "150" in r.stderr or "150" in r.stdout
    assert "140" in r.stderr or "140" in r.stdout


def test_cli_stdin(tmp_path):
    env = {**os.environ, "CI_UNIT_PYTEST_BUDGET_S": "135"}
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="3944 passed in 115.23s\n",
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0


def test_cli_missing_budget_exits_2():
    env = {k: v for k, v in os.environ.items() if k != "CI_UNIT_PYTEST_BUDGET_S"}
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--log", "/dev/null"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 2
