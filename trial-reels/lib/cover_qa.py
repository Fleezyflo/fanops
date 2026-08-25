"""Cover-frame OCR QA for trial Reels hooks.

Extracts a cover still (default t=0.4s — not frame 0, which is often black/fade),
crops the ASS hook stamp band (Alignment 8, MarginV 320), preprocesses for
tesseract, and matches attested card words. ffmpeg + tesseract only (no Pillow).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from lib.captions import (
    DEFAULT_FONTSIZE,
    DEFAULT_MARGIN_V,
    DEFAULT_OUTLINE,
    DEFAULT_SHADOW,
    write_ass,
    write_ass_file,
)

# Frame 0 is often black or pre-roll; 0.4s lands on the burned hook for clip_5a92132dc6de-style covers.
COVER_EXTRACT_S = 0.4
PLAY_RES_Y = 1920
HOOK_BAND_PAD_PX = 40
HOOK_BAND_FONT_LINES = 1.5
NOTO_NASKH_REGULAR = "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"

# Attested on-screen card phrases from clip_5a92132dc6de purple-neon covers.
DEFAULT_ATTESTED_WORDS: tuple[str, ...] = (
    "عزبتني",
    "كفاية",
    "لك كفاية عزبتني",
)

_FFMPEG_TIMEOUT = 60.0
_TESS_TIMEOUT = 30.0

_ARABIC_PREPROCESS = (
    "scale=iw*2:ih*2:flags=lanczos,format=gray,eq=contrast=2.8:brightness=0.04",
    "scale=iw*2:ih*2:flags=lanczos,format=gray,geq=lum='if(gt(lum(X,Y),175),255,0)'",
)
_ENGLISH_PREPROCESS = ("scale=iw*2:ih*2:flags=lanczos,format=gray,eq=contrast=2.2:brightness=0.04",)


def ocr_langs_for_language(language: str | None) -> str:
    """Pick tesseract language packs from clip language — never default English to ara."""
    lang = (language or "").strip().lower()
    if lang.startswith("ar"):
        return "ara"
    if lang.startswith("en"):
        return "eng"
    return "ara+eng"


def hook_band_crop_filter(
    *,
    play_res_y: int = PLAY_RES_Y,
    margin_v: int = DEFAULT_MARGIN_V,
    fontsize: int = DEFAULT_FONTSIZE,
    outline: int = DEFAULT_OUTLINE,
    shadow: int = DEFAULT_SHADOW,
    pad_px: int = HOOK_BAND_PAD_PX,
    font_lines: float = HOOK_BAND_FONT_LINES,
) -> str:
    """ffmpeg crop filter for the burned ASS hook stamp (Alignment 8 / MarginV)."""
    stamp_h = int(fontsize * font_lines + 2 * outline + shadow + pad_px * 2)
    y0 = max(0, margin_v - outline - pad_px)
    return f"crop=iw:ih*{stamp_h}/{play_res_y}:0:ih*{y0}/{play_res_y}"


def hook_band_geometry(
    frame_height: int,
    *,
    play_res_y: int = PLAY_RES_Y,
    margin_v: int = DEFAULT_MARGIN_V,
    fontsize: int = DEFAULT_FONTSIZE,
    outline: int = DEFAULT_OUTLINE,
    shadow: int = DEFAULT_SHADOW,
    pad_px: int = HOOK_BAND_PAD_PX,
    font_lines: float = HOOK_BAND_FONT_LINES,
) -> tuple[int, int]:
    """Return (y0_px, height_px) for the ASS hook band on a frame."""
    stamp_h = int(fontsize * font_lines + 2 * outline + shadow + pad_px * 2)
    y0 = max(0, margin_v - outline - pad_px)
    scale = frame_height / play_res_y
    return int(y0 * scale), max(1, int(stamp_h * scale))


@dataclass(frozen=True)
class CoverQAResult:
    ok: bool
    ocr_text: str
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    frame_path: Path | None
    band_path: Path | None
    preprocessed_path: Path | None
    extract_s: float
    message: str


def _run(cmd: list[str], *, timeout: float = _FFMPEG_TIMEOUT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)


def _ffmpeg_escape_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def extract_cover_frame(
    video_path: str | Path,
    output_path: str | Path,
    *,
    at: float = COVER_EXTRACT_S,
) -> Path:
    """Grab one cover still at `at` seconds (default 0.4s, not frame 0)."""
    src = Path(video_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # -ss before -i = input seek (fast). Prefer 0.4s over frame 0 — lead-in is often black/fade.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{at:.3f}",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-pix_fmt",
        "rgb24",
        str(out),
    ]
    r = _run(cmd)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg cover extract failed: {(r.stderr or r.stdout or '').strip()}")
    return out


def crop_hook_band(
    image_path: str | Path,
    output_path: str | Path,
    *,
    crop_filter: str | None = None,
) -> Path:
    """Crop the tight ASS hook stamp band (MarginV 320), not the whole top of frame."""
    src = Path(image_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = crop_filter or hook_band_crop_filter()
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        "-frames:v",
        "1",
        str(out),
    ]
    r = _run(cmd)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg hook-band crop failed: {(r.stderr or r.stdout or '').strip()}")
    return out


def preprocess_for_ocr(
    image_path: str | Path,
    output_path: str | Path,
    *,
    language: str | None = None,
    variant: int = 0,
) -> Path:
    """Contrast / white-stroke preprocessing for hook-band OCR (ffmpeg filters only)."""
    src = Path(image_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lang = (language or "").lower()
    if lang.startswith("en"):
        filters = _ENGLISH_PREPROCESS
    else:
        filters = _ARABIC_PREPROCESS
    vf = filters[min(variant, len(filters) - 1)]
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        "-frames:v",
        "1",
        str(out),
    ]
    r = _run(cmd)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg OCR preprocess failed: {(r.stderr or r.stdout or '').strip()}")
    return out


def ocr_image(
    image_path: str | Path,
    *,
    langs: str = "ara+eng",
    psm: int = 6,
) -> str:
    """Run tesseract on a preprocessed hook-band image."""
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract not found on PATH")
    src = Path(image_path)
    cmd = ["tesseract", str(src), "stdout", "-l", langs, "--psm", str(psm)]
    r = _run(cmd, timeout=_TESS_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"tesseract failed: {(r.stderr or r.stdout or '').strip()}")
    return (r.stdout or "").strip()


def ocr_hook_band(
    band_path: str | Path,
    *,
    language: str | None,
    langs: str,
    workdir: str | Path,
) -> str:
    """Run language-aware OCR passes on the hook band and merge text."""
    band = Path(band_path)
    tmp = Path(workdir)
    tmp.mkdir(parents=True, exist_ok=True)
    lang = (language or "").lower()
    if lang.startswith("en"):
        filters = _ENGLISH_PREPROCESS
        psms = (7,)
    else:
        filters = _ARABIC_PREPROCESS
        psms = (11, 7)

    chunks: list[str] = []
    for idx, _ in enumerate(filters):
        pre = tmp / f"hook_band_ocr_{idx}.png"
        preprocess_for_ocr(band, pre, language=language, variant=idx)
        for psm in psms:
            try:
                text = ocr_image(pre, langs=langs, psm=psm)
            except RuntimeError:
                continue
            if text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks)


_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_ALEF_VARIANTS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})


def normalize_ocr_text(text: str) -> str:
    """Normalize OCR output for fuzzy Arabic + English matching."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.translate(_ALEF_VARIANTS)
    text = re.sub(r"[^\w\s\u0600-\u06FF]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def match_attested_words(
    ocr_text: str,
    attested: tuple[str, ...] | list[str],
    *,
    min_hits: int = 1,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (matched, missing) attested phrases found in OCR output."""
    norm_ocr = normalize_ocr_text(ocr_text)
    matched: list[str] = []
    missing: list[str] = []
    for phrase in attested:
        norm_phrase = normalize_ocr_text(phrase)
        if not norm_phrase:
            continue
        if norm_phrase in norm_ocr or _fuzzy_token_hit(norm_phrase, norm_ocr):
            matched.append(phrase)
        else:
            missing.append(phrase)
    if len(matched) < min_hits:
        return tuple(matched), tuple(missing)
    return tuple(matched), tuple(missing)


def _fuzzy_token_hit(phrase: str, ocr: str) -> bool:
    """Allow one-character edit on short Arabic hook tokens (عزيتني vs عزبتني)."""
    tokens = [t for t in phrase.split() if t]
    if not tokens:
        return False
    ocr_tokens = ocr.split()
    for token in tokens:
        if any(_levenshtein_leq(token, ot, 1) for ot in ocr_tokens):
            continue
        if token not in ocr:
            return False
    return True


def _levenshtein_leq(a: str, b: str, max_dist: int) -> bool:
    if abs(len(a) - len(b)) > max_dist:
        return False
    if a == b:
        return True
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
            row_min = min(row_min, cur[j])
        if row_min > max_dist:
            return False
        prev = cur
    return prev[-1] <= max_dist


def _language_from_tess(tess_langs: str | None) -> str | None:
    if not tess_langs:
        return None
    if tess_langs == "eng":
        return "en"
    if tess_langs == "ara":
        return "ar"
    return None


def qa_cover(
    video_or_image: str | Path,
    attested_words: tuple[str, ...] | list[str] | None = None,
    *,
    extract_s: float = COVER_EXTRACT_S,
    workdir: str | Path | None = None,
    min_hits: int = 1,
    keep_artifacts: bool = False,
    tess_langs: str | None = None,
    language: str | None = None,
) -> CoverQAResult:
    """Run the full cover QA pipeline on a video or still image."""
    src = Path(video_or_image)
    if not src.exists():
        return CoverQAResult(
            ok=False,
            ocr_text="",
            matched=(),
            missing=tuple(attested_words or DEFAULT_ATTESTED_WORDS),
            frame_path=None,
            band_path=None,
            preprocessed_path=None,
            extract_s=extract_s,
            message=f"input not found: {src}",
        )
    attested = tuple(attested_words or DEFAULT_ATTESTED_WORDS)
    lang = language or _language_from_tess(tess_langs)
    langs = tess_langs or ocr_langs_for_language(lang)
    tmp = Path(workdir) if workdir else Path(src.parent) / ".cover_qa_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    frame = tmp / "cover_frame.png"
    band = tmp / "hook_band.png"
    pre = tmp / "hook_band_ocr.png"
    try:
        if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            frame = src
        else:
            extract_cover_frame(src, frame, at=extract_s)
        crop_hook_band(frame, band)
        text = ocr_hook_band(band, language=lang, langs=langs, workdir=tmp)
        preprocess_for_ocr(band, pre, language=lang, variant=0)
        matched, missing = match_attested_words(text, attested, min_hits=min_hits)
        ok = len(matched) >= min_hits
        msg = "ok" if ok else f"missing attested hook text: {', '.join(missing)}"
        return CoverQAResult(
            ok=ok,
            ocr_text=text,
            matched=matched,
            missing=missing,
            frame_path=frame,
            band_path=band,
            preprocessed_path=pre,
            extract_s=extract_s,
            message=msg,
        )
    except RuntimeError as exc:
        return CoverQAResult(
            ok=False,
            ocr_text="",
            matched=(),
            missing=attested,
            frame_path=frame if frame.exists() else None,
            band_path=band if band.exists() else None,
            preprocessed_path=pre if pre.exists() else None,
            extract_s=extract_s,
            message=str(exc),
        )
    finally:
        if not keep_artifacts and workdir is None:
            for p in (frame, band, pre):
                if p != src:
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
            for extra in tmp.glob("hook_band_ocr_*.png"):
                try:
                    extra.unlink(missing_ok=True)
                except OSError:
                    pass


def generate_ass_stamp_cover(
    output_path: str | Path,
    *,
    text: str = "لك كفاية عزبتني",
    width: int = 1080,
    height: int = 1920,
    wall_overlay: bool = False,
) -> Path:
    """Burn a production ASS hook stamp onto a purple studio still (ffmpeg + libass)."""
    out = Path(output_path)
    work = out.parent
    work.mkdir(parents=True, exist_ok=True)
    ass_path = work / "stamp.ass"
    write_ass_file(write_ass([{"start": 0.0, "end": 2.5, "text": text}], width=width, height=height), ass_path)
    sub = _ffmpeg_escape_path(ass_path)
    fontsdir = _ffmpeg_escape_path(Path(NOTO_NASKH_REGULAR).parent)
    if wall_overlay:
        filter_complex = (
            f"[0:v]scale={width}:{height},format=rgba,colorchannelmixer=aa=0.55[wall];"
            f"color=c=0x6B00A8:s={width}x{height}:d=1,format=rgba[studio];"
            f"[studio][wall]overlay=0:0:format=auto[bg];"
            f"[bg]subtitles={sub}:fontsdir={fontsdir}[o]"
        )
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=s={width}x{height}:rate=1",
            "-filter_complex",
            filter_complex,
            "-map",
            "[o]",
            "-frames:v",
            "1",
            str(out),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x6B00A8:s={width}x{height}:d=1",
            "-vf",
            f"subtitles={sub}:fontsdir={fontsdir}",
            "-frames:v",
            "1",
            str(out),
        ]
    r = _run(cmd)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ASS stamp cover generation failed: {(r.stderr or r.stdout or '').strip()}")
    return out


def generate_purple_fixture(
    output_path: str | Path,
    *,
    text_lines: tuple[str, ...] = ("كفاية", "لك كفاية عزبتني"),
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Backward-compatible alias: burn ASS stamp at MarginV 320, not drawtext at y=120."""
    text = text_lines[-1] if text_lines else "لك كفاية عزبتني"
    return generate_ass_stamp_cover(output_path, text=text, width=width, height=height)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Cover-frame OCR QA for trial Reels hooks (ASS stamp band, language-aware tesseract)."
    )
    parser.add_argument("input", help="Video or still image path")
    parser.add_argument(
        "--words",
        nargs="+",
        default=list(DEFAULT_ATTESTED_WORDS),
        help="Attested on-screen hook phrases to match",
    )
    parser.add_argument(
        "--at",
        type=float,
        default=COVER_EXTRACT_S,
        help=f"Cover extract timestamp in seconds (default {COVER_EXTRACT_S}, not frame 0)",
    )
    parser.add_argument("--min-hits", type=int, default=1, help="Minimum attested phrase hits")
    parser.add_argument("--workdir", help="Keep intermediate frames in this directory")
    parser.add_argument("--lang", help="Clip language (ar/en) for tess + preprocess routing")
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    args = parser.parse_args(argv)

    result = qa_cover(
        args.input,
        tuple(args.words),
        extract_s=args.at,
        workdir=args.workdir,
        min_hits=args.min_hits,
        keep_artifacts=bool(args.workdir),
        language=args.lang,
    )
    if args.json:
        import json

        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "message": result.message,
                    "ocr_text": result.ocr_text,
                    "matched": list(result.matched),
                    "missing": list(result.missing),
                    "extract_s": result.extract_s,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(f"ok={result.ok} extract_s={result.extract_s}")
        print(f"ocr: {result.ocr_text!r}")
        print(f"matched: {result.matched}")
        if result.missing:
            print(f"missing: {result.missing}")
        print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
