"""MOL-960 Wave B: unattended exit contract — stuck gates → 1; pause → 0; progress warn → unhealthy."""
from fanops.health_model import HealthReport, DepHealth, report_is_healthy


def test_cmd_run_gates_blocked_exits_1(tmp_path, monkeypatch):
    from fanops import cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_check_accounts", lambda cfg: 0)
    monkeypatch.setattr(cli, "_check_preflight", lambda cfg: 0)
    monkeypatch.setattr(cli, "_cmd_run_pass",
                        lambda cfg, base_time: {"awaiting": {"moments": 1, "captions": 0}})
    monkeypatch.setattr(cli, "_heartbeat", lambda *a, **k: None)
    assert cli.main(["run"]) == 1


def test_cmd_run_pause_exits_0(tmp_path, monkeypatch):
    from fanops import cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_check_accounts", lambda cfg: 0)
    monkeypatch.setattr(cli, "_check_preflight", lambda cfg: 0)
    monkeypatch.setattr(cli, "_cmd_run_pass",
                        lambda cfg, base_time: {"paused": True, "awaiting": {}})
    monkeypatch.setattr(cli, "_heartbeat", lambda *a, **k: None)
    assert cli.main(["run"]) == 0


def test_cmd_run_converged_exits_0(tmp_path, monkeypatch):
    from fanops import cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_check_accounts", lambda cfg: 0)
    monkeypatch.setattr(cli, "_check_preflight", lambda cfg: 0)
    monkeypatch.setattr(cli, "_cmd_run_pass",
                        lambda cfg, base_time: {"awaiting": {"moments": 0, "captions": 0}})
    monkeypatch.setattr(cli, "_heartbeat", lambda *a, **k: None)
    assert cli.main(["run"]) == 0


def test_progress_blocking_sensor_makes_report_unhealthy():
    """Progress-blocking operational check (ok=False) flips report_is_healthy — not warn-only."""
    rep = HealthReport(
        checks=[{"label": "no stale agent gates (responder answering)", "ok": False, "warn": True}],
        notes=[],
        deps=[DepHealth("docker", True, "up")],
    )
    assert report_is_healthy(rep) is False
