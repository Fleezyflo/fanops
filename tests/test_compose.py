# tests/test_compose.py — Produced-clip compositing (MoviePy layer). The ORCHESTRATION + FAIL-OPEN
# contract is proven here with an INJECTED renderer (no MoviePy needed): an empty spec, a missing
# MoviePy (ImportError), any render error, or a missing/empty output all degrade to a byte-copy of
# the base clip so the caller ALWAYS gets a usable file. The REAL MoviePy 2.x render is exercised by
# the single `integration`-marked test (skipped unless moviepy + ffmpeg are present).
import subprocess
from pathlib import Path
import pytest
from fanops.compose import compose_clip, prepend_intro, TemplateSpec


def _basefile(tmp_path, name="base.mp4", data=b"BASECLIP"):
    p = tmp_path / name; p.write_bytes(data); return p


def test_template_spec_is_empty():
    assert TemplateSpec().is_empty() is True
    assert TemplateSpec(title="x").is_empty() is False
    assert TemplateSpec(intro_text="MOH FLOW").is_empty() is False
    assert TemplateSpec(outro_text="@h").is_empty() is False

def test_empty_spec_failopens_to_base_copy(tmp_path):
    base = _basefile(tmp_path); out = tmp_path / "out.mp4"
    ok = compose_clip(str(base), str(out), TemplateSpec(),
                      render=lambda *a, **k: pytest.fail("renderer must NOT run on an empty spec"))
    assert ok is False and out.read_bytes() == b"BASECLIP"      # base copied through, renderer skipped

def test_success_returns_true_and_keeps_renderer_output(tmp_path):
    base = _basefile(tmp_path); out = tmp_path / "out.mp4"
    def fake_render(b, o, spec, *, timeout): Path(o).write_bytes(b"COMPOSED")
    ok = compose_clip(str(base), str(out), TemplateSpec(title="Hi"), render=fake_render)
    assert ok is True and out.read_bytes() == b"COMPOSED"

def test_render_exception_failopens_to_base(tmp_path):
    base = _basefile(tmp_path); out = tmp_path / "out.mp4"; logs = []
    def boom(b, o, spec, *, timeout): raise RuntimeError("moviepy API drift")
    ok = compose_clip(str(base), str(out), TemplateSpec(title="Hi"), render=boom, log=logs.append)
    assert ok is False and out.read_bytes() == b"BASECLIP"
    assert any("compose failed" in m for m in logs)            # logged once, not swallowed silently

def test_importerror_failopens_to_base(tmp_path):
    # moviepy absent (a no-[compose] install) -> the broad except catches ImportError, base is used.
    base = _basefile(tmp_path); out = tmp_path / "out.mp4"
    def absent(b, o, spec, *, timeout): raise ImportError("No module named 'moviepy'")
    ok = compose_clip(str(base), str(out), TemplateSpec(title="x"), render=absent)
    assert ok is False and out.read_bytes() == b"BASECLIP"

def test_renderer_no_output_failopens(tmp_path):
    base = _basefile(tmp_path); out = tmp_path / "out.mp4"
    def noop(b, o, spec, *, timeout): pass                     # writes nothing
    ok = compose_clip(str(base), str(out), TemplateSpec(title="Hi"), render=noop)
    assert ok is False and out.read_bytes() == b"BASECLIP"

def test_renderer_empty_output_failopens(tmp_path):
    base = _basefile(tmp_path); out = tmp_path / "out.mp4"
    def empty(b, o, spec, *, timeout): Path(o).write_bytes(b"")
    ok = compose_clip(str(base), str(out), TemplateSpec(title="Hi"), render=empty)
    assert ok is False and out.read_bytes() == b"BASECLIP"

def test_timeout_is_passed_to_renderer(tmp_path):
    base = _basefile(tmp_path); out = tmp_path / "out.mp4"; seen = {}
    def r(b, o, spec, *, timeout): seen["t"] = timeout; Path(o).write_bytes(b"X")
    compose_clip(str(base), str(out), TemplateSpec(title="Hi"), timeout=12.5, render=r)
    assert seen["t"] == 12.5


