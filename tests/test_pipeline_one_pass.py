# tests/test_pipeline_one_pass.py
"""M1+M3 invariants — the slow stages run lock-free in `produce.run_all` (M3) and the reducer
in `pipeline.advance()` adopts the warm artifacts inside a SHORT main transaction.

M1: two concurrent transcribe_source calls for the same source spawn ONE whisper subprocess.
M3: a warm transcript JSON + signals sidecar means advance() never shells whisper / silencedetect /
scdet (argv recorded at the subprocess edge)."""
import json
import threading
import time
from pathlib import Path

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.signals import _SIDECAR_V
from fanops.transcribe import transcribe_source
from tests.fixtures.speech_segments import talk_seg


def test_double_transcribe_spawns_one_whisper(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
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
        time.sleep(0.4)
        outdir = Path(cmd[cmd.index("--output_dir") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{Path(cmd[-1]).stem}.json").write_text(
            json.dumps({"language": "en", "segments": [talk_seg("one", start=0.0, end=1.0)]}))

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
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert len(calls) == 1, (
        f"expected ONE whisper subprocess (stage lock + on-disk artifact short-circuit), got "
        f"{len(calls)} — the producer lock is not closing the race")


def test_transcribe_short_circuits_on_existing_json(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_warm", source_path=str(cfg.sources / "src_warm.mp4"),
                          state=SourceState.catalogued))
    out_dir = cfg.agent_io / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "src_warm.json").write_text(json.dumps({"language": "en", "segments": [
        talk_seg("warm", start=0.0, end=1.0)]}))

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


def test_main_reduce_txn_is_short(tmp_path, monkeypatch, mocker):
    """Warm transcript + signals sidecar: advance must not spawn whisper or ffmpeg signal passes."""
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))

    src_path = cfg.sources / "src_warm.mp4"
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_warm", source_path=str(src_path),
                          duration=10.0, state=SourceState.catalogued))
    led.save()
    out_dir = cfg.agent_io / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "src_warm.json").write_text(json.dumps({"language": "en", "segments": [
        talk_seg("warm", start=0.0, end=1.0)]}))
    sig_dir = cfg.agent_io / "signals"
    sig_dir.mkdir(parents=True, exist_ok=True)
    (sig_dir / "src_warm.json").write_text(json.dumps({
        "v": _SIDECAR_V, "peaks": [{"t": 1.0, "kind": "speech_resume", "score": 0.5}],
        "duration": 10.0}))

    spawned: list[list[str]] = []

    def record(cmd, **kw):
        spawned.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])

        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()

    for mod in ("transcribe", "signals", "clip", "ingest", "media_probe"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=record)

    from fanops.pipeline import advance
    advance(cfg, base_time="2026-06-29T12:00:00Z")

    producer = [c for c in spawned if (
        (c and c[0] == "whisper") or "fanops._fwrun" in c
        or any("silencedetect" in str(x) or "scdet" in str(x) for x in c))]
    assert producer == [], (
        f"warm transcript/signals still spawned a producer subprocess: {producer}")
    assert Ledger.load(cfg).sources["src_warm"].state is not SourceState.catalogued
