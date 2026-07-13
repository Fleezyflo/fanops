"""MOL-352: fanops run --loop outer sleep loop over advance()."""
import json
import pytest
from fanops.cli import main


def _idle_summary():
    return {
        "sources": 0, "moments": 0, "clips": 0, "posts": 0, "published": 0, "failed": 0,
        "published_in_run": 0, "last_published_age_hours": None,
        "needs_reconcile": 0, "holds": 0, "hook_burn_failed": 0, "frames_unread": 0, "errors": 0,
        "awaiting": {"moments": 0, "moment_hooks": 0, "captions": 0},
    }


def _setup_accounts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_AUTO_ADOPT", "0")   # these exercise loop iteration, not self-adopt re-exec
    from fanops.config import Config
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps(
        {"accounts": [{"handle": "@x", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))


def _stub_run(mocker, cli, *, advance_side_effect=None, summary=None):
    summary = summary or _idle_summary()
    if advance_side_effect is None:
        mocker.patch.object(cli, "advance", return_value=summary)
    else:
        mocker.patch.object(cli, "advance", side_effect=advance_side_effect)
    mocker.patch.object(cli, "get_responder",
                        return_value=type("_R", (), {"answer_pending": lambda self, c: None})())
    # _heartbeat now records the running-code SHA; stub it so these hermetic loop tests never spawn the
    # `git rev-parse` subprocess (the CI unit job is subprocess-free) — it also makes the per-tick loop
    # deterministic. The real snapshot-once path is covered by test_self_adopt/test_daemon.
    mocker.patch.object(cli, "_running_code_sha", return_value="testsha")


def _stop_loop_after(mocker, n):
    sleeps = []
    def fake_sleep(sec):
        sleeps.append(sec)
        if len(sleeps) >= n:
            raise KeyboardInterrupt
    mocker.patch("fanops.cli.time.sleep", side_effect=fake_sleep)
    return sleeps


def _heartbeat_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if '"heartbeat"' in ln]


def test_loop_invokes_advance_n_times(tmp_path, monkeypatch, mocker):
    _setup_accounts(tmp_path, monkeypatch)
    import fanops.cli as cli
    n = 3
    _stub_run(mocker, cli)
    spy = cli.advance
    _stop_loop_after(mocker, n)
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--loop", "--interval", "60s"])
    assert spy.call_count == n


def test_loop_fresh_base_time_each_iteration(tmp_path, monkeypatch, mocker):
    _setup_accounts(tmp_path, monkeypatch)
    import fanops.cli as cli
    base_times = []
    stamps = iter(f"2026-07-09T12:00:{i:02d}Z" for i in range(10))
    mocker.patch.object(cli, "_fresh_run_base_time", side_effect=lambda: next(stamps))
    def track_base_time(cfg, *, base_time):
        base_times.append(base_time)
        return _idle_summary()
    _stub_run(mocker, cli, advance_side_effect=track_base_time)
    n = 3
    _stop_loop_after(mocker, n)
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--loop", "--interval", "90s"])
    assert len(base_times) == n
    assert base_times == ["2026-07-09T12:00:00Z", "2026-07-09T12:00:01Z", "2026-07-09T12:00:02Z"]


def test_loop_exception_in_one_iteration_continues(tmp_path, monkeypatch, mocker, capsys):
    _setup_accounts(tmp_path, monkeypatch)
    import fanops.cli as cli
    calls = []
    def boom_on_second(cfg, *, base_time):
        calls.append(base_time)
        if len(calls) == 2:
            raise RuntimeError("transient publish fault")
        return _idle_summary()
    _stub_run(mocker, cli, advance_side_effect=boom_on_second)
    n = 3
    _stop_loop_after(mocker, n)
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--loop", "--interval", "60s"])
    assert len(calls) == 3
    err = capsys.readouterr().err
    assert "run halted" in err and "RuntimeError" in err


def test_loop_emits_heartbeat_each_iteration(tmp_path, monkeypatch, mocker, capsys):
    _setup_accounts(tmp_path, monkeypatch)
    import fanops.cli as cli
    _stub_run(mocker, cli)
    n = 3
    _stop_loop_after(mocker, n)
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--loop", "--interval", "60s"])
    assert len(_heartbeat_lines(capsys.readouterr().out)) == n


def test_loop_stays_resident_across_idle_passes(tmp_path, monkeypatch, mocker):
    # Must NOT exit after one idle tick — sleep-and-continue across ≥2 idle iterations.
    _setup_accounts(tmp_path, monkeypatch)
    import fanops.cli as cli
    _stub_run(mocker, cli)
    sleeps = _stop_loop_after(mocker, 2)
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--loop", "--interval", "60s"])
    assert len(sleeps) >= 2
    assert cli.advance.call_count >= 2


def test_loop_reloads_env_from_disk_each_iteration(tmp_path, monkeypatch, mocker):
    """B01 C1: resident loop must pick up .env disk writes without process restart."""
    _setup_accounts(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text("FANOPS_LIVE=1\nFANOPS_RESPONDER=manual\n")
    monkeypatch.setenv("FANOPS_LIVE", "1")                 # stale process env — disk flip must override
    import fanops.cli as cli
    config_calls: list = []
    real_config = cli.Config
    def tracking_config(*a, **k):
        c = real_config(*a, **k)
        config_calls.append(c)
        return c
    mocker.patch.object(cli, "Config", side_effect=tracking_config)
    is_live_seen: list[bool] = []
    def track_live(cfg, *, base_time):
        is_live_seen.append(cfg.is_live)
        if len(is_live_seen) == 1:
            env.write_text("FANOPS_LIVE=0\nFANOPS_RESPONDER=manual\n")   # disk-only flip
        return _idle_summary()
    _stub_run(mocker, cli, advance_side_effect=track_live)
    n = 2
    _stop_loop_after(mocker, n)
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--loop", "--interval", "60s"])
    assert len(config_calls) >= n + 1                      # startup + one Config per loop tick
    assert is_live_seen == [True, False]                   # iteration 2 sees dryrun from disk


def test_loop_rejects_sub_minute_interval(tmp_path, monkeypatch, capsys):
    """B11: bad --interval exits 2 like cmd_daemon, never a traceback."""
    _setup_accounts(tmp_path, monkeypatch)
    from fanops.cli import main
    assert main(["run", "--loop", "--interval", "5x"]) == 2
    assert "interval" in capsys.readouterr().err.lower()


def test_oneshot_without_loop_unchanged(tmp_path, monkeypatch, mocker, capsys):
    _setup_accounts(tmp_path, monkeypatch)
    import fanops.cli as cli
    _stub_run(mocker, cli)
    sleep_spy = mocker.patch("fanops.cli.time.sleep")
    assert main(["run", "--base-time", "2026-06-02T18:00:00Z"]) == 0
    sleep_spy.assert_not_called()
    assert cli.advance.call_count == 1
    assert len(_heartbeat_lines(capsys.readouterr().out)) == 1
