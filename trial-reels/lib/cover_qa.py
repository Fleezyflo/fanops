"""Cover-frame OCR QA for trial Reels hooks.

Extracts a cover still (default t=0.4s — not frame 0, which is often black/fade),
crops the top hook band, preprocesses for tesseract, and matches attested card words.
ffmpeg + tesseract only (no Pillow).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from lib.hooks import hook_window

# Frame 0 is often black or pre-roll; 0.4s lands on the burned hook for clip_5a92132dc6de-style covers.
COVER_EXTRACT_S = 0.4
HOOK_BAND_FRACTION = 0.28
NOTO_NASKH_BOLD = "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"

# Attested on-screen card phrases from clip_5a92132dc6de purple-neon covers.
DEFAULT_ATTESTED_WORDS: tuple[str, ...] = (
    "عزبتني",
    "كفاية",
    "لك كفاية عزبتني",
)

_FFMPEG_TIMEOUT = 60.0
_TESS_TIMEOUT = 30.0


def cover_extract_s_for_hook(
    policy: str,
    *,
    cite_start_s: float,
    total_duration_s: float,
    fallback: float = COVER_EXTRACT_S,
) -> float:
    """Seconds into a rendered cut to sample the visible ASS hook stamp."""
    try:
        window = hook_window(policy, cite_start_s=cite_start_s, total_duration_s=total_duration_s)
    except ValueError:
        return fallback
    rel_in = max(0.0, window.hook_in_s - window.cut_start_s)
    rel_out = max(rel_in + 0.12, window.hook_out_s - window.cut_start_s)
    sample = rel_in + (rel_out - rel_in) * 0.35
    sample = max(0.15, min(rel_out - 0.08, sample))
    return round(sample, 3)


def ocr_langs_for_language(language: str | None) -> str:
    """Pick tesseract language packs from clip language — never default English to ara."""
    lang = (language or "").strip().lower()
    if lang.startswith("ar"):
        return "ara"
    if lang.startswith("en"):
        return "eng"
    return "ara+eng"


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
    fraction: float = HOOK_BAND_FRACTION,
) -> Path:
    """Crop the top `fraction` of the frame (hook safe zone)."""
    src = Path(image_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # crop=width:height:x:y — top band only.
    vf = f"crop=iw:ih*{fraction}:0:0"
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
) -> Path:
    """Contrast preprocessing for hook-band OCR (ffmpeg filters only).

    Arabic on purple neon: grayscale + contrast lift (invert hurts ara reads).
    English: same chain plus mild upscale for thin burned ASS strokes.
    """
    src = Path(image_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lang = (language or "").lower()
    if lang.startswith("en"):
        vf = "scale=iw*2:ih*2:flags=lanczos,format=gray,eq=contrast=2.2:brightness=0.04"
    else:
        vf = "format=gray,eq=contrast=2.0:brightness=0.02"
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
    # Single-row DP capped at max_dist.
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
    langs = tess_langs or ocr_langs_for_language(language)
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
        preprocess_for_ocr(band, pre, language=language)
        text = ocr_image(pre, langs=langs)
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


def generate_purple_fixture(
    output_path: str | Path,
    *,
    text_lines: tuple[str, ...] = ("كفاية", "لك كفاية عزبتني"),
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Synthesize a purple-neon cover still for tests (ffmpeg drawtext, no Pillow)."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    font = NOTO_NASKH_BOLD
    if not Path(font).exists():
        font = "DejaVu Sans"
    filters: list[str] = []
    y = 120
    for i, line in enumerate(text_lines):
        size = 60 if i == 0 else 48
        escaped = line.replace(":", r"\:").replace("'", r"\'")
        filters.append(
            f"drawtext=fontfile={font}:text='{escaped}':fontcolor=white:fontsize={size}:"
            f"x=(w-text_w)/2:y={y}"
        )
        y += 90
    vf = ",".join(filters)
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
        vf,
        "-frames:v",
        "1",
        str(out),
    ]
    r = _run(cmd)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"purple fixture generation failed: {(r.stderr or r.stdout or '').strip()}")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Cover-frame OCR QA for trial Reels hooks (crop top band, ara+eng tesseract)."
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
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    args = parser.parse_args(argv)

    result = qa_cover(
        args.input,
        tuple(args.words),
        extract_s=args.at,
        workdir=args.workdir,
        min_hits=args.min_hits,
        keep_artifacts=bool(args.workdir),
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