@pytest.mark.integration
def test_real_moviepy_prepend_intro_continuous_audio(tmp_path):
    # The REAL MoviePy 2.x compose-PREPEND: a 2s intro before a 6s base (with a tone "music bed") must yield
    # an 8s composite whose AUDIO spans the FULL duration — the PRD's continuous-bed requirement (no silent
    # opener, no tail gap). Skipped unless moviepy + ffmpeg/ffprobe are present (CI e2e installs .[compose]).
    pytest.importorskip("moviepy")
    import shutil
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg + ffprobe required for the real prepend render")
    base = tmp_path / "base.mp4"; intro = tmp_path / "intro.mp4"; out = tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=180x320:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-pix_fmt", "yuv420p",
                    "-shortest", str(base)], capture_output=True, check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=720x1280:d=3",
                    "-pix_fmt", "yuv420p", str(intro)], capture_output=True, check=True)
    ok = prepend_intro(str(base), str(intro), str(out), tease_text="wait for it", intro_seconds=2.0)
    assert ok is True and out.exists()
    a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=duration", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True)
    assert float(a.stdout.strip()) >= 7.5          # the music bed spans the full ~8s composite (continuous, no gap)


@pytest.mark.integration
def test_real_moviepy_compose_produces_longer_clip(tmp_path):
    # The REAL MoviePy 2.x render: intro card + titled base clip + outro card, crossfaded. Skipped
    # unless moviepy + ffmpeg are present (CI e2e installs .[compose]); verifies the v2 API end-to-end.
    pytest.importorskip("moviepy")
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for the real compose render")
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=180x320:d=2",
                    "-pix_fmt", "yuv420p", str(base)], capture_output=True, check=True)
    out = tmp_path / "out.mp4"
    spec = TemplateSpec(title="THE BEAT DROPS", intro_text="MOH FLOW", outro_text="@moh.flow",
                        card_sec=0.6, title_sec=1.0, transition_sec=0.3)
    ok = compose_clip(str(base), str(out), spec)
    assert ok is True and out.exists() and out.stat().st_size > 0
    from moviepy import VideoFileClip
    with VideoFileClip(str(out)) as v:
        assert v.duration > 2.0                                # intro + outro cards extend the 2s base


# ---- M6 (intro-tease): the compose fingerprint — compose.py has none today (always re-renders). It lets
# the lock-free prewarm + in-lock commit agree on when an existing composed mp4 may be adopted (no MoviePy
# under the flock), exactly like clip._render_fingerprint does for the base render. Pure, no MoviePy. ----
def test_compose_fingerprint_is_deterministic():
    from fanops.compose import _compose_fingerprint
    fp1 = _compose_fingerprint("/s/base.mp4", "/s/intro.mp4", {"tease_text": "wait for it"}, 1080, 1920)
    fp2 = _compose_fingerprint("/s/base.mp4", "/s/intro.mp4", {"tease_text": "wait for it"}, 1080, 1920)
    assert fp1 == fp2 and isinstance(fp1, str) and len(fp1) == 64   # sha256 hex, stable

def test_compose_fingerprint_changes_with_any_input():
    from fanops.compose import _compose_fingerprint
    base = _compose_fingerprint("/s/base.mp4", "/s/intro.mp4", {"tease_text": "wait for it"}, 1080, 1920)
    assert _compose_fingerprint("/s/OTHER.mp4", "/s/intro.mp4", {"tease_text": "wait for it"}, 1080, 1920) != base
    assert _compose_fingerprint("/s/base.mp4", "/s/OTHER.mp4", {"tease_text": "wait for it"}, 1080, 1920) != base
    assert _compose_fingerprint("/s/base.mp4", "/s/intro.mp4", {"tease_text": "DIFFERENT"}, 1080, 1920) != base
    assert _compose_fingerprint("/s/base.mp4", "/s/intro.mp4", {"tease_text": "wait for it"}, 720, 1280) != base


# ---- M6 Task 2: prepend_intro — the compose-PREPEND primitive. Prepends an aspect-normalized intro
# (video/photo) before the base clip, over a CONTINUOUS music bed (PRD: no silent opener). FAIL-OPEN to
# a base byte-copy exactly like compose_clip; the validity gate is DURATION-based (expected = intro_seconds
# + base_dur, within impact_cut.DURATION_TOLERANCE) — a render that drops audio/frames lands a wrong total
# duration and is rejected. The injected render + probe seams prove the contract with no MoviePy/ffprobe. ----
def _stub_probe(durs):
    # path -> duration map; unknown path -> None (unprobeable). Mirrors the real clip._probe_duration shape.
    return lambda path: durs.get(path)

def test_prepend_missing_intro_failopens(tmp_path):
    base = _basefile(tmp_path); out = tmp_path / "out.mp4"; logs = []
    ok = prepend_intro(str(base), str(tmp_path / "nope.mp4"), str(out), tease_text="wait for it",
                       intro_seconds=2.0, log=logs.append,
                       render=lambda *a, **k: pytest.fail("renderer must NOT run when the intro asset is missing"),
                       probe_duration=_stub_probe({}))
    assert ok is False and out.read_bytes() == b"BASECLIP"
    assert any("intro asset missing" in m for m in logs)

