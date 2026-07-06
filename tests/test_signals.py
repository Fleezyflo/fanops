# tests/test_signals.py
import subprocess
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.errors import ToolchainMissingError
from fanops.signals import (parse_silences, parse_scene_changes, detect_signals, apply_energy,
                            filter_peaks_by_intensity, _silence_cmd, _scene_cmd, _scene_timeout)

SILENCE_STDERR = """
[silencedetect @ 0x] silence_start: 2.5
[silencedetect @ 0x] silence_end: 4.0 | silence_duration: 1.5
[silencedetect @ 0x] silence_start: 9.2
[silencedetect @ 0x] silence_end: 10.0 | silence_duration: 0.8
"""
# Real scdet output form (ffmpeg prints these at -loglevel info):
SCENE_STDERR = """
[scdet @ 0x] lavfi.scd.score: 12.345, lavfi.scd.time: 1.20
[scdet @ 0x] lavfi.scd.score: 28.900, lavfi.scd.time: 6.80
"""
# astats energy pass (Theme 1): a quiet window early, a LOUD window at ~4s and ~10s (the speech onsets).
ENERGY_STDOUT = """
frame:0   pts_time:0.000000
lavfi.astats.Overall.RMS_level=-42.0
frame:1   pts_time:4.000000
lavfi.astats.Overall.RMS_level=-6.500000
frame:2   pts_time:10.000000
lavfi.astats.Overall.RMS_level=-8.000000
"""


def _energy_fake_run(cmd, **kw):
    joined = " ".join(cmd)
    class R:
        returncode = 0
        stdout = ENERGY_STDOUT if "astats" in joined else ""
        stderr = "" if "astats" in joined else (SILENCE_STDERR if "silencedetect" in joined else SCENE_STDERR)
    return R()

def test_parse_silences():
    s = parse_silences(SILENCE_STDERR)
    assert {round(x["t"], 1) for x in s} == {4.0, 10.0}
    assert all(x["kind"] == "speech_resume" for x in s)

def test_parse_scene_changes_from_scdet():
    sc = parse_scene_changes(SCENE_STDERR)
    assert {round(x["t"], 1) for x in sc} == {1.2, 6.8}
    assert all(x["kind"] == "scene_cut" for x in sc)
    assert any(x["score"] > 20 for x in sc)

def test_detect_signals_merges_advances_and_backfills_duration(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, duration=None,
                          transcript=[{"start": 0, "end": 1, "text": "x"}], meta={"transcribed": True}))
    def fake_run(cmd, **kw):
        joined = " ".join(cmd)
        class R:
            returncode = 0; stdout = ""
            stderr = SILENCE_STDERR if "silencedetect" in joined else SCENE_STDERR
        return R()
    mocker.patch("fanops.signals.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.signals.probe_dimensions", return_value=(1920, 1080, 12.0))
    led = detect_signals(led, cfg, "src_1")
    s = led.sources["src_1"]
    assert s.state is SourceState.signalled
    kinds = {p["kind"] for p in s.signal_peaks}
    assert "speech_resume" in kinds and "scene_cut" in kinds
    assert s.duration == 12.0                       # backfilled

def test_detect_signals_adopts_sidecar_and_skips_ffmpeg(tmp_path, mocker):
    # Phase D: a lock-free pre-warm pass wrote a deterministic per-source signals sidecar. detect_signals
    # must adopt it and NOT shell ffmpeg — keeping the two signal passes out of the ledger lock.
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    from fanops.signals import _SIDECAR_V
    sc = cfg.agent_io / "signals"; sc.mkdir(parents=True, exist_ok=True)
    (sc / "src_1.json").write_text(json.dumps(
        {"v": _SIDECAR_V, "peaks": [{"t": 4.0, "kind": "speech_resume", "score": 0.5}], "duration": 12.0}))
    spy = mocker.patch("fanops.signals.subprocess.run")
    led = detect_signals(led, cfg, "src_1")
    spy.assert_not_called()                                  # warm current-version sidecar reused — no ffmpeg
    s = led.sources["src_1"]
    assert s.state is SourceState.signalled and s.duration == 12.0
    assert s.signal_peaks[0]["kind"] == "speech_resume"

def test_detect_signals_writes_sidecar_for_the_commit_pass(tmp_path, mocker):
    # The warm (real) run must PERSIST a sidecar so the in-lock commit pass can skip the ffmpeg passes.
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    def fake_run(cmd, **kw):
        joined = " ".join(cmd)
        class R:
            returncode = 0; stdout = ""
            stderr = SILENCE_STDERR if "silencedetect" in joined else SCENE_STDERR
        return R()
    mocker.patch("fanops.signals.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.signals.probe_dimensions", return_value=(1920, 1080, 12.0))
    detect_signals(led, cfg, "src_1")
    sidecar = cfg.agent_io / "signals" / "src_1.json"
    assert sidecar.exists(), "expected a written signals sidecar"
    d = json.loads(sidecar.read_text())
    assert d["duration"] == 12.0 and any(p["kind"] == "scene_cut" for p in d["peaks"])

