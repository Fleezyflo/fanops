"""Trial-reels runner — one clip in, 15–20 vertical hook cuts out.

Pipeline: ingest → transcript → desk (contiguous attested hooks) → ASS burn-in →
ffmpeg stacks. No Pillow, no PNG overlays, no model downloads unless explicitly allowed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.captions import write_ass, write_ass_file
from lib.desk import HOOKS, TARGET_VARIANTS, expand_variant_slots, write
from lib.desk_swarm import validate_desk_result
from lib.hooks import (
    LyricEvent,
    cut_spec,
    hook_window,
    stamp_lyric_events,
)
from lib.ingest import collect_sources
from lib.media import MediaInfo, probe_media, vertical_filter_chain
from lib.cover_qa import cover_extract_s_for_hook, qa_cover
from lib.pipeline import PipelineScore, score_run, write_score_report
from lib.stacks import (
    ffmpeg_cmd,
    rehooks_for_stack,
    resolve_ffmpeg_bin,
    stack_gate_passes,
)

REHOOK_DURATION_S = 1.8
DEFAULT_OUT_WIDTH = 1080
DEFAULT_OUT_HEIGHT = 1920
MIN_TARGET_OUTPUTS = TARGET_VARIANTS
MAX_TARGET_OUTPUTS = TARGET_VARIANTS


@dataclass
class VariantResult:
    hook: str
    stack: str
    output_path: Path
    cite_start_s: float
    cut_length_s: float
    hook_text: str
    ok: bool
    message: str = ""
    cover_ok: bool | None = None
    cover_message: str = ""
    cover_extract_s: float = 0.0


@dataclass
class ClipRunResult:
    clip_id: str
    source_path: Path
    desk: dict[str, Any]
    validation: dict[str, Any]
    variants: list[VariantResult] = field(default_factory=list)
    score: PipelineScore | None = None
    blocked: bool = False
    message: str = ""


def _clip_id(path: Path) -> str:
    stem = path.stem
    if stem.startswith("clip_"):
        return stem
    return f"clip_{stem}"


def _transcript_candidates(source: Path, work_dir: Path) -> list[Path]:
    return [
        work_dir / "transcript.json",
        source.with_suffix(".transcript.json"),
        source.parent / f"{source.stem}.transcript.json",
    ]


def _load_transcript(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _whisper_models_cached() -> bool:
    cache = Path.home() / ".cache" / "whisper"
    if not cache.is_dir():
        return False
    return any(cache.glob("*.pt"))


def load_or_transcribe(
    source: Path,
    work_dir: Path,
    *,
    transcript_path: Path | None = None,
    allow_asr: bool = False,
) -> dict[str, Any]:
    """Load transcript JSON or transcribe when ASR is explicitly allowed and models exist."""
    candidates: list[Path] = []
    if transcript_path is not None:
        candidates.append(transcript_path)
    candidates.extend(_transcript_candidates(source, work_dir))

    for candidate in candidates:
        if candidate.is_file():
            return _load_transcript(candidate)

    if not allow_asr:
        raise FileNotFoundError(
            "no transcript JSON found; pass --transcript or set TRIAL_REELS_ALLOW_ASR=1 "
            "(requires local whisper weights — no downloads)"
        )

    if not _whisper_models_cached():
        raise RuntimeError(
            "TRIAL_REELS_ALLOW_ASR is set but no local whisper weights found; "
            "will not download models — provide --transcript"
        )

    from lib.ears import transcribe

    wav = work_dir / f"{source.stem}.wav"
    work_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )
    merged = transcribe(wav, work_dir)
    out = work_dir / "transcript.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def _segments_to_lyrics(transcript: dict[str, Any]) -> list[LyricEvent]:
    rows = transcript.get("segments") or transcript.get("lines") or []
    events: list[LyricEvent] = []
    for row in rows:
        text = str(row.get("text") or row.get("content") or "").strip()
        if not text:
            continue
        start = float(row.get("start") or row.get("ts") or 0.0)
        end = float(row.get("end") or start + 2.0)
        events.append(LyricEvent(start_s=start, end_s=end, text=text))
    return events


def _rel_time(absolute_s: float, cite_start_s: float, cut_length_s: float) -> float:
    return max(0.0, min(cut_length_s, absolute_s - cite_start_s))


def build_ass_events(
    *,
    hook_text: str,
    hook_in_s: float,
    hook_out_s: float,
    cite_start_s: float,
    cut_length_s: float,
    lyric_events: list[LyricEvent],
    stack: str,
    rehooks_s: tuple[float, ...] | list[float],
) -> list[dict[str, float | str]]:
    """ASS dialogue events relative to the cut timeline (0 = cite_start)."""
    events: list[dict[str, float | str]] = []
    hook_start = _rel_time(hook_in_s, cite_start_s, cut_length_s)
    hook_end = max(hook_start + 0.4, _rel_time(hook_out_s, cite_start_s, cut_length_s))
    if hook_text.strip():
        events.append({"start": hook_start, "end": hook_end, "text": hook_text.strip()})

    for lyric in lyric_events:
        start = _rel_time(lyric.start_s, cite_start_s, cut_length_s)
        end = _rel_time(lyric.end_s, cite_start_s, cut_length_s)
        if end <= start:
            end = min(cut_length_s, start + 2.0)
        if lyric.text.strip() and end > start:
            events.append({"start": start, "end": end, "text": lyric.text.strip()})

    for rehook_s in rehooks_for_stack(stack, rehooks_s):
        if rehook_s >= cut_length_s:
            continue
        end = min(cut_length_s, rehook_s + REHOOK_DURATION_S)
        if hook_text.strip():
            events.append({"start": rehook_s, "end": end, "text": hook_text.strip()})

    return events


def render_variant(
    *,
    source: Path,
    output: Path,
    cite_start_s: float,
    cut_length_s: float,
    stack: str,
    ass_path: Path,
    media: MediaInfo,
    out_width: int = DEFAULT_OUT_WIDTH,
    out_height: int = DEFAULT_OUT_HEIGHT,
) -> None:
    """Encode one hook×stack vertical cut with burned ASS."""
    vprep = "vprep"
    prep = vertical_filter_chain("0:v", width=out_width, height=out_height, out_label=vprep)
    cmd = ffmpeg_cmd(
        stack,
        input_path=str(source),
        output_path=str(output),
        duration_s=cut_length_s,
        width=out_width,
        height=out_height,
        has_audio=media.has_audio,
        sub_path=str(ass_path),
        cite_start_s=cite_start_s,
        video_in=vprep,
        audio_in="0:a",
        vertical_prep=prep,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not output.is_file():
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed for {output.name}: {detail}")


def run_clip(
    source: Path,
    *,
    out_dir: Path,
    work_dir: Path,
    recipes: dict[str, Any],
    transcript_path: Path | None = None,
    allow_asr: bool = False,
    run_cover_qa: bool = True,
) -> ClipRunResult:
    """Run the full trial-reels pipeline for one source clip."""
    source = source.resolve()
    clip_id = _clip_id(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    media = probe_media(source)
    transcript = load_or_transcribe(
        source,
        work_dir,
        transcript_path=transcript_path,
        allow_asr=allow_asr,
    )
    desk_result = write(transcript)
    validation = validate_desk_result(desk_result)

    result = ClipRunResult(
        clip_id=clip_id,
        source_path=source,
        desk=desk_result,
        validation=validation,
        blocked=desk_result.get("mode") != "write",
        message=str(desk_result.get("reason") or ""),
    )

    if result.blocked:
        return result

    lyric_events = _segments_to_lyrics(transcript)
    cards = list(desk_result.get("cards") or [])
    rehooks_s = tuple(recipes.get("rehooks_s") or (3, 8))
    variant_slots = expand_variant_slots(cards)
    clip_language = str(desk_result.get("language") or transcript.get("language") or "en")

    for index, slot in enumerate(variant_slots):
        hook = str(slot.get("hook") or HOOKS[index % len(HOOKS)])
        stack = str(slot.get("stack") or "punch_cuts")
        cite_start_s = float((slot.get("cite") or {}).get("start") or 0.0)
        hook_text = str(slot.get("text") or "").strip()
        cut_start, cut_length = cut_spec(cite_start_s, media.duration_s)
        window = hook_window(hook, cite_start_s=cite_start_s, total_duration_s=media.duration_s)
        variant_tag = f"v{index + 1:02d}"

        if not stack_gate_passes(stack, cut_length):
            result.variants.append(
                VariantResult(
                    hook=hook,
                    stack=stack,
                    output_path=out_dir / f"{clip_id}_{variant_tag}_{stack}.mp4",
                    cite_start_s=cut_start,
                    cut_length_s=cut_length,
                    hook_text=hook_text,
                    ok=False,
                    message=f"stack gated for {cut_length:.2f}s",
                )
            )
            continue

        drop_last = stack == "open_loop"
        stamped = stamp_lyric_events(
            hook,
            lyric_events,
            cite_start_s=cite_start_s,
            total_duration_s=media.duration_s,
            drop_last=drop_last,
        )
        ass_events = build_ass_events(
            hook_text=hook_text,
            hook_in_s=window.hook_in_s,
            hook_out_s=window.hook_out_s,
            cite_start_s=cut_start,
            cut_length_s=cut_length,
            lyric_events=stamped,
            stack=stack,
            rehooks_s=rehooks_s,
        )
        ass_path = work_dir / f"{clip_id}_{variant_tag}_{stack}.ass"
        ass_text = write_ass(ass_events)
        if not ass_text.strip():
            result.variants.append(
                VariantResult(
                    hook=hook,
                    stack=stack,
                    output_path=out_dir / f"{clip_id}_{variant_tag}_{stack}.mp4",
                    cite_start_s=cut_start,
                    cut_length_s=cut_length,
                    hook_text=hook_text,
                    ok=False,
                    message="empty ASS",
                )
            )
            continue
        write_ass_file(ass_text, ass_path)

        out_path = out_dir / f"{clip_id}_{variant_tag}_{stack}.mp4"
        try:
            render_variant(
                source=source,
                output=out_path,
                cite_start_s=cut_start,
                cut_length_s=cut_length,
                stack=stack,
                ass_path=ass_path,
                media=media,
            )
            cover_ok: bool | None = None
            cover_message = ""
            cover_extract_s = cover_extract_s_for_hook(
                hook,
                cite_start_s=cite_start_s,
                total_duration_s=media.duration_s,
            )
            variant_ok = True
            if run_cover_qa and hook_text.strip():
                cover = qa_cover(
                    out_path,
                    attested_words=(hook_text,),
                    extract_s=cover_extract_s,
                    workdir=work_dir / "cover_qa" / f"{clip_id}_{variant_tag}_{stack}",
                    keep_artifacts=True,
                    language=clip_language,
                )
                cover_ok = cover.ok
                cover_message = cover.message
                if not cover.ok:
                    variant_ok = False
            result.variants.append(
                VariantResult(
                    hook=hook,
                    stack=stack,
                    output_path=out_path,
                    cite_start_s=cut_start,
                    cut_length_s=cut_length,
                    hook_text=hook_text,
                    ok=variant_ok,
                    cover_ok=cover_ok,
                    cover_message=cover_message,
                    cover_extract_s=cover_extract_s,
                )
            )
        except RuntimeError as exc:
            result.variants.append(
                VariantResult(
                    hook=hook,
                    stack=stack,
                    output_path=out_path,
                    cite_start_s=cut_start,
                    cut_length_s=cut_length,
                    hook_text=hook_text,
                    ok=False,
                    message=str(exc),
                )
            )

    checked_variants = [v for v in result.variants if v.cover_ok is not None]
    ok_variants = [v for v in result.variants if v.ok]
    clip_payloads = [
        {
            "clip_id": clip_id,
            "desk": desk_result,
            "output_path": str(v.output_path),
            "attested_words": (v.hook_text,),
            "cover_ok": v.cover_ok,
            "cover_message": v.cover_message,
        }
        for v in checked_variants
    ]
    result.score = score_run(
        clip_payloads=clip_payloads,
        stacks_landed=len(ok_variants),
        file_count=len(ok_variants),
        require_cover=run_cover_qa and bool(checked_variants),
        cover_checked=len(checked_variants),
        cover_pass=sum(1 for v in checked_variants if v.cover_ok),
    )
    return result


def run_batch(
    sources: list[Path],
    *,
    out_dir: Path,
    work_root: Path,
    recipes: dict[str, Any],
    transcript_paths: dict[Path, Path] | None = None,
    allow_asr: bool = False,
    run_cover_qa: bool = True,
) -> list[ClipRunResult]:
    results: list[ClipRunResult] = []
    transcript_paths = transcript_paths or {}
    for source in sources:
        work_dir = work_root / _clip_id(source)
        results.append(
            run_clip(
                source,
                out_dir=out_dir,
                work_dir=work_dir,
                recipes=recipes,
                transcript_path=transcript_paths.get(source.resolve()),
                allow_asr=allow_asr,
                run_cover_qa=run_cover_qa,
            )
        )
    return results


def _load_recipes(path: Path | None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "recipes.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Trial-reels runner: one clip → 15–20 vertical hook cuts (ffmpeg + ASS)."
    )
    parser.add_argument("--file", help="Single source media file")
    parser.add_argument("--folder", help="Folder of media files")
    parser.add_argument("--from-fanops", dest="from_fanops", help="FanOps clip id under 03_clips")
    parser.add_argument("--in-dir", dest="in_dir", default="in", help="Default ingest folder")
    parser.add_argument("--out-dir", default="out", help="Output directory for rendered cuts")
    parser.add_argument("--work-dir", default="work", help="Working directory for transcripts/ASS")
    parser.add_argument("--transcript", help="Transcript JSON (skips ASR)")
    parser.add_argument("--recipes", help="Path to recipes.json")
    parser.add_argument("--no-cover-qa", action="store_true", help="Skip cover OCR in scoring")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args(argv)

    sources = collect_sources(args)
    if not sources:
        parser.error("no media sources found")

    allow_asr = bool(os.environ.get("TRIAL_REELS_ALLOW_ASR", "").strip())
    recipes = _load_recipes(Path(args.recipes) if args.recipes else None)
    out_dir = Path(args.out_dir)
    work_root = Path(args.work_dir)

    transcript_paths: dict[Path, Path] = {}
    if args.transcript:
        transcript_paths[sources[0].resolve()] = Path(args.transcript)

    results = run_batch(
        sources,
        out_dir=out_dir,
        work_root=work_root,
        recipes=recipes,
        transcript_paths=transcript_paths,
        allow_asr=allow_asr,
        run_cover_qa=not args.no_cover_qa,
    )

    total_ok = sum(1 for r in results for v in r.variants if v.ok)
    summary = {
        "clips": len(results),
        "outputs": total_ok,
        "target_min": MIN_TARGET_OUTPUTS,
        "target_max": MAX_TARGET_OUTPUTS,
        "ffmpeg": resolve_ffmpeg_bin(),
        "runs": [
            {
                "clip_id": r.clip_id,
                "blocked": r.blocked,
                "desk_mode": r.desk.get("mode"),
                "validation_ok": r.validation.get("ok"),
                "variants_ok": sum(1 for v in r.variants if v.ok),
                "variants_total": len(r.variants),
                "score": r.score.to_dict() if r.score else None,
            }
            for r in results
        ],
    }

    report_path = work_root / "run_report.json"
    write_score_report(
        results[0].score if results and results[0].score else score_run(clip_payloads=[]),
        report_path,
    )
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for run in summary["runs"]:
            print(
                f"{run['clip_id']}: desk={run['desk_mode']} "
                f"variants={run['variants_ok']}/{run['variants_total']}"
            )
        print(f"total outputs: {total_ok}")

    if total_ok < MIN_TARGET_OUTPUTS and not any(r.blocked for r in results):
        return 1
    if any(r.blocked for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
