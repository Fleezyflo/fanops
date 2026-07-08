"""Unit tests for scripts/orchestrate.py — the one-command front door."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / ".cursor" / "hooks"))
import orchestrate as orch  # noqa: E402
import orchestration_gate as _og_hook  # noqa: E402  (verify engage() actually flips the gate on)


def test_engage_creates_marker_and_activates_gate(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_ORCHESTRATED", raising=False)
    assert _og_hook.is_active(tmp_path) is False
    marker = orch.engage(tmp_path)
    assert marker.exists()
    assert _og_hook.is_active(tmp_path) is True     # engaging really turns enforcement on


def test_start_dispatch_engages_then_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FANOPS_ORCHESTRATED", raising=False)
    calls = {}
    monkeypatch.setattr(orch.repo_sweep, "main", lambda argv: calls.update({"argv": argv}) or 0)
    rc = orch.main(["start", "--root", str(tmp_path), "--repo", "o/r"])
    assert rc == 0
    assert (tmp_path / ".orchestration" / "state" / "ACTIVE").exists()
    assert "--require-pristine" not in calls["argv"]           # start shows status, not the gate
    assert "ENGAGED" in capsys.readouterr().out


def test_done_dispatch_uses_require_pristine(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(orch.repo_sweep, "main", lambda argv: seen.update({"argv": argv}) or 3)
    rc = orch.main(["done", "--root", str(tmp_path), "--repo", "o/r"])
    assert rc == 3
    assert "--require-pristine" in seen["argv"]                 # done IS the gate


def test_status_dispatch_is_plain_report(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(orch.repo_sweep, "main", lambda argv: seen.update({"argv": argv}) or 0)
    orch.main(["status", "--root", str(tmp_path), "--repo", "o/r"])
    assert "--require-pristine" not in seen["argv"]