def test_apply_energy_sets_speech_score_from_nearest_rms_and_normalizes_scene():
    # THE commensurability fix (M2): in energy mode speech peaks score on loudness and scene scores are
    # normalized to [0,1], so a loud audio peak can finally out-rank a mid scene cut in the SAME field.
    peaks = [{"t": 4.0, "kind": "speech_resume", "score": 0.5},
             {"t": 1.2, "kind": "scene_cut", "score": 30.0}]
    windows = [{"t": 0.0, "rms": -42.0}, {"t": 4.0, "rms": -6.5}]
    out = apply_energy(peaks, windows)
    sp = next(p for p in out if p["kind"] == "speech_resume")
    sc = next(p for p in out if p["kind"] == "scene_cut")
    assert sp["score"] > 0.9 and sp["energy"] > 0.9           # loud onset at -6.5 dB
    assert sc["score"] == 0.3                                  # 30/100 normalized into [0,1]
    assert sp["score"] > sc["score"]                          # loud audio peak now outranks the scene cut

def test_apply_energy_legacy_unchanged_when_no_windows():
    # No energy windows (pass empty / failed) -> peaks are byte-equal to today (speech 0.5, scene raw).
    peaks = [{"t": 4.0, "kind": "speech_resume", "score": 0.5},
             {"t": 1.2, "kind": "scene_cut", "score": 30.0}]
    assert apply_energy(peaks, []) == peaks

def test_detect_signals_scores_speech_peaks_from_energy(tmp_path, mocker):
    # End-to-end wiring: with the astats pass returning loud windows at the speech onsets, the
    # speech_resume peaks no longer carry the constant 0.5 — they carry a real energy-derived score.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    mocker.patch("fanops.signals.subprocess.run", side_effect=_energy_fake_run)
    mocker.patch("fanops.signals.probe_dimensions", return_value=(1920, 1080, 12.0))
    led = detect_signals(led, cfg, "src_1")
    sp = [p for p in led.sources["src_1"].signal_peaks if p["kind"] == "speech_resume"]
    assert sp and all(p["score"] != 0.5 for p in sp)          # energy replaced the constant
    assert any(p.get("energy", 0) > 0.9 for p in sp)          # the loud onset window carried through

def test_detect_signals_v1_sidecar_not_adopted_recomputes(tmp_path, mocker):
    # C2/H2 cache trap: a pre-energy sidecar (no "v", or v<2) MUST NOT be adopted — else every
    # already-ingested source serves score=0.5 forever after Theme 1 ships. A stale sidecar is a cache
    # miss: detect_signals recomputes (runs ffmpeg) and overwrites with a v2 payload.
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    sc = cfg.agent_io / "signals"; sc.mkdir(parents=True, exist_ok=True)
    (sc / "src_1.json").write_text(json.dumps(                # legacy v1-shaped sidecar (no version)
        {"peaks": [{"t": 4.0, "kind": "speech_resume", "score": 0.5}], "duration": 12.0}))
    spy = mocker.patch("fanops.signals.subprocess.run", side_effect=_energy_fake_run)
    mocker.patch("fanops.signals.probe_dimensions", return_value=(1920, 1080, 12.0))
    detect_signals(led, cfg, "src_1")
    spy.assert_called()                                       # stale sidecar rejected -> ffmpeg ran
    d = json.loads((sc / "src_1.json").read_text())
    assert d["v"] == 3                                        # rewritten with the schema version (AGENT-2 peak cap bump)

def test_detect_signals_writes_versioned_sidecar(tmp_path, mocker):
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    mocker.patch("fanops.signals.subprocess.run", side_effect=_energy_fake_run)
    mocker.patch("fanops.signals.probe_dimensions", return_value=(1920, 1080, 12.0))
    detect_signals(led, cfg, "src_1")
    d = json.loads((cfg.agent_io / "signals" / "src_1.json").read_text())
    assert d["v"] == 3 and "peaks" in d

def test_detect_signals_raises_toolchain_error_when_ffmpeg_absent(tmp_path, mocker):
    # ffmpeg off PATH -> subprocess.run raises FileNotFoundError before the process starts
    # (check=False suppresses only a nonzero RETURNCODE). detect_signals runs INSIDE the pipeline's
    # per-source quarantine, so the typed ToolchainMissingError it raises is caught there and the
    # source goes to SourceState.error (see test_pipeline) — the pass never crashes. Here we pin
    # that the helper raises the TYPED error (not a bare FileNotFoundError) so the quarantine
    # records a clear "toolchain missing" reason.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    def absent(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])
    mocker.patch("fanops.signals.subprocess.run", side_effect=absent)
    with pytest.raises(ToolchainMissingError, match="ffmpeg"):
        detect_signals(led, cfg, "src_1")

