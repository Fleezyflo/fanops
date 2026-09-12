# tests/test_pipeline_concurrent.py
"""Parallel per-source pipeline (FANOPS_CONCURRENT_SOURCES). The safety contract is EQUIVALENCE, not
timing: flag-ON final ledger state == flag-OFF final state (determinism), one source erroring
quarantines only itself. NO wall-clock assertions — the 60s pytest timeout is a DEADLOCK detector."""
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
        if cmd[0] == "ffmpeg" and not str(cmd[-1]).startswith("-"):
            if fail_src_substr and fail_src_substr in joined:
                class R: returncode=1; stderr="boom"; stdout=""
                return R()
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
            class R: returncode=0; stderr=""; stdout=""
            return R()
        if not str(cmd[-1]).startswith("-"):
            out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(b"X")
        class R: returncode=0; stderr=""; stdout=""
        return R()
    for mod in ("transcribe", "signals", "clip", "ingest", "media_probe"):
        mocker.patch(f"fanops.{mod}.subprocess.run", side_effect=fake)


def _seed_n_decided(cfg, n):
    with Ledger.transaction(cfg) as led:
        for i in range(n):
            sid = f"src_{i}"; sp = cfg.sources / f"{sid}.mp4"; _put(sp, b"V")
            led.add_source(Source(id=sid, source_path=str(sp), state=SourceState.moments_decided,
                                  sha256=str(i), width=1920, height=1080, duration=20.0,
                                  signal_peaks=[{"t": 16.0, "score": 0.9}],
                                  transcript=[{"start": 0, "end": 2, "text": "hi"}]))
            led.add_moment(Moment(id=f"mom_{i}", parent_id=sid, state=MomentState.decided,
                                  start=14.0, end=18.0, reason="punchline"))


def _final_state(cfg):
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
    monkeypatch.setenv("FANOPS_HOOK_EDITOR", "off")
    monkeypatch.delenv("FANOPS_HOOK_JUDGE", raising=False)
    if on: monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")
    else: monkeypatch.delenv("FANOPS_CONCURRENT_SOURCES", raising=False)
    if workers is not None: monkeypatch.setenv("FANOPS_CONCURRENT_WORKERS", str(workers))
    cfg = Config(root=root); _accts(cfg); _ff(mocker, fail_src_substr=fail_src_substr); _seed_n_decided(cfg, n)
    advance(cfg, base_time="2099-01-01T00:00:00Z")
    return cfg


def test_flag_off_final_state_matches_today(tmp_path, monkeypatch, mocker):
    cfg = _run(tmp_path, monkeypatch, mocker, on=False, n=3)
    st = _final_state(cfg)
    assert st["n_clips"] == 3
    assert all(v == "clipped" for v in st["moments"].values())


def test_flag_on_final_state_identical(tmp_path, monkeypatch, mocker):
    off = _final_state(_run(tmp_path / "off", monkeypatch, mocker, on=False, n=3))
    on = _final_state(_run(tmp_path / "on", monkeypatch, mocker, on=True, n=3))
    assert on == off


def test_workers_one_equals_sequential(tmp_path, monkeypatch, mocker):
    off = _final_state(_run(tmp_path / "off", monkeypatch, mocker, on=False, n=3))
    on1 = _final_state(_run(tmp_path / "on1", monkeypatch, mocker, on=True, workers=1, n=3))
    assert on1 == off


def test_one_source_error_does_not_kill_others(tmp_path, monkeypatch, mocker):
    cfg = _run(tmp_path, monkeypatch, mocker, on=True, n=3, fail_src_substr="src_1.mp4")
    led = Ledger.load(cfg)
    bad = [c for c in led.clips.values() if c.parent_id == "mom_1"]
    assert bad and all("ffmpeg rc=1" in (c.error_reason or "") for c in bad)
    assert led.moments["mom_1"].state is MomentState.decided
    others = [m for mid, m in led.moments.items() if mid != "mom_1"]
    assert others and all(m.state is MomentState.clipped for m in others)
    ok_clips = [c for c in led.clips.values() if c.parent_id != "mom_1"]
    assert ok_clips and all((c.error_reason or "") == "" for c in ok_clips)


def test_error_isolation_equivalent_off_and_on(tmp_path, monkeypatch, mocker):
    off = _final_state(_run(tmp_path / "off", monkeypatch, mocker, on=False, n=3, fail_src_substr="src_1.mp4"))
    on = _final_state(_run(tmp_path / "on", monkeypatch, mocker, on=True, n=3, fail_src_substr="src_1.mp4"))
    assert on == off


def test_empty_corpus_on(tmp_path, monkeypatch, mocker):
    monkeypatch.delenv("FANOPS_POSTER", raising=False); monkeypatch.setenv("FANOPS_HOOK_EDITOR", "off")
    monkeypatch.setenv("FANOPS_CONCURRENT_SOURCES", "1")
    cfg = Config(root=tmp_path); _accts(cfg); _ff(mocker)
    s = advance(cfg, base_time="2099-01-01T00:00:00Z")
    assert s["sources"] == 0 and s["clips"] == 0


def test_single_source_on(tmp_path, monkeypatch, mocker):
    cfg = _run(tmp_path, monkeypatch, mocker, on=True, n=1)
    st = _final_state(cfg)
    assert st["n_clips"] == 1 and st["moments"]["mom_0"] == "clipped"
