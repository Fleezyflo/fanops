"""Trial-reels runner — one clip in, 15–20 vertical hook cuts out.

Pipeline: ingest → transcribe (optional) → desk → ASS → ffmpeg stacks → score.
On-screen text is always a contiguous attested transcript span (desk card).
No Pillow, no PNG overlays, no model downloads.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.captions import DEFAULT_FONT, write_ass, write_ass_file
from lib.cover_qa import (
    cover_extract_s_for_hook,
    extract_cover_frame,
    ocr_langs_for_language,
    qa_cover,
)
from lib.desk import TARGET_VARIANTS, contract_met, write as desk_write
from lib.desk_swarm import validate_desk_result
from lib.hooks import HOOK_POLICIES, LyricEvent, cut_spec, hook_window
from lib.ingest import collect_sources
from lib.pipeline import score_run, write_score_report
from lib.stacks import STACK_NAMES, ffmpeg_cmd, resolve_ffmpeg_bin, stack_gate_passes

RECIPES_PATH = Path(__file__).resolve().parents[1] / "recipes.json"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920


@dataclass
class VariantPlan:
    hook: str
    stack: str
    card: dict[str, Any]
    cite_start_s: float
    cut_length_s: float
    output_name: str


@dataclass
class RunResult:
    clip_id: str
    desk: dict[str, Any]
    validation: dict[str, Any]
    variants_planned: int
    variants_rendered: int
    outputs: list[Path] = field(default_factory=list)
    clip_payloads: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    score: dict[str, Any] | None = None
    success: bool = False
    message: str = ""


def load_recipes(path: Path | None = None) -> dict[str, Any]:
    p = path or RECIPES_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {(proc.stderr or proc.stdout).strip()}")
    return float(proc.stdout.strip())


def _has_audio(path: Path) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode == 0 and "audio" in (proc.stdout or "")


def transcript_from_segments(
    segments: list[dict[str, Any]],
    *,
    language: str | None = None,
) -> dict[str, Any]:
    lines = []
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        lines.append({"start": float(seg.get("start") or 0.0), "text": text})
    payload: dict[str, Any] = {"lines": lines}
    if language:
        payload["language"] = language
    return payload


def transcript_from_ears(merged: dict[str, Any]) -> dict[str, Any]:
    return transcript_from_segments(
        list(merged.get("segments") or []),
        language=str(merged.get("language") or ""),
    )


def _lyric_events(transcript: dict[str, Any]) -> list[LyricEvent]:
    events: list[LyricEvent] = []
    raw_lines = transcript.get("lines") or transcript.get("segments") or []
    for raw in raw_lines:
        text = ""
        for key in ("text", "content", "line"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        if not text:
            continue
        start = float(raw.get("start") or raw.get("ts") or 0.0)
        end = float(raw.get("end") or start + 2.0)
        events.append(LyricEvent(start, end, text))
    return events


def _write_cover_jpg(
    video: Path,
    dest: Path,
    *,
    policy: str,
    cite_start_s: float,
    total_duration_s: float,
) -> Path:
    """Extract the hook-visible cover still for a rendered variant."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    extract_s = cover_extract_s_for_hook(
        policy,
        cite_start_s=cite_start_s,
        total_duration_s=total_duration_s,
    )
    extract_cover_frame(video, dest, at=extract_s)
    return dest


