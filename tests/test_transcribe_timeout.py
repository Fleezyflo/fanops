# tests/test_transcribe_timeout.py — the whisper subprocess bound is duration-aware AND single-mode.
# Root bug it guards: a long source (e.g. 58min) blew the fixed 45min cap every pass -> never transcribed
# -> frozen at `catalogued` forever. The fix is to scale the budget to the source length so a long source
# actually finishes.
#
# M1 collapse + H10: transcribe_source may run INSIDE the ledger flock (pipeline reducer) with
# in_lock=True — adopt-or-defer on cold cache; whisper never shells under the flock. Out-of-lock
# callers (produce.run_all) use the default in_lock=False and run the full whisper subprocess under
# the per-(stage,source) stage_lock instead.
import subprocess
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.transcribe import _whisper_timeout, _WHISPER_TIMEOUT, _PREWARM_TIMEOUT_FACTOR, transcribe_source


def test_long_source_scales_with_length():
    # a 58min (3480s) source gets a budget that covers it (the wedge fix): 3480*1.5 = 5220 > 2700.
    assert _whisper_timeout(3480.0) == 3480.0 * _PREWARM_TIMEOUT_FACTOR
    assert _whisper_timeout(3480.0) > _WHISPER_TIMEOUT


def test_short_or_unknown_duration_uses_the_floor():
    # a short / unknown / zero-duration source still gets at least the fixed floor — no tiny budget.
    assert _whisper_timeout(60.0) == _WHISPER_TIMEOUT
    assert _whisper_timeout(None) == _WHISPER_TIMEOUT
    assert _whisper_timeout(0.0) == _WHISPER_TIMEOUT


def test_timeout_killed_log_emitted(tmp_path, mocker, monkeypatch, capsys):
    # MOL-481: silent timeout kills are visible in structured run.log.
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued, duration=3600.0))
    def hung(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=hung)
    transcribe_source(led, cfg, "src_1")
    out = capsys.readouterr().err
    assert "timeout_killed" in out and "transcribe" in out
    assert "model=" in out or '"model"' in out


def test_whisper_timeout_downgrades_model_on_retry(tmp_path, mocker, monkeypatch):
    # After a timeout kill, the next attempt picks a smaller model.
    monkeypatch.delenv("FANOPS_ASR_MODEL", raising=False)
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.catalogued, duration=3600.0,
                          meta={"whisper_timeout_attempts": 1}))
    models = []
    def fake_run(cmd, **kw):
        models.append(cmd[cmd.index("--model") + 1])
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "src_1")
    assert models[0] == cfg.asr_model_for(3600.0, timeout_attempts=1)


def test_repeated_whisper_timeouts_stop_auto_resume(tmp_path, mocker, monkeypatch):
    # MOL-481: after 3 timeout kills auto-resume stops (doom loop mitigation).
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path)
    path = str(tmp_path / "vid.mp4"); Path(path).write_bytes(b"V")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path=path, state=SourceState.error,
                              error_reason="whisper timed out after 5400s (attempt 3/3)",
                              meta={"transcribed": False, "whisper_timeout_attempts": 3}))
    from fanops.artifacts import is_transient_error
    from fanops.pipeline import reconcile_source_progress
    from fanops.log import get_logger
    assert is_transient_error("whisper timed out after 5400s (attempt 3/3)") is False
    with Ledger.transaction(cfg) as led:
        reconcile_source_progress(led, cfg, get_logger(cfg))
    assert Ledger.load(cfg).sources["s1"].state is SourceState.error
