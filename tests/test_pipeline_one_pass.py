# tests/test_pipeline_one_pass.py
"""M1+M3 invariants — the slow stages run lock-free in `produce.run_all` (M3) and the reducer
in `pipeline.advance()` adopts the warm artifacts inside a SHORT main transaction.

M1 race the producer lock closes: two concurrent transcribe_source calls for the same source
must spawn ONE whisper subprocess. The per-(stage, source) producer lock + on-disk artifact
short-circuit makes the bad path unconstructable, not guarded by a re-raceable sentinel.

M3 reduce-txn invariant: on a produced source (transcript JSON warm), advance()'s main
ledger transaction completes in well under one wall-clock second — the in-lock transcribe
call is a microsecond cache hit by construction. A future regression that re-introduces an
in-lock subprocess spawn would balloon this past 1s.

Mutation proofs:
- Remove the transcribe stage_lock acquire -> test_double_transcribe_spawns_one_whisper fails.
- Re-introduce a slow in-lock subprocess inside _stage_source_to_moments -> the wall-clock
  bound in test_main_reduce_txn_is_short fails."""
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


def test_main_reduce_txn_is_short(tmp_path, monkeypatch, mocker):
    """M3 — on a source whose transcript JSON is already warm (the producer wrote it lock-free),
    the main reduce transaction in advance() completes in well under one second. The reducer's
    in-lock transcribe_source call is a microsecond cache hit by construction; a future
    regression that re-introduced a slow in-lock subprocess would balloon this past the bound.

    Setup: one catalogued source + a pre-warmed transcript JSON on disk. Mock ingest_staged so we
    don't shell ffprobe; mock subprocess.run so a stray subprocess (a bug) would be visible. The
    in-lock `_stage_source_to_moments`'s transcribe call adopts the cache and flips state without
    ever entering subprocess.run."""
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_ISOLATE_VOCALS", "0")
    mocker.patch("fanops.transcribe._fw_available", return_value=True)
    cfg = Config(root=tmp_path)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))

    # Seed a catalogued source.
    src_path = cfg.sources / "src_warm.mp4"
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_warm", source_path=str(src_path),
                          duration=10.0, state=SourceState.catalogued))
    led.save()
    # Pre-warm the transcript JSON (simulates a prior producer's lock-free output).
    out_dir = cfg.agent_io / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "src_warm.json").write_text(json.dumps({"language": "en", "segments": [
        {"start": 0.0, "end": 1.0, "text": "warm"}]}))

    # If the producer's stitch prewarm tries to shell anything, return a no-op.
    def noop(cmd, **kw):
        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()
    mocker.patch("fanops.transcribe.subprocess.run", side_effect=noop)
    mocker.patch("fanops.signals.subprocess.run", side_effect=noop)
    mocker.patch("fanops.clip.subprocess.run", side_effect=noop)
    mocker.patch("fanops.ingest.subprocess.run", side_effect=noop)
    from fanops.ingest import IngestCounts, StagedInbox
    # M05 ingest split: stage is lock-free; mint is a no-op (source already catalogued).
    mocker.patch("fanops.pipeline.stage_inbox_candidates", return_value=StagedInbox(cfg.inbox, [], [], IngestCounts()))
    mocker.patch("fanops.pipeline.ingest_staged", side_effect=lambda led, cfg, staged, **kw: (led, IngestCounts()))
    mocker.patch("fanops.pipeline._archive_staged")

    # Spy the ledger transaction so we can isolate the time spent INSIDE its critical section.
    import fanops.ledger as ledger_mod
    real_transaction = ledger_mod.Ledger.transaction
    txn_durations: list[float] = []

    from contextlib import contextmanager

    @contextmanager
    def timing_transaction(cfg_arg, *a, **kw):
        t0 = time.monotonic()
        with real_transaction(cfg_arg, *a, **kw) as led_inner:
            yield led_inner
        txn_durations.append(time.monotonic() - t0)

    mocker.patch.object(ledger_mod.Ledger, "transaction", staticmethod(timing_transaction))

    from fanops.pipeline import advance
    advance(cfg, base_time="2026-06-29T12:00:00Z")

    # advance() opens 2 transactions: the short ingest one and the main reduce one. Both must be
    # under 1s wall clock on a warm source.
    assert len(txn_durations) >= 1
    longest = max(txn_durations)
    assert longest < 1.0, (
        f"main reduce txn held the ledger flock for {longest:.3f}s — a slow in-lock subprocess "
        f"has regressed; M3's lock-free produce contract is broken")