def test_detect_signals_is_time_bounded_and_timeout_propagates(tmp_path, mocker):
    # The ffmpeg signal passes run inside advance()'s ledger transaction — unbounded, a hang on a
    # corrupt source held the flock forever. Each pass must carry a hard timeout=; a hang raises
    # TimeoutExpired, which propagates BY DESIGN to advance()'s per-source quarantine (test_pipeline
    # pins that path) -> SourceState.error "TimeoutExpired: ..." — the same retriable-error
    # treatment as the typed ToolchainMissingError above, and the pass never crashes.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    seen = {}
    def hung(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
    mocker.patch("fanops.signals.subprocess.run", side_effect=hung)
    with pytest.raises(subprocess.TimeoutExpired):
        detect_signals(led, cfg, "src_1")
    assert seen.get("timeout") == 600.0                               # the bound is actually wired


# ---- AGENT-2/ASSET-8: peaks are top-N capped at persist so a flashing/long source can't unbound the payload ----
def test_peaks_are_capped_at_persist():
    from fanops.signals import _cap_peaks, _MAX_PEAKS
    peaks = [{"t": float(i), "kind": "scene_cut", "score": float(i % 7)} for i in range(_MAX_PEAKS + 50)]
    out = _cap_peaks(peaks)
    assert len(out) == _MAX_PEAKS
    assert out == sorted(out, key=lambda p: p["t"])                   # chronological order preserved for window-scoping
    assert _cap_peaks(peaks[:10]) == peaks[:10]                       # under cap -> byte-identical (small sources unchanged)


# ---- MOL-119: audio-only passes must not decode video (a 13GB source blew the 600s cap on full-video decode) ----
def test_silence_cmd_is_audio_only():
    cmd = _silence_cmd("/x/in.mp4")
    assert "-vn" in cmd                                               # no video decode for a pure audio filter
    assert "silencedetect=noise=-30dB:d=0.5" in " ".join(cmd)         # still the same detector
    assert "-f" in cmd and "null" in cmd                             # null sink (analysis only)

def test_energy_cmd_is_audio_only():
    from fanops.audio_energy import energy_cmd
    cmd = energy_cmd("/x/in.mp4")
    assert "-vn" in cmd                                               # astats is audio-only — never decode video
    assert "astats=metadata=1:reset=1" in " ".join(cmd)               # existing filter unchanged

def test_scene_cmd_still_decodes_video():
    # scdet detects VISUAL cuts — it MUST keep decoding video (no -vn). Guards against a copy-paste of the fix.
    assert "-vn" not in _scene_cmd("/x/in.mp4")


# ---- MOL-120: the scene (video) pass timeout must scale with source duration, not a fixed 600s ----
def test_scene_timeout_scales_with_duration():
    assert _scene_timeout(None) == 600.0                             # unknown duration -> the 600s floor
    assert _scene_timeout(30.0) == 600.0                             # short source -> floor (max wins)
    assert _scene_timeout(1455.0) == 5820.0                          # 1455s * 4.0 headroom (the live DJI source)

def test_detect_signals_wires_duration_scaled_scene_timeout(tmp_path, mocker):
    # The scene pass on a long source must carry a duration-scaled timeout; the audio passes keep the 600s floor.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, duration=1455.0, meta={"transcribed": True}))
    seen = {}                                                        # timeout per command kind (last write wins per kind)
    def rec(cmd, **kw):
        joined = " ".join(cmd)
        kind = "scene" if "scdet" in joined else ("energy" if "astats" in joined else "silence")
        seen[kind] = kw.get("timeout")
        class R:
            returncode = 0; stdout = ""
            stderr = SILENCE_STDERR if "silencedetect" in joined else SCENE_STDERR
        return R()
    mocker.patch("fanops.signals.subprocess.run", side_effect=rec)
    detect_signals(led, cfg, "src_1")
    assert seen["scene"] == 5820.0                                   # 1455 * 4.0 — the video pass gets headroom
    assert seen["silence"] == 600.0 and seen["energy"] == 600.0      # audio passes keep the fixed floor


# ---- MOL-122: the in-lock reduce pass must never shell slow ffmpeg — adopt-or-defer on a cold sidecar ----
def test_detect_signals_in_lock_defers_when_sidecar_cold(tmp_path, mocker):
    # The reducer runs detect_signals INSIDE the ledger flock, meant only to ADOPT the producer's warm
    # sidecar. If the producer failed (cold sidecar), an in-lock ffmpeg run would hold the flock for up to
    # the (now duration-scaled) scene timeout — an hour on a long source. in_lock=True must DEFER instead:
    # no subprocess, source stays `transcribed`, a breadcrumb logged; the next producer tick re-warms it.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, duration=1455.0, meta={"transcribed": True}))
    mocker.patch("fanops.signals.shutil.which", return_value="/usr/bin/ffmpeg")   # toolchain present -> defer, don't quarantine
    spy = mocker.patch("fanops.signals.subprocess.run")
    led = detect_signals(led, cfg, "src_1", in_lock=True)
    spy.assert_not_called()                                          # NO slow ffmpeg under the flock
    assert led.sources["src_1"].state is SourceState.transcribed     # left for the next producer pass

def test_detect_signals_in_lock_absent_toolchain_still_raises(tmp_path, mocker):
    # A genuinely-absent toolchain fails in microseconds (no flock risk) and must STILL quarantine — the
    # in-lock defer applies only to the SLOW work, never to a real toolchain-missing failure that would
    # otherwise spin the source `transcribed` forever. The cheap PATH probe raises the typed error in-lock.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, duration=1455.0, meta={"transcribed": True}))
    mocker.patch("fanops.signals.shutil.which", return_value=None)   # ffmpeg absent from PATH
    with pytest.raises(ToolchainMissingError, match="ffmpeg"):
        detect_signals(led, cfg, "src_1", in_lock=True)

def test_detect_signals_in_lock_adopts_warm_sidecar(tmp_path, mocker):
    # in_lock with a VALID v3 sidecar adopts it exactly like today — byte-identical, no ffmpeg.
    import json
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    from fanops.signals import _SIDECAR_V
    sc = cfg.agent_io / "signals"; sc.mkdir(parents=True, exist_ok=True)
    (sc / "src_1.json").write_text(json.dumps(
        {"v": _SIDECAR_V, "peaks": [{"t": 4.0, "kind": "speech_resume", "score": 0.5}], "duration": 12.0}))
    spy = mocker.patch("fanops.signals.subprocess.run")
    led = detect_signals(led, cfg, "src_1", in_lock=True)
    spy.assert_not_called()
    assert led.sources["src_1"].state is SourceState.signalled and led.sources["src_1"].duration == 12.0

def test_detect_signals_producer_path_still_runs_ffmpeg(tmp_path, mocker):
    # The producer path (in_lock default False) is unchanged — it may run long, that's its job.
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          state=SourceState.transcribed, meta={"transcribed": True}))
    def fake_run(cmd, **kw):
        joined = " ".join(cmd)
        class R:
            returncode = 0; stdout = ""
            stderr = SILENCE_STDERR if "silencedetect" in joined else SCENE_STDERR
        return R()
    spy = mocker.patch("fanops.signals.subprocess.run", side_effect=fake_run)
    mocker.patch("fanops.signals.probe_dimensions", return_value=(1920, 1080, 12.0))
    led = detect_signals(led, cfg, "src_1")                          # default: producer path
    spy.assert_called()                                             # ffmpeg ran (warms the sidecar)
    assert led.sources["src_1"].state is SourceState.signalled


