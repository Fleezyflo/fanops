# tests/test_pipeline_concurrent.py
"""Parallel per-source pipeline (FANOPS_CONCURRENT_SOURCES). The safety contract is EQUIVALENCE, not
timing: flag-ON final ledger state == flag-OFF final state (determinism), the pool is NOT constructed
when the flag is OFF, advance() keeps EXACTLY two Ledger.transaction calls (ingest tx + main tx — the
one-writer rule), and one source erroring quarantines only itself. NO wall-clock assertions anywhere —
the 60s pytest timeout is a DEADLOCK detector; a hang here means a worker touched the ledger."""
import json
from pathlib import Path

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, SourceState, MomentState
from fanops.pipeline import advance


def _put(p, b): p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)


def _accts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "1", "platforms": ["instagram"], "status": "active"}]}))


def _ff(mocker, *, fail_src_substr=None):
    # Content-addressed subprocess fake, pool-safe (writes per-source / per-output paths, no shared
    # temp). If fail_src_substr is given, an ffmpeg RENDER whose input source path contains that
    # substring returns rc=1 (no output written) — used to force ONE source's clip render to fail
    # while the others render cleanly (fault-isolation test).
    def fake(cmd, **kw):
        joined = " ".join(str(c) for c in cmd)
        if cmd[0] == "whisper" or "fanops._fwrun" in cmd:
            outdir = Path(cmd[cmd.index("--output_dir") + 1]); outdir.mkdir(parents=True, exist_ok=True)
            (outdir / f"{Path(cmd[-1]).stem}.json").write_text(json.dumps(
                {"language": "en", "segments": [{"start": 14.0, "end": 18.0, "text": "they slept on me"}]}))
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if cmd[0] in ("ffmpeg",) and "null" in cmd:
            class R:
                returncode=0; stdout=""
                stderr = ("silence_end: 16.0 | silence_duration: 1.0" if "silencedetect" in joined
                          else "[scdet @ 0x] lavfi.scd.score: 28.0, lavfi.scd.time: 16.0")
            return R()
        if cmd[0] == "ffprobe":
            class R:
                returncode=0; stderr=""
                stdout = "video" if "codec_type" in joined else "1920\n1080\n20.0\n"
            return R()
        # a clip RENDER: ffmpeg with a real output path. Optionally fail it for one source's input.
        if cmd[0] == "ffmpeg" and not str(cmd[-1]).startswith("-"):
            if fail_src_substr and fail_src_substr in joined:
                class R: returncode=1; stderr="boom"; stdout=""
                return R()                                # no output written -> ClipState.error
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)


def _seed_n_decided(cfg, n):
    # N independent sources, each with one clean (hook=None) DECIDED moment ready to render. The
    # moment is decided, so the pre-warm pass (sequential OR concurrent) renders its clip lock-free;
    # advance()'s main tx then adopts the warm clip via the fingerprint skip. Per-source disjoint
    # paths so concurrent renders never collide.
    with Ledger.transaction(cfg) as led:
        for i in range(n):
            sid = f"src_{i}"; sp = cfg.sources / f"{sid}.mp4"; _put(sp, b"V")
            led.add_source(Source(id=sid, source_path=str(sp), state=SourceState.moments_decided,
                                  sha256=str(i), width=1920, height=1080, duration=20.0,
                                  signal_peaks=[{"t": 16.0, "score": 0.9}],
                                  transcript=[{"start": 0, "end": 2, "text": "hi"}]))
            led.add_moment(Moment(id=f"mom_{i}", parent_id=sid, state=MomentState.decided,
                                  start=14.0, end=18.0, reason="punchline"))   # hook=None -> clean clip


def _final_state(cfg):
    # The deterministic state to compare across flag-OFF vs flag-ON: per-source states, per-moment
    # states, clip count + per-clip (parent, state), post count. NOT timing, NOT as_completed order.
    led = Ledger.load(cfg)
    return {
        "sources": {sid: s.state.value for sid, s in led.sources.items()},
        "moments": {mid: m.state.value for mid, m in led.moments.items()},
        "clips": sorted((c.parent_id, c.state.value) for c in led.clips.values()),
        "n_clips": len(led.clips),
        "n_posts": len(led.posts),
    }


