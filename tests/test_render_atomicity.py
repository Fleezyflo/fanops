import shutil
import subprocess
import pytest
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, MomentState, ClipState, Fmt
from fanops.clip import render_moment, render_reframed


def _muxer_strict_ffmpeg(cmd, **kw):
    """A fake ffmpeg that behaves like the REAL binary w.r.t. the output muxer: it infers the
    container from the output path's extension, so an output path NOT ending in `.mp4` fails to
    initialize the muxer — returncode 1, NO file created (exactly the MOL-78 CI failure). A `.mp4`
    output writes a nonempty payload and exits 0."""
    out = Path(cmd[-1])
    if out.suffix != ".mp4":
        class R: returncode = 1; stderr = f"Error initializing the muxer for {out.name}: Invalid argument"; stdout = ""
        return R()                                            # like real ffmpeg: no output file produced
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"MUXED-OK")
    class R: returncode = 0; stderr = ""; stdout = ""
    return R()


@pytest.fixture(autouse=True)
def _cv_off(monkeypatch):
    monkeypatch.setenv("FANOPS_CREATIVE_VARIATION", "0")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")            # isolate from the frame probe
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "0")           # blind centered crop, single-pass path
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")


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
    # no orphan temp either (the muxer-inferable temp is `<dst>.part.mp4`; the bare `.part` is never used)
    assert not (cfg.clips / f"{c.id}.mp4.part.mp4").exists()
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
    assert not (cfg.clips / f"{c.id}.mp4.part.mp4").exists()  # muxer-inferable temp swept
    assert not (cfg.clips / f"{c.id}.mp4.part").exists()
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
    # the internal temp keeps a muxer-inferable .mp4 suffix (MOL-78): `<dst>.part.mp4`, not `<dst>.part`.
    assert not (tmp_path / "out.mp4.part.mp4").exists(), "temp not swept on timeout"
    assert not (tmp_path / "out.mp4.part").exists()


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


# --- RED 5 (MOL-78 CI root cause): the internal temp must keep a muxer-inferable .mp4 suffix ---
# Real ffmpeg picks the container from the output extension; a `.part` temp fails "Error initializing
# the muxer", produces NO file, rc!=0 -> os.replace never runs -> dst never created. The E2E job caught
# this (unit tests stubbed ffmpeg and passed). This test uses a muxer-STRICT fake so it reproduces the
# failure with a mocked subprocess: it must FAIL on the pre-fix `str(dst)+".part"` temp, PASS after.
def test_render_reframed_temp_keeps_mp4_suffix_for_muxer(tmp_path, mocker):
    dst = tmp_path / "out.mp4"
    mocker.patch("fanops.clip.subprocess.run", side_effect=_muxer_strict_ffmpeg)
    r = render_reframed(str(tmp_path / "s.mp4"), str(dst), 0.0, 7.0, "9:16", src_w=1920, src_h=1080)
    assert r.returncode == 0, "ffmpeg failed to init the muxer -> internal temp did not end in .mp4"
    assert dst.exists() and dst.read_bytes() == b"MUXED-OK"   # os.replace ran because the temp muxed
    assert not (tmp_path / "out.mp4.part.mp4").exists()       # internal temp swept
    assert not (tmp_path / "out.mp4.part").exists()


# --- RED 6 (render_account_cut heal): a non-.mp4 dst still publishes through render_reframed ---
# render_account_cut passes its OWN `<out>.part` as render_reframed's dst arg. Pre-fix, ffmpeg wrote
# directly to a `.part` path -> muxer failure -> the path could NEVER be produced (silent fail-open).
# After the fix, render_reframed muxes internally to `<dst>.part.mp4` and os.replaces onto the given
# dst whatever its extension. Pin that a non-.mp4 dst is still published.
def test_render_reframed_publishes_non_mp4_dst(tmp_path, mocker):
    dst = tmp_path / "out.mp4.part"                           # render_account_cut's tmp (non-.mp4 extension)
    mocker.patch("fanops.clip.subprocess.run", side_effect=_muxer_strict_ffmpeg)
    r = render_reframed(str(tmp_path / "s.mp4"), str(dst), 0.0, 7.0, "9:16", src_w=1920, src_h=1080)
    assert r.returncode == 0, "muxer-strict ffmpeg refused the output -> internal temp lacked .mp4"
    assert dst.exists() and dst.read_bytes() == b"MUXED-OK"   # published verbatim to the non-.mp4 dst
    assert not (tmp_path / "out.mp4.part.part.mp4").exists()  # internal temp swept


# --- Real-ffmpeg smoke: gated exactly like the repo's toolchain tests (shutil.which) ---
@pytest.mark.integration
@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="real ffmpeg/ffprobe required")
def test_render_reframed_real_ffmpeg_lands_nonempty(tmp_path):
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=1",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1",
                    "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(src)],
                   check=True, capture_output=True, text=True)
    dst = tmp_path / "out.mp4"
    r = render_reframed(str(src), str(dst), 0.0, 1.0, "9:16", src_w=1920, src_h=1080)
    assert r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nk=1:nw=1", str(dst)], capture_output=True, text=True)
    assert probe.returncode == 0 and probe.stdout.strip()     # ffprobe reads a real muxed file at dst
