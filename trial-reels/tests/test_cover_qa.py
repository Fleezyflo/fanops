import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib.captions import DEFAULT_MARGIN_V
from lib.cover_qa import (
    COVER_EXTRACT_S,
    DEFAULT_ATTESTED_WORDS,
    generate_ass_stamp_cover,
    hook_band_crop_filter,
    hook_band_geometry,
    match_attested_words,
    normalize_ocr_text,
    ocr_langs_for_language,
    preprocess_for_ocr,
    qa_cover,
)

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIVE_COVERS_ENV = "TRIAL_REELS_LIVE_COVERS"


def test_normalize_ocr_text_strips_diacritics():
    assert normalize_ocr_text("كَفاية") == normalize_ocr_text("كفاية")


def test_ocr_langs_for_language():
    assert ocr_langs_for_language("en") == "eng"
    assert ocr_langs_for_language("ar") == "ara"
    assert ocr_langs_for_language(None) == "ara+eng"


def test_match_attested_words_arabic_substring():
    matched, missing = match_attested_words("كفاية لك كفاية عزبتني", DEFAULT_ATTESTED_WORDS)
    assert "عزبتني" in matched
    assert "كفاية" in matched
    assert not missing or "لك كفاية عزبتني" in matched


def test_match_attested_words_english_card():
    matched, _ = match_attested_words("HOOK ALPHA on card", ("HOOK ALPHA",))
    assert matched == ("HOOK ALPHA",)


def test_hook_band_crop_targets_margin_v_not_frame_top():
    """ASS stamp sits at MarginV 320 — crop must not start at y=0."""
    y0, h = hook_band_geometry(1920)
    assert y0 > 200
    assert y0 < DEFAULT_MARGIN_V
    assert h < 1920 * 0.15
    vf = hook_band_crop_filter()
    assert "0:ih*" in vf
    assert vf.split(":0:ih*")[1] != "0"


@pytest.fixture
def ass_stamp_cover(tmp_path: Path) -> Path:
    out = tmp_path / "ass_stamp_cover.png"
    generate_ass_stamp_cover(out, wall_overlay=True)
    return out


@pytest.fixture
def busy_ass_stamp_cover(tmp_path: Path) -> Path:
    """Poster strip above the stamp — old top-28% crop OCRs poster noise."""
    out = tmp_path / "busy_ass_stamp_cover.png"
    work = tmp_path / "busy_work"
    work.mkdir(parents=True, exist_ok=True)
    poster = work / "poster.png"
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
            "testsrc=s=1080x250",
            "-frames:v",
            "1",
            str(poster),
        ],
        check=True,
    )
    base = work / "base.png"
    generate_ass_stamp_cover(base)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(base),
            "-i",
            str(poster),
            "-filter_complex",
            "[0:v][1:v]overlay=0:0",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
    )
    return out


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract required")
def test_qa_cover_hits_arabic_on_ass_stamp_cover(ass_stamp_cover: Path):
    result = qa_cover(
        ass_stamp_cover,
        attested_words=("عزبتني", "كفاية"),
        workdir=ass_stamp_cover.parent / "qa_work",
        keep_artifacts=True,
        language="ar",
    )
    assert result.ok, f"{result.message!r} ocr={result.ocr_text!r}"
    assert "عزبتني" in result.matched or "كفاية" in result.matched
    assert normalize_ocr_text("عزبتني") in normalize_ocr_text(result.ocr_text) or normalize_ocr_text(
        "كفاية"
    ) in normalize_ocr_text(result.ocr_text)


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract required")
def test_tight_crop_beats_loose_top_band_on_busy_cover(busy_ass_stamp_cover: Path, tmp_path: Path):
    """Full cover QA passes on busy covers; loose top-28% band alone does not."""
    result = qa_cover(
        busy_ass_stamp_cover,
        attested_words=("عزبتني", "كفاية"),
        workdir=tmp_path / "qa_full",
        keep_artifacts=True,
        language="ar",
    )
    assert result.ok, f"{result.message!r} ocr={result.ocr_text!r}"

    loose = tmp_path / "loose_band.png"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(busy_ass_stamp_cover),
            "-vf",
            "crop=iw:ih*0.28:0:0",
            "-frames:v",
            "1",
            str(loose),
        ],
        check=True,
    )
    pre = tmp_path / "loose_pre.png"
    preprocess_for_ocr(loose, pre, language="ar", variant=0)
    loose_txt = subprocess.run(
        ["tesseract", str(pre), "stdout", "-l", "ara", "--psm", "11"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    _, missing_loose = match_attested_words(loose_txt, ("عزبتني", "كفاية"))
    assert missing_loose


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
    result = qa_cover(black, ("عزبتني",), workdir=tmp_path / "qa", keep_artifacts=True, language="ar")
    assert not result.ok


def test_cli_module_runs(ass_stamp_cover: Path):
    if not shutil.which("tesseract"):
        pytest.skip("tesseract required")
    trial_root = str(Path(__file__).resolve().parents[1])
    cmd = [sys.executable, "-m", "lib.cover_qa", str(ass_stamp_cover), "--words", "عزبتني", "--lang", "ar"]
    env = {**os.environ, "PYTHONPATH": trial_root}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=trial_root)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok=True" in proc.stdout


def _live_cover_cases() -> list[tuple[Path, tuple[str, ...], str]]:
    root = os.environ.get(LIVE_COVERS_ENV, "").strip()
    if not root:
        return []
    base = Path(root)
    manifest = base / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        cases: list[tuple[Path, tuple[str, ...], str]] = []
        for entry in data:
            path = base / entry["path"]
            words = tuple(entry.get("words") or DEFAULT_ATTESTED_WORDS)
            lang = entry.get("language") or "ar"
            cases.append((path, words, lang))
        return cases
    return [
        (p, DEFAULT_ATTESTED_WORDS, "ar")
        for p in sorted(base.glob("**/*"))
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".mp4"}
    ]


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract required")
@pytest.mark.parametrize(
    ("cover_path", "words", "language"),
    _live_cover_cases() or [(Path("/dev/null"), DEFAULT_ATTESTED_WORDS, "ar")],
)
def test_live_arabic_covers_hit_attested_words(cover_path: Path, words: tuple[str, ...], language: str):
    if not _live_cover_cases():
        pytest.skip(f"set {LIVE_COVERS_ENV} to a directory of live Arabic covers")
    result = qa_cover(
        cover_path,
        attested_words=words,
        workdir=cover_path.parent / ".cover_qa_live",
        keep_artifacts=True,
        language=language,
    )
    assert result.ok, f"{cover_path.name}: {result.message!r} ocr={result.ocr_text!r}"


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract required")
def test_clip_5a92132dc6de_attested_words_on_ass_stamp(tmp_path: Path):
    """clip_5a92132dc6de card phrase on a production-style ASS stamp cover."""
    transcript = json.loads((FIXTURES / "clip_5a92132dc6de.json").read_text(encoding="utf-8"))
    hook = transcript["lines"][0]["text"]
    cover = tmp_path / "clip_5a92132dc6de_cover.png"
    generate_ass_stamp_cover(cover, text=hook, wall_overlay=True)
    result = qa_cover(
        cover,
        attested_words=(hook, "عزبتني", "كفاية"),
        workdir=tmp_path / "qa",
        keep_artifacts=True,
        language=transcript.get("language") or "ar",
    )
    assert result.ok, f"{result.message!r} ocr={result.ocr_text!r}"
    assert "عزبتني" in result.matched
    assert "كفاية" in result.matched