# ---- MOL-158 (P4b): content_focus INTENSITY filters the peak set — NET-NEW, no energy lever ----
def _score(p):
    return float(p.get("energy") or p.get("score") or 0.0)

def _sample_peaks():
    return [{"t": 1.0, "kind": "speech_resume", "score": 0.15, "energy": 0.15},
            {"t": 2.0, "kind": "speech_resume", "score": 0.45, "energy": 0.45},
            {"t": 3.0, "kind": "speech_resume", "score": 0.70, "energy": 0.70},
            {"t": 4.0, "kind": "speech_resume", "score": 0.95, "energy": 0.95}]

def test_intensity_filter_high_keeps_loud():
    peaks = _sample_peaks()
    out = filter_peaks_by_intensity(peaks, "high")
    assert out and max(_score(p) for p in out) >= 0.95
    assert all(_score(p) >= _score(peaks[2]) for p in out)

def test_intensity_filter_low_keeps_calm():
    peaks = _sample_peaks()
    out = filter_peaks_by_intensity(peaks, "low")
    assert out and max(_score(p) for p in out) <= 0.45
    assert all(_score(p) <= _score(peaks[1]) for p in out)

def test_intensity_filter_neutral_unchanged():
    peaks = _sample_peaks()
    for neutral in (None, "", "neutral", "medium"):
        assert filter_peaks_by_intensity(peaks, neutral) is peaks

def test_intensity_filter_is_new_function():
    import fanops.signals as sig
    assert callable(filter_peaks_by_intensity)
    assert not hasattr(sig, "filter_peaks_by_energy")

def test_no_energy_key_referenced():
    import inspect
    assert "energy" not in inspect.signature(filter_peaks_by_intensity).parameters