def _run(root, monkeypatch, mocker, *, on, workers=None, n=3, fail_src_substr=None):
    monkeypatch.delenv("FANOPS_POSTER", raising=False)
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "off")           # clean baseline: no feed-editor hold
    monkeypatch.delenv("FANOPS_HOOK_JUDGE", raising=False)    # conftest strips it -> code default
    if on: monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")
    else: monkeypatch.delenv("FANOPS_CONCURRENT_SOURCES", raising=False)
    if workers is not None: monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", str(workers))
    cfg = Config(root=root); _accts(cfg); _ff(mocker, fail_src_substr=fail_src_substr); _seed_n_decided(cfg, n)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    return cfg


def test_flag_off_final_state_matches_today(tmp_path, monkeypatch, mocker):
    # Baseline: with the flag OFF, N sources each render their clip exactly as today.
    cfg = _run(tmp_path, monkeypatch, mocker, on=False, n=3)
    st = _final_state(cfg)
    assert st["n_clips"] == 3                                  # one clip per source
    assert all(v == "clipped" for v in st["moments"].values())


def test_flag_on_final_state_identical(tmp_path, monkeypatch, mocker):
    # EQUIVALENCE (the core safety claim): the flag-ON final ledger state is byte-identical to the
    # flag-OFF run on the same corpus. Two independent roots, seeded identically; compare the state
    # dicts. No timing — determinism only.
    off = _final_state(_run(tmp_path / "off", monkeypatch, mocker, on=False, n=3))
    on = _final_state(_run(tmp_path / "on", monkeypatch, mocker, on=True, n=3))
    assert on == off


def test_workers_one_equals_sequential(tmp_path, monkeypatch, mocker):
    # WORKERS=1 degenerates the pool to serial -> identical final state to the flag-OFF run.
    off = _final_state(_run(tmp_path / "off", monkeypatch, mocker, on=False, n=3))
    on1 = _final_state(_run(tmp_path / "on1", monkeypatch, mocker, on=True, workers=1, n=3))
    assert on1 == off


def test_one_source_error_does_not_kill_others(tmp_path, monkeypatch, mocker):
    # Fault isolation: force ONE source's clip render to fail (ffmpeg rc=1). render_moment records the
    # failure as an error_reason on that clip (fail-open per clip.py) and the source's moment stays
    # un-clipped (decided); the OTHER sources render clean clips (no error_reason) and their moments
    # advance to clipped. The pass returns normally — one bad source is isolated, never wedging the rest.
    cfg = _run(tmp_path, monkeypatch, mocker, on=True, n=3, fail_src_substr="src_1.mp4")
    led = Ledger.load(cfg)
    bad = [c for c in led.clips.values() if c.parent_id == "mom_1"]
    assert bad and all("ffmpeg rc=1" in (c.error_reason or "") for c in bad)   # the failed source's clip carries the error
    assert led.moments["mom_1"].state is MomentState.decided                   # un-clipped -> a re-run retries
    others = [m for mid, m in led.moments.items() if mid != "mom_1"]
    assert others and all(m.state is MomentState.clipped for m in others)      # the rest rendered cleanly
    ok_clips = [c for c in led.clips.values() if c.parent_id != "mom_1"]
    assert ok_clips and all((c.error_reason or "") == "" for c in ok_clips)    # no error bled onto the others


def test_error_isolation_equivalent_off_and_on(tmp_path, monkeypatch, mocker):
    # EQUIVALENCE under a fault: a failing source produces the SAME final state OFF and ON (the error
    # is re-derived deterministically by the in-lock reduce, not masked by the concurrency).
    off = _final_state(_run(tmp_path / "off", monkeypatch, mocker, on=False, n=3, fail_src_substr="src_1.mp4"))
    on = _final_state(_run(tmp_path / "on", monkeypatch, mocker, on=True, n=3, fail_src_substr="src_1.mp4"))
    assert on == off