def _enrich_desk(
    desk: dict[str, Any],
    *,
    plans: list[VariantPlan],
    outputs: list[Path],
    cover_paths: list[Path | None],
    cover_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach render manifest + cover verification to desk.json payload."""
    if desk.get("mode") != "write":
        return desk

    variants: list[dict[str, Any]] = []
    verified_texts: set[str] = set()
    for plan, out, cover, cover_meta in zip(
        plans, outputs, cover_paths, cover_results, strict=False
    ):
        entry = {
            "hook": plan.hook,
            "stack": plan.stack,
            "text": plan.card.get("text") or "",
            "cite": plan.card.get("cite") or {},
            "mp4": str(out) if out else "",
            "cover_jpg": str(cover) if cover else "",
            "cover_ok": cover_meta.get("cover_ok"),
            "cover_message": cover_meta.get("cover_message", ""),
        }
        variants.append(entry)
        if cover_meta.get("cover_ok") and entry["text"]:
            verified_texts.add(entry["text"])

    enriched = dict(desk)
    enriched["variants"] = variants
    enriched["verified_distinct_texts"] = len(verified_texts)
    enriched["contract_met"] = contract_met(desk)
    enriched["verified_contract_met"] = len(verified_texts) >= TARGET_VARIANTS
    return enriched


def build_ass_events(
    card: dict[str, Any],
    *,
    policy: str,
    cite_start_s: float,
    cut_length_s: float,
) -> list[dict[str, float | str]]:
    """Burn only the desk hook card — one contiguous attested span, top safe zone."""
    window = hook_window(
        policy,
        cite_start_s=cite_start_s,
        total_duration_s=cite_start_s + cut_length_s,
    )
    hook_in = max(0.0, window.hook_in_s - window.cut_start_s)
    hook_out = max(hook_in + 0.4, window.hook_out_s - window.cut_start_s)
    hook_out = min(hook_out, cut_length_s)
    return [{"start": hook_in, "end": hook_out, "text": card["text"]}]


def plan_variants(
    desk: dict[str, Any],
    *,
    clip_id: str,
    recipes: dict[str, Any] | None = None,
    source_duration_s: float,
) -> list[VariantPlan]:
    """Enumerate hook×stack variants — one treatment text per slot when ceiling allows."""
    if desk.get("mode") != "write":
        return []

    recipes = recipes or load_recipes()
    hooks = list(recipes.get("hooks") or HOOK_POLICIES)
    stacks = list(recipes.get("stacks") or STACK_NAMES)
    cards = list(desk.get("cards") or [])
    if not cards:
        return []

    plans: list[VariantPlan] = []
    for card in cards:
        hook = card.get("hook") or ""
        stack = card.get("stack") or ""
        if hook not in hooks or stack not in stacks:
            continue
        cite = card.get("cite") or {}
        cite_start = float(cite.get("start") or 0.0)
        _, cut_length = cut_spec(cite_start, source_duration_s)
        if not stack_gate_passes(stack, cut_length):
            continue
        plans.append(
            VariantPlan(
                hook=hook,
                stack=stack,
                card=card,
                cite_start_s=cite_start,
                cut_length_s=cut_length,
                output_name=f"{clip_id}_{hook}_{stack}.mp4",
            )
        )
    return plans


def trim_to_vertical(
    source: Path,
    dest: Path,
    *,
    start_s: float,
    duration_s: float,
    has_audio: bool,
) -> None:
    """Extract a cut window and normalize to 9:16 (1080×1920)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )
    cmd = [
        resolve_ffmpeg_bin(),
        "-y",
        "-ss",
        f"{start_s:.6f}",
        "-i",
        str(source),
        "-t",
        f"{duration_s:.6f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if has_audio:
        cmd.extend(["-c:a", "aac"])
    else:
        cmd.append("-an")
    cmd.append(str(dest))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError(f"trim failed: {(proc.stderr or proc.stdout).strip()}")


def render_variant(
    source: Path,
    plan: VariantPlan,
    *,
    workdir: Path,
    has_audio: bool,
    dry_run: bool = False,
) -> Path | None:
    """Render one hook×stack variant with ASS burn-in."""
    out = workdir / "out" / plan.output_name
    out.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return out

    trimmed = workdir / "trim" / f"{plan.hook}_{plan.stack}_trim.mp4"
    trim_to_vertical(
        source,
        trimmed,
        start_s=plan.cite_start_s,
        duration_s=plan.cut_length_s,
        has_audio=has_audio,
    )

    events = build_ass_events(
        plan.card,
        policy=plan.hook,
        cite_start_s=plan.cite_start_s,
        cut_length_s=plan.cut_length_s,
    )
    ass_text = write_ass(events, DEFAULT_FONT, width=TARGET_WIDTH, height=TARGET_HEIGHT)
    if not ass_text:
        return None

    ass_path = workdir / "ass" / f"{plan.hook}_{plan.stack}.ass"
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    write_ass_file(ass_text, ass_path)

    # Stack graph operates on the trimmed clip (timeline starts at 0).
    trimmed_duration = _ffprobe_duration(trimmed)
    cmd = ffmpeg_cmd(
        plan.stack,
        input_path=str(trimmed),
        output_path=str(out),
        duration_s=trimmed_duration,
        width=TARGET_WIDTH,
        height=TARGET_HEIGHT,
        has_audio=has_audio,
        sub_path=str(ass_path),
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(
            f"stack {plan.stack!r} failed for {plan.hook}: {(proc.stderr or proc.stdout).strip()}"
        )
    return out


def run_clip(
    source: Path,
    *,
    workdir: Path,
    clip_id: str | None = None,
    transcript: dict[str, Any] | None = None,
    recipes: dict[str, Any] | None = None,
    dry_run: bool = False,
    require_cover: bool = False,
) -> RunResult:
    """Run the full trial-reels pipeline on one source clip."""
    source = source.resolve()
    clip_id = clip_id or source.stem
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    duration_s = _ffprobe_duration(source)
    audio = _has_audio(source)

    if transcript is None:
        from lib.ears import transcribe

        wav = workdir / "audio.wav"
        extract_cmd = [
            resolve_ffmpeg_bin(),
            "-y",
            "-i",
            str(source),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav),
        ]
        if audio:
            proc = subprocess.run(extract_cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise RuntimeError(f"audio extract failed: {(proc.stderr or proc.stdout).strip()}")
            merged = transcribe(wav, workdir / "whisper")
            transcript = transcript_from_ears(merged)
        else:
            transcript = {"language": "en", "lines": []}

    desk = desk_write(transcript)
    validation = validate_desk_result(desk)
    plans = plan_variants(desk, clip_id=clip_id, recipes=recipes, source_duration_s=duration_s)

    outputs: list[Path] = []
    skipped: list[str] = []
    clip_payloads: list[dict[str, Any]] = []

    if desk.get("mode") != "write":
        return RunResult(
            clip_id=clip_id,
            desk=desk,
            validation=validation,
            variants_planned=len(plans),
            variants_rendered=0,
            outputs=[],
            skipped=[f"desk blocked: {desk.get('reason')}"],
            success=False,
            message=f"desk blocked: {desk.get('reason')}",
        )

    rendered_plans: list[VariantPlan] = []
    cover_paths: list[Path | None] = []
    cover_results: list[dict[str, Any]] = []

    for plan in plans:
        try:
            out = render_variant(source, plan, workdir=workdir, has_audio=audio, dry_run=dry_run)
            if out is None:
                skipped.append(f"{plan.hook}/{plan.stack}: empty ass")
                continue
            outputs.append(out)
            rendered_plans.append(plan)

            cover_ok: bool | None = None
            cover_message = ""
            cover_path: Path | None = None
            hook_text = str(plan.card.get("text") or "")
            if not dry_run and hook_text.strip():
                cover_path = workdir / "covers" / f"{plan.hook}_{plan.stack}.jpg"
                try:
                    _write_cover_jpg(
                        out,
                        cover_path,
                        policy=plan.hook,
                        cite_start_s=plan.cite_start_s,
                        total_duration_s=duration_s,
                    )
                    extract_s = cover_extract_s_for_hook(
                        plan.hook,
                        cite_start_s=plan.cite_start_s,
                        total_duration_s=duration_s,
                    )
                    cover = qa_cover(
                        out,
                        attested_words=(hook_text,),
                        extract_s=extract_s,
                        workdir=workdir / "cover_qa" / f"{plan.hook}_{plan.stack}",
                        language=str(desk.get("language") or ""),
                        tess_langs=ocr_langs_for_language(desk.get("language")),
                    )
                    cover_ok = cover.ok
                    cover_message = cover.message
                except RuntimeError as exc:
                    cover_ok = False
                    cover_message = str(exc)

            cover_paths.append(cover_path)
            cover_results.append({"cover_ok": cover_ok, "cover_message": cover_message})

            clip_payloads.append(
                {
                    "clip_id": f"{clip_id}_{plan.hook}_{plan.stack}",
                    "desk": desk,
                    "output_path": str(out),
                    "cover_jpg": str(cover_path) if cover_path else "",
                    "attested_words": (hook_text,),
                    "cover_ok": cover_ok,
                    "cover_message": cover_message,
                }
            )
        except RuntimeError as exc:
            skipped.append(f"{plan.hook}/{plan.stack}: {exc}")

    desk = _enrich_desk(
        desk,
        plans=rendered_plans,
        outputs=outputs,
        cover_paths=cover_paths,
        cover_results=cover_results,
    )

    checked = [p for p in clip_payloads if p.get("cover_ok") is not None]
    score = score_run(
        clip_payloads=clip_payloads,
        stacks_landed=len(outputs),
        file_count=len(outputs),
        require_cover=bool(checked) and require_cover,
    )
    report_path = workdir / "score.json"
    write_score_report(score, report_path)

    return RunResult(
        clip_id=clip_id,
        desk=desk,
        validation=validation,
        variants_planned=len(plans),
        variants_rendered=len(outputs),
        outputs=outputs,
        clip_payloads=clip_payloads,
        skipped=skipped,
        score=score.to_dict(),
        success=score.success and validation["ok"],
        message=score.message,
    )


def run_batch(
    sources: list[Path],
    *,
    out_root: Path,
    transcript_map: dict[str, dict[str, Any]] | None = None,
    dry_run: bool = False,
    require_cover: bool = False,
) -> list[RunResult]:
    results: list[RunResult] = []
    for src in sources:
        transcript = (transcript_map or {}).get(str(src.resolve()))
        workdir = out_root / src.stem
        results.append(
            run_clip(
                src,
                workdir=workdir,
                transcript=transcript,
                dry_run=dry_run,
                require_cover=require_cover,
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Trial-reels runner: one clip → 15–20 vertical hook cuts (ffmpeg + ASS)."
    )
    parser.add_argument("--file", help="Single source media file")
    parser.add_argument("--folder", help="Folder of source media")
    parser.add_argument("--from-fanops", dest="from_fanops", help="FanOps clip id under 03_clips")
    parser.add_argument("--in-dir", dest="in_dir", default="in", help="Default ingest folder")
    parser.add_argument("--out", default="out", help="Output root directory")
    parser.add_argument("--transcript", help="Pre-baked transcript JSON (skip whisper)")
    parser.add_argument("--dry-run", action="store_true", help="Plan variants without ffmpeg render")
    parser.add_argument("--require-cover", action="store_true", help="Fail unless cover OCR passes")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary on stdout")
    args = parser.parse_args(argv)

    sources = collect_sources(args)
    if not sources:
        print("no sources found", file=sys.stderr)
        return 1

    transcript_map: dict[str, dict[str, Any]] | None = None
    if args.transcript:
        payload = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
        transcript_map = {str(sources[0].resolve()): payload}

    out_root = Path(args.out)
    results = run_batch(
        sources,
        out_root=out_root,
        transcript_map=transcript_map,
        dry_run=args.dry_run,
        require_cover=args.require_cover,
    )

    summary = {
        "clips": len(results),
        "success": all(r.success for r in results),
        "total_outputs": sum(r.variants_rendered for r in results),
        "results": [
            {
                "clip_id": r.clip_id,
                "success": r.success,
                "message": r.message,
                "desk_mode": r.desk.get("mode"),
                "variants_planned": r.variants_planned,
                "variants_rendered": r.variants_rendered,
                "outputs": [str(p) for p in r.outputs],
                "skipped": r.skipped,
            }
            for r in results
        ],
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(
                f"{r.clip_id}: {r.variants_rendered}/{r.variants_planned} rendered — "
                f"{'ok' if r.success else r.message}"
            )

    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