def test_prepend_success_returns_true(tmp_path):
    base = _basefile(tmp_path); intro = _basefile(tmp_path, "intro.mp4", b"INTRO"); out = tmp_path / "out.mp4"
    def fake_render(b, i, o, *, tease_text, intro_seconds, timeout): Path(o).write_bytes(b"PREPENDED")
    # base 8s + intro 2s -> expected 10s; the (faked) probe reports exactly 10s for the output -> valid.
    ok = prepend_intro(str(base), str(intro), str(out), tease_text="wait for it", intro_seconds=2.0,
                       render=fake_render, probe_duration=_stub_probe({str(base): 8.0, str(out): 10.0}))
    assert ok is True and out.read_bytes() == b"PREPENDED"

def test_prepend_render_exception_failopens(tmp_path):
    base = _basefile(tmp_path); intro = _basefile(tmp_path, "intro.mp4", b"INTRO"); out = tmp_path / "out.mp4"; logs = []
    def boom(b, i, o, *, tease_text, intro_seconds, timeout): raise RuntimeError("moviepy audio compositing failed")
    ok = prepend_intro(str(base), str(intro), str(out), tease_text="x", intro_seconds=2.0,
                       render=boom, probe_duration=_stub_probe({str(base): 8.0}), log=logs.append)
    assert ok is False and out.read_bytes() == b"BASECLIP"
    assert any("prepend failed" in m for m in logs)

def test_prepend_no_output_failopens(tmp_path):
    base = _basefile(tmp_path); intro = _basefile(tmp_path, "intro.mp4", b"INTRO"); out = tmp_path / "out.mp4"
    def noop(b, i, o, *, tease_text, intro_seconds, timeout): pass        # writes nothing
    ok = prepend_intro(str(base), str(intro), str(out), tease_text="x", intro_seconds=2.0,
                       render=noop, probe_duration=_stub_probe({str(base): 8.0, str(out): 10.0}))
    assert ok is False and out.read_bytes() == b"BASECLIP"

def test_prepend_duration_mismatch_failopens(tmp_path):
    # The "silent/gap" failure proxy: the renderer drops the intro segment, so the output is base-length
    # (8s) not the expected 10s — outside DURATION_TOLERANCE -> rejected, fail-open to the bare base.
    base = _basefile(tmp_path); intro = _basefile(tmp_path, "intro.mp4", b"INTRO"); out = tmp_path / "out.mp4"; logs = []
    def short(b, i, o, *, tease_text, intro_seconds, timeout): Path(o).write_bytes(b"TOOSHORT")
    ok = prepend_intro(str(base), str(intro), str(out), tease_text="x", intro_seconds=2.0,
                       render=short, probe_duration=_stub_probe({str(base): 8.0, str(out): 8.0}), log=logs.append)
    assert ok is False and out.read_bytes() == b"BASECLIP"
    assert any("duration" in m for m in logs)

def test_prepend_unprobeable_output_failopens(tmp_path):
    # Can't prove validity (probe returns None for the output) -> never ship a possibly-broken composite.
    base = _basefile(tmp_path); intro = _basefile(tmp_path, "intro.mp4", b"INTRO"); out = tmp_path / "out.mp4"
    def r(b, i, o, *, tease_text, intro_seconds, timeout): Path(o).write_bytes(b"PREPENDED")
    ok = prepend_intro(str(base), str(intro), str(out), tease_text="x", intro_seconds=2.0,
                       render=r, probe_duration=_stub_probe({str(base): 8.0}))   # out path -> None
    assert ok is False and out.read_bytes() == b"BASECLIP"

def test_prepend_passes_intro_tease_and_seconds_to_renderer(tmp_path):
    base = _basefile(tmp_path); intro = _basefile(tmp_path, "intro.mp4", b"INTRO"); out = tmp_path / "out.mp4"; seen = {}
    def r(b, i, o, *, tease_text, intro_seconds, timeout):
        seen.update(intro=i, tease=tease_text, secs=intro_seconds, t=timeout); Path(o).write_bytes(b"X")
    prepend_intro(str(base), str(intro), str(out), tease_text="3 incoming", intro_seconds=1.5, timeout=42.0,
                  render=r, probe_duration=_stub_probe({str(base): 8.0, str(out): 9.5}))
    assert seen == {"intro": str(intro), "tease": "3 incoming", "secs": 1.5, "t": 42.0}