def test_transaction_count_stays_two(tmp_path, monkeypatch, mocker):
    # ONE-WRITER guard: with the flag ON and 3 sources seeded, advance() must open EXACTLY two
    # Ledger.transaction (the ingest tx + the main tx). The map phase holds NO lock and adds NO
    # transaction; no worker opens one. This is the machine guard against a worker touching the ledger.
    monkeypatch.delenv("FANOPS_POSTER", raising=False); monkeypatch.setenv("FANOPS_HOOK_EDITOR", "off")
    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")
    cfg = Config(root=tmp_path); _accts(cfg); _ff(mocker); _seed_n_decided(cfg, 3)
    spy = mocker.spy(Ledger, "transaction")
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    assert spy.call_count == 2


def test_pool_not_constructed_when_flag_off(tmp_path, monkeypatch, mocker):
    # BYTE-IDENTICAL guard: with the flag OFF, the ThreadPoolExecutor is never constructed. Patch it
    # to raise on construction; flag-off advance() must NOT raise (it takes the sequential path).
    # M3: the pool now lives in fanops.produce, not fanops.pipeline.
    monkeypatch.delenv("FANOPS_POSTER", raising=False); monkeypatch.setenv("FANOPS_HOOK_EDITOR", "off")
    monkeypatch.delenv("FANOPS_CONCURRENT_SOURCES", raising=False)
    def boom(*a, **k): raise AssertionError("ThreadPoolExecutor constructed on the flag-OFF path")
    mocker.patch("fanops.produce.ThreadPoolExecutor", side_effect=boom)
    cfg = Config(root=tmp_path); _accts(cfg); _ff(mocker); _seed_n_decided(cfg, 3)
    advance(cfg, base_time="2099-01-01T00:00:00Z")            # must not raise
    assert _final_state(cfg)["n_clips"] == 3


def test_empty_corpus_on(tmp_path, monkeypatch, mocker):
    # Edge: no sources -> the pool is over an empty id list (no-op), advance() still opens exactly the
    # two transactions and crashes on nothing.
    monkeypatch.delenv("FANOPS_POSTER", raising=False); monkeypatch.setenv("FANOPS_HOOK_EDITOR", "off")
    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")
    cfg = Config(root=tmp_path); _accts(cfg); _ff(mocker)
    spy = mocker.spy(Ledger, "transaction")
    s = advance(cfg, base_time="2099-01-01T00:00:00Z")
    assert spy.call_count == 2 and s["sources"] == 0 and s["clips"] == 0


def test_single_source_on(tmp_path, monkeypatch, mocker):
    # Edge: a pool of one future renders the single source's clip, state correct.
    cfg = _run(tmp_path, monkeypatch, mocker, on=True, n=1)
    st = _final_state(cfg)
    assert st["n_clips"] == 1 and st["moments"]["mom_0"] == "clipped"


def test_producer_isolates_a_worker_crash(tmp_path, monkeypatch, mocker):
    # Defensive: a worker that crashes PAST its own fail-open guard (OOM / thread-level) must NOT
    # propagate fut.result() up through produce.run_all -> advance() and abort the pass before
    # the main transaction opens. It is logged as a warn and the producer returns normally.
    # M3: _prewarm_concurrent + _produce_source live in fanops.produce now.
    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")        # exercise the pool path
    from fanops.produce import run_all
    from fanops.log import get_logger
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg); led.add_source(Source(id="s1", source_path="/x.mp4", state=SourceState.catalogued)); led.save()
    mocker.patch("fanops.produce._produce_one", side_effect=RuntimeError("worker SENTINEL-CRASH"))
    run_all(cfg, set(), get_logger(cfg))                         # must NOT raise
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "SENTINEL-CRASH" in log                              # surfaced as a warn, not propagated
