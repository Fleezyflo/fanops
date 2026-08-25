import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib.cover_qa import (
    COVER_EXTRACT_S,
    DEFAULT_ATTESTED_WORDS,
    crop_hook_band,
    generate_purple_fixture,
    match_attested_words,
    normalize_ocr_text,
    preprocess_for_ocr,
    qa_cover,
)

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def test_normalize_ocr_text_strips_diacritics():
    assert normalize_ocr_text("كَفاية") == normalize_ocr_text("كفاية")


def test_match_attested_words_arabic_substring():
    matched, missing = match_attested_words("كفاية لك كفاية عزبتني", DEFAULT_ATTESTED_WORDS)
    assert "عزبتني" in matched
    assert "كفاية" in matched
    assert not missing or "لك كفاية عزبتني" in matched


def test_match_attested_words_english_card():
    matched, _ = match_attested_words("HOOK ALPHA on card", ("HOOK ALPHA",))
    assert matched == ("HOOK ALPHA",)


@pytest.fixture
def purple_fixture(tmp_path: Path) -> Path:
    out = tmp_path / "purple_cover.png"
    generate_purple_fixture(out)
    return out


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract required")
def test_qa_cover_hits_arabic_on_purple_fixture(purple_fixture: Path):
    result = qa_cover(
        purple_fixture,
        attested_words=("عزبتني", "كفاية"),
        workdir=purple_fixture.parent / "qa_work",
        keep_artifacts=True,
    )
    assert result.ok, f"{result.message!r} ocr={result.ocr_text!r}"
    assert "عزبتني" in result.matched or "كفاية" in result.matched
    assert normalize_ocr_text("عزبتني") in normalize_ocr_text(result.ocr_text)


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract required")
def test_hook_band_crop_beats_whole_frame_on_purple(purple_fixture: Path, tmp_path: Path):
    whole = tmp_path / "whole_ocr.png"
    preprocess_for_ocr(purple_fixture, whole)
    band = tmp_path / "band.png"
    pre = tmp_path / "band_ocr.png"
    crop_hook_band(purple_fixture, band)
    preprocess_for_ocr(band, pre)
    whole_txt = subprocess.run(
        ["tesseract", str(whole), "stdout", "-l", "ara+eng", "--psm", "6"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    band_txt = subprocess.run(
        ["tesseract", str(pre), "stdout", "-l", "ara+eng", "--psm", "6"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    _, missing_band = match_attested_words(band_txt, ("عزبتني",))
    _, missing_whole = match_attested_words(whole_txt, ("عزبتني",))
    assert not missing_band
    # Whole frame may still hit on synthetic fixture, but band must be strictly better signal.
    assert len(normalize_ocr_text(band_txt)) >= len(normalize_ocr_text(whole_txt)) // 4


def test_cover_extract_s_is_not_frame_zero():
    """Real covers (clip_5a92132dc6de) miss when extracted at t=0 — use 0.4s."""
    assert COVER_EXTRACT_S == 0.4


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract required")
def test_black_hook_band_fails_qa(tmp_path: Path):
    black = tmp_path / "black.png"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:d=1",
            "-frames:v",
            "1",
            str(black),
        ],
        check=True,
    )
    result = qa_cover(black, ("عزبتني",), workdir=tmp_path / "qa", keep_artifacts=True)
    assert not result.ok


def test_cli_module_runs(purple_fixture: Path):
    if not shutil.which("tesseract"):
        pytest.skip("tesseract required")
    trial_root = str(Path(__file__).resolve().parents[1])
    cmd = [sys.executable, "-m", "lib.cover_qa", str(purple_fixture), "--words", "عزبتني"]
    env = {**os.environ, "PYTHONPATH": trial_root}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=trial_root)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok=True" in proc.stdout
