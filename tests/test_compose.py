# tests/test_compose.py — Produced-clip compositing (MoviePy layer). The ORCHESTRATION + FAIL-OPEN
# contract is proven here with an INJECTED renderer (no MoviePy needed): an empty spec, a missing
# MoviePy (ImportError), any render error, or a missing/empty output all degrade to a byte-copy of
# the base clip so the caller ALWAYS gets a usable file. The REAL MoviePy 2.x render is exercised by
# the single `integration`-marked test (skipped unless moviepy + ffmpeg are present).
import subprocess
from pathlib import Path
import pytest
from fanops.compose import compose_clip, TemplateSpec


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
