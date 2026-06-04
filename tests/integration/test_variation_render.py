import subprocess, shutil
from pathlib import Path
import pytest
import fanops.overlay as overlay

REQUIRE = pytest.mark.integration


@REQUIRE
def test_two_accounts_get_distinct_burned_hooks(tmp_path):
    if not overlay.ffmpeg_has_textfilter():
        pytest.skip("ffmpeg lacks text filters (libass) — burned-hook variation not provable here")
    # a real base clip
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=navy:s=720x1280:d=4", "-f", "lavfi", "-i", "sine=frequency=300:d=4",
                    "-shortest", str(base), "-y"], check=True)
    out_a = tmp_path / "a.mp4"; out_b = tmp_path / "b.mp4"
    ok_a = overlay.burn_hook_only(str(base), str(out_a), "HOOK ALPHA", width=720, height=1280)
    ok_b = overlay.burn_hook_only(str(base), str(out_b), "HOOK BETA", width=720, height=1280)
    assert ok_a and ok_b and out_a.exists() and out_b.exists()
    # the two per-account files DIFFER from each other and from the base (different burned text)
    assert out_a.read_bytes() != out_b.read_bytes()
    assert out_a.stat().st_size != base.stat().st_size
    # OCR proof if tesseract is available (else the differ proof above stands). The hook is amber
    # (RGB FFC800) on dark footage; tesseract reads coloured-on-dark text far better from a
    # grayscale frame, so the extract pass flattens to `format=gray` (the text is genuinely burned
    # either way — this only makes it OCR-legible, not present).
    if shutil.which("tesseract"):
        fa = tmp_path / "fa.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "1.0", "-i", str(out_a),
                        "-frames:v", "1", "-vf", "format=gray", str(fa), "-y"], check=True)
        txt = subprocess.run(["tesseract", str(fa), "-", "--psm", "6"], capture_output=True, text=True).stdout.upper()
        assert "ALPHA" in txt
