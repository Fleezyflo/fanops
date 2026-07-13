# tests/test_pipeline_one_pass.py
"""M1+M3 invariants — the slow stages run lock-free in `produce.run_all` (M3) and the reducer
in `pipeline.advance()` adopts the warm artifacts inside a SHORT main transaction.

M1 race the producer lock closes: two concurrent transcribe_source calls for the same source
must spawn ONE whisper subprocess. The per-(stage, source) producer lock + on-disk artifact
short-circuit makes the bad path unconstructable, not guarded by a re-raceable sentinel.

M3 reduce-txn invariant: on a produced source (transcript JSON warm), advance()'s in-lock
transcribe_source / detect_signals calls ADOPT the producer's cached artifact and never shell out
— the slow whisper/ffmpeg subprocess that once wedged the daemon under the flock. The contract is
proven by asserting those two producers spawn NO subprocess while the flock is held, NOT by a
wall-clock bound (which flakes under a contended CI host). (Bounded keyframe ffmpeg the reducer
runs downstream is permitted and deliberately NOT counted.) A generous env-tunable duration budget
stays as a soft signal for a non-subprocess in-lock stall.

Mutation proofs:
- Remove the transcribe stage_lock acquire -> test_double_transcribe_spawns_one_whisper fails.
- Drop the in-lock adopt-or-defer guard in detect_signals (defeat the warm-sidecar adopt AND the
  `if in_lock` defer) so the reducer shells its ffmpeg passes under the flock -> the in-lock
  producer subprocess assertion in test_main_reduce_txn_is_short fails (verified: it records the 3
  silencedetect/scdet/astats spawns). detect_signals is the producer that reliably runs `in_lock`
  on this warm path — transcribe_source short-circuits on its warm JSON before its own in-lock
  guard — but transcribe_source is watched too (it exercises in-lock on a cold-transcript tick)."""
import json
import os
import threading
import time
from pathlib import Path

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.transcribe import transcribe_source
from tests.fixtures.speech_segments import talk_seg


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
        talk_seg("warm", start=0.0, end=1.0)]}))

    # A no-op subprocess spy so the produce prewarm never actually shells out; it also RECORDS the
    # argv of any call made while `_in_producer[0]` is set (see below) — that flag marks the window
    # INSIDE an in-lock transcribe_source / detect_signals call, which is exactly where the M3
    # contract forbids a spawn. (All fanops modules share the one stdlib `subprocess` object, so
    # patching `.run` here catches every subprocess spawn repo-wide, incl. the bounded keyframe
    # ffmpeg the reducer legitimately runs — which is why the recorder gates on `_in_producer`, not
    # on merely being inside the transaction: keyframe extraction under the flock is NOT the hazard.)
    _in_producer = [False]
    in_lock_producer_subprocess: list[list[str]] = []

    def noop(cmd, **kw):
        if _in_producer[0]:
            in_lock_producer_subprocess.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])

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

    # THE CONTRACT SENSOR: wrap the two adopt-or-defer producers the reducer calls under the flock
    # (transcribe_source / detect_signals, both `in_lock=True` at pipeline.py). Set `_in_producer`
    # only for the duration of each call, so `noop` above records any subprocess THAT call spawns.
    # On a warm source both must take the cache-adopt branch and never reach subprocess.run — that
    # is the M3 "cache hit by construction" invariant, asserted directly (immune to CI contention,
    # unlike a wall-clock bound). pipeline.py binds both as module-level names, so patch them there.
    import fanops.pipeline as pipeline_mod
    real_transcribe = pipeline_mod.transcribe_source
    real_detect = pipeline_mod.detect_signals

    def watched_transcribe(*a, **kw):
        prev, _in_producer[0] = _in_producer[0], True
        try: return real_transcribe(*a, **kw)
        finally: _in_producer[0] = prev

    def watched_detect(*a, **kw):
        prev, _in_producer[0] = _in_producer[0], True
        try: return real_detect(*a, **kw)
        finally: _in_producer[0] = prev

    mocker.patch.object(pipeline_mod, "transcribe_source", watched_transcribe)
    mocker.patch.object(pipeline_mod, "detect_signals", watched_detect)

    # Spy the ledger transaction for a soft secondary signal: the wall-clock time inside the
    # critical section. Kept generous + env-tunable so a contended CI host never flakes it.
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

    # THE INVARIANT (M3 lock-free produce contract): on a warm source, the reducer's in-lock
    # transcribe_source / detect_signals calls ADOPT the producer's cached artifact and NEVER shell
    # out — the slow whisper/ffmpeg subprocess that once wedged the daemon under the flock. Asserted
    # directly on the sensor above; a regression that re-introduced an in-lock spawn (removed the
    # cache short-circuit, or dropped the `in_lock` adopt-or-defer guard) trips it deterministically,
    # not on a fragile wall-clock threshold.
    assert len(txn_durations) >= 1
    assert in_lock_producer_subprocess == [], (
        f"an in-lock producer (transcribe_source/detect_signals) spawned a subprocess while holding "
        f"the ledger flock — M3's lock-free produce contract is broken (the reducer must adopt the "
        f"warm artifact, never shell whisper/ffmpeg under the flock): {in_lock_producer_subprocess}")
    # Soft secondary signal: even a cache-hit reduce should be quick. Generous + env-tunable so a
    # contended parallel-worktree CI host never flakes it; the subprocess assertion above is the
    # real gate, so this only catches a non-subprocess in-lock stall.
    budget = float(os.environ.get("FANOPS_REDUCE_TXN_BUDGET_S", "10.0"))
    longest = max(txn_durations)
    assert longest < budget, (
        f"main reduce txn held the ledger flock for {longest:.3f}s (budget {budget:.1f}s) with NO "
        f"in-lock producer subprocess — a non-subprocess in-lock stall has regressed M3's fast-reduce contract")
