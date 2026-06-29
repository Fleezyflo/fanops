# tests/test_pipeline_one_pass.py
"""M1 invariant — two concurrent transcribe_source calls on the same source spawn ONE whisper
subprocess, not two. The race the M1 milestone closes: advance() called twice in rapid succession
ran transcribe in prewarm (lock-free) AND in the main pass (in-lock), both saw the cache JSON
absent (whisper still running), both spawned a subprocess on the same audio, both ground 70+ CPU
minutes against each other while the daemon's flock starved.

The fix is the per-stage producer lock (src/fanops/stage_lock.py). Re-check the JSON INSIDE the
lock: the first acquirer runs whisper and writes JSON atomically; the second acquirer enters the
critical section, finds the JSON, short-circuits. The bad path becomes unconstructable, not
guarded by a sentinel that can be re-raced.

Mutation proof: removing the stage_lock acquire in transcribe_source makes
test_double_transcribe_spawns_one_whisper fail (two subprocess.run calls).
"""
import json
import threading
import time
from pathlib import Path

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.transcribe import transcribe_source


def test_double_transcribe_spawns_one_whisper(tmp_path, mocker, monkeypatch):
    """Two threads call transcribe_source for the SAME source within the same second. The first
    enters the stage lock, sleeps (simulating a long whisper run), writes the JSON, exits. The
    second waits on the lock, enters, finds the JSON on disk, short-circuits. Exactly ONE
    subprocess.run is observed."""
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")           # skip demucs; isolate the lock contract
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_race", source_path=str(cfg.sources / "src_race.mp4"),
                          state=SourceState.catalogued))
    led.save()

    calls: list[list[str]] = []
    call_lock = threading.Lock()

    def slow_run(cmd, **kw):
        with call_lock:
            calls.append(list(cmd))
        time.sleep(0.4)                                        # simulate a slow whisper run
        outdir = Path(cmd[cmd.index("--output_dir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(
            json.dumps({"language": "en", "segments": [
                {"start": 0.0, "end": 1.0, "text": "one"}]}))

        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()

    mocker.patch("fanops.transcribe.subprocess.run", side_effect=slow_run)

    def race():
        local_led = Ledger.load(cfg)
        transcribe_source(local_led, cfg, "src_race")

    t1 = threading.Thread(target=race)
    t2 = threading.Thread(target=race)
    t1.start()
    # Stagger by less than the simulated run length so the second thread blocks on the lock while
    # the first is mid-run (the exact shape of the race that wedged the daemon).
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert len(calls) == 1, (
        f"expected ONE whisper subprocess (stage lock + on-disk artifact short-circuit), got "
        f"{len(calls)} — the producer lock is not closing the race")


def test_transcribe_short_circuits_on_existing_json(tmp_path, mocker, monkeypatch):
    """A second transcribe_source call on a source whose JSON already exists must NOT shell out.
    Pre-existing behaviour, pinned here so a future refactor can't lose it."""
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_warm", source_path=str(cfg.sources / "src_warm.mp4"),
                          state=SourceState.catalogued))
    out_dir = cfg.agent_io / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "src_warm.json").write_text(json.dumps({"language": "en", "segments": [
        {"start": 0.0, "end": 1.0, "text": "warm"}]}))

    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))

        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()

    mocker.patch("fanops.transcribe.subprocess.run", side_effect=fake_run)
    transcribe_source(led, cfg, "src_warm")
    assert calls == [], (
        f"transcribe_source spawned a subprocess despite an existing JSON: {calls}")
    assert led.sources["src_warm"].state is SourceState.transcribed
