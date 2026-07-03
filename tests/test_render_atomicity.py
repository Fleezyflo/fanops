import subprocess
import pytest
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, ClipState, Fmt
from fanops.clip import render_moment, render_reframed


@pytest.fixture(autouse=True)
def _cv_off(monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")            # isolate from the frame probe
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")           # blind centered crop, single-pass path
    monkeypatch.delenv("FANOPS_BURN_SUBS", raising=False)


def _seed(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(cfg.sources / "src_1.mp4"),
                          width=1920, height=1080, duration=30.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7",
                          start=0, end=7, reason="r", state=MomentState.decided))
    return led


# --- RED 1: a subprocess failure that has ALREADY written a partial file must leave NO torn file at dst ---
def test_partial_write_then_nonzero_leaves_no_file_at_dst(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    # ffmpeg writes a PARTIAL/corrupt payload to its output path, THEN reports failure (rc=1).
    # The bug: without staging, ffmpeg's output path IS the final content-addressed dst, so the
    # partial file is observable at dst by a concurrent reader before the error is even detected.
    def partial_fail(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PARTIAL-TORN-MUX")                  # a real, non-empty, corrupt payload
        class R: returncode = 1; stderr = "muxer: broken pipe"; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=partial_fail)
    led, c = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    real_dst = cfg.clips / f"{c.id}.mp4"
    assert c.state is ClipState.error
    # THE CONTRACT: the final content-addressed dst must be absent (never a torn file), even though
    # the fake ffmpeg physically wrote a partial payload to its output path mid-run.
    assert not real_dst.exists(), "torn/partial file survived at the final content-addressed path"
    # no orphan .part temp either
    assert not (cfg.clips / f"{c.id}.mp4.part").exists()
    assert led.moments["mom_1"].state is MomentState.decided  # retriable


# --- RED 2: happy path still lands the COMPLETE file at the exact dst + stamps the fingerprint sidecar ---
def test_happy_path_lands_complete_file_and_fingerprint(tmp_path, mocker):
    cfg = Config(root=tmp_path); led = _seed(cfg)
    def ok(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"COMPLETE-CLIP-BYTES")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=ok)
    led, c = render_moment(led, cfg, "mom_1", aspect=Fmt.r9x16)
    real_dst = cfg.clips / f"{c.id}.mp4"
    assert c.state is ClipState.rendered
    assert real_dst.exists() and real_dst.read_bytes() == b"COMPLETE-CLIP-BYTES"
    assert (cfg.clips / f"{c.id}.render.json").exists()       # fingerprint sidecar stamped after success
    assert not (cfg.clips / f"{c.id}.mp4.part").exists()      # temp swept
    assert led.moments["mom_1"].state is MomentState.clipped


# --- RED 3: render_reframed itself — a timeout mid-write must not leave a file at its dst arg + propagate ---
def test_render_reframed_timeout_sweeps_temp_and_propagates(tmp_path, mocker):
    dst = tmp_path / "out.mp4"
    def timeout_after_partial(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"HALF")                              # partial written before the hang is killed
        raise subprocess.TimeoutExpired(cmd, 600.0)
    mocker.patch("fanops.clip.subprocess.run", side_effect=timeout_after_partial)
    with pytest.raises(subprocess.TimeoutExpired):
        render_reframed(str(tmp_path / "s.mp4"), str(dst), 0.0, 7.0, "9:16", src_w=1920, src_h=1080)
    assert not dst.exists(), "timeout left a torn file at render_reframed's dst"
    assert not (tmp_path / "out.mp4.part").exists(), "temp not swept on timeout"


# --- RED 4: render_reframed success replaces onto the exact dst arg (contract for render_account_cut's tmp) ---
def test_render_reframed_success_lands_at_dst_arg(tmp_path, mocker):
    dst = tmp_path / "out.mp4.part"                           # render_account_cut passes its OWN .part here
    def ok(cmd, **kw):
        out = Path(cmd[-1]); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"GOOD")
        class R: returncode = 0; stderr = ""; stdout = ""
        return R()
    mocker.patch("fanops.clip.subprocess.run", side_effect=ok)
    r = render_reframed(str(tmp_path / "s.mp4"), str(dst), 0.0, 7.0, "9:16", src_w=1920, src_h=1080)
    assert r.returncode == 0
    assert dst.exists() and dst.read_bytes() == b"GOOD"       # final content landed at the dst arg verbatim
