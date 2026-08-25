"""Trial-reels runner — one clip in, 15–20 vertical hook×stack cuts out.

Orchestrates ingest → transcript → constrained desk → hook windows → ASS burn-in
via ffmpeg stacks. Refuses to ship when desk validation fails (no permutation fakes,
no whisper-slice cards, no file-count theatre).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from lib.captions import DEFAULT_FONT, write_ass, write_ass_file
from lib.desk import write as desk_write
from lib.desk_swarm import validate_desk_result
from lib.ears import merge_ears
from lib.hooks import hook_window
from lib.ingest import collect_sources
from lib.pipeline import score_run, write_score_report
from lib.stacks import (
    STACK_NAMES,
    ffmpeg_cmd,
    rehooks_for_stack,
    resolve_ffmpeg_bin,
    stack_gate_passes,
)

RECIPES_PATH = Path(__file__).resolve().parents[1] / "recipes.json"
REHOOK_DURATION_S = 1.8
DEFAULT_OUT_DIR = Path("out")


@dataclass(frozen=True)
class MediaProbe:
    duration_s: float
    width: int
    height: int
    has_audio: bool


@dataclass(frozen=True)
class VariantPlan:
    hook: str
    stack: str
    card: dict[str, Any]
    cut_start_s: float
    cut_length_s: float
    hook_in_s: float
    hook_out_s: float
    output_name: str


@dataclass
class RunResult:
    clip_id: str
    source: Path
    out_dir: Path
    desk: dict[str, Any]
    validation: dict[str, Any]
    variants_planned: int
    variants_rendered: int
    outputs: list[Path]
    blocked: bool
    message: str


def load_recipes(path: Path | None = None) -> dict[str, Any]:
    p = path or RECIPES_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def load_transcript(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return normalize_transcript(payload)


def normalize_transcript(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept desk lines, merged segments, or dual-ear fixture shapes."""
    if "primary" in payload and "secondary" in payload:
        payload = merge_ears(payload["primary"], payload["secondary"])
    if "segments" in payload and "lines" not in payload:
        lines = []
        for seg in payload.get("segments") or []:
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            lines.append({"start": float(seg.get("start") or 0.0), "text": text})
        payload = {**payload, "lines": lines}
    return payload


def probe_media(path: Path) -> MediaProbe:
    """Return duration and stream geometry via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {(proc.stderr or proc.stdout).strip()}")

    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or [{}]
    stream = streams[0] if streams else {}
    width = int(stream.get("width") or 1080)
    height = int(stream.get("height") or 1920)
    duration = float((data.get("format") or {}).get("duration") or 0.0)

    audio_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    audio_proc = subprocess.run(audio_cmd, capture_output=True, text=True, check=False)
    has_audio = audio_proc.returncode == 0 and bool((audio_proc.stdout or "").strip())

    return MediaProbe(duration_s=duration, width=width, height=height, has_audio=has_audio)


def build_hook_ass_events(
    card: dict[str, Any],
    *,
    cut_start_s: float,
    cut_length_s: float,
    hook_in_s: float,
    hook_out_s: float,
    stack: str,
    rehooks_s: Sequence[float],
) -> list[dict[str, float | str]]:
    """ASS dialogue events — only the attested card text, never permuted slices."""
    text = str(card.get("text") or "").strip()
    if not text:
        return []

    def rel(ts: float) -> float:
        return max(0.0, ts - cut_start_s)

    events: list[dict[str, float | str]] = []
    main_start = rel(hook_in_s)
    main_end = min(cut_length_s, rel(hook_out_s))
    if main_end > main_start:
        events.append({"start": main_start, "end": main_end, "text": text})

    for rh in rehooks_for_stack(stack, rehooks_s):
        if rh >= cut_length_s:
            continue
        events.append(
            {
                "start": rh,
                "end": min(cut_length_s, rh + REHOOK_DURATION_S),
                "text": text,
            }
        )
    return events


def plan_variants(
    desk_result: dict[str, Any],
    *,
    total_duration_s: float,
    recipes: dict[str, Any],
    clip_stem: str,
) -> list[VariantPlan]:
    """Cross hook cards with stacks; skip stacks gated off for the cut length."""
    if desk_result.get("mode") != "write":
        return []

    rehooks_s = tuple(recipes.get("rehooks_s") or ())
    plans: list[VariantPlan] = []

    for card in desk_result.get("cards") or []:
        hook = str(card.get("hook") or "")
        cite = card.get("cite") or {}
        cite_start = float(cite.get("start") or 0.0)
        window = hook_window(hook, cite_start_s=cite_start, total_duration_s=total_duration_s)

        for stack in recipes.get("stacks") or STACK_NAMES:
            if stack not in STACK_NAMES:
                continue
            if not stack_gate_passes(stack, window.cut_length_s):
                continue
            name = f"{clip_stem}_{hook}_{stack}.mp4"
            plans.append(
                VariantPlan(
                    hook=hook,
                    stack=stack,
                    card=card,
                    cut_start_s=window.cut_start_s,
                    cut_length_s=window.cut_length_s,
                    hook_in_s=window.hook_in_s,
                    hook_out_s=window.hook_out_s,
                    output_name=name,
                )
            )
    return plans


def precut_clip(
    source: Path,
    dest: Path,
    *,
    start_s: float,
    duration_s: float,
    has_audio: bool,
) -> None:
    """Trim source to the hook cut window before stack processing."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg_bin()
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_s:.6f}",
        "-i",
        str(source),
        "-t",
        f"{duration_s:.6f}",
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
        raise RuntimeError(f"precut failed: {(proc.stderr or proc.stdout).strip()}")


def render_variant(
    precut: Path,
    output: Path,
    plan: VariantPlan,
    *,
    ass_path: Path,
    probe: MediaProbe,
    rehooks_s: Sequence[float],
) -> None:
    """Apply one edit stack with ASS hook burn-in."""
    output.parent.mkdir(parents=True, exist_ok=True)

    events = build_hook_ass_events(
        plan.card,
        cut_start_s=plan.cut_start_s,
        cut_length_s=plan.cut_length_s,
        hook_in_s=plan.hook_in_s,
        hook_out_s=plan.hook_out_s,
        stack=plan.stack,
        rehooks_s=rehooks_s,
    )
    ass_text = write_ass(events, DEFAULT_FONT, width=probe.width, height=probe.height)
    if not ass_text:
        raise RuntimeError(f"empty ASS for {plan.output_name}")
    write_ass_file(ass_text, ass_path)

    cmd = ffmpeg_cmd(
        plan.stack,
        input_path=str(precut),
        output_path=str(output),
        duration_s=plan.cut_length_s,
        width=probe.width,
        height=probe.height,
        has_audio=probe.has_audio,
        sub_path=str(ass_path),
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(
            f"stack {plan.stack!r} render failed for {plan.hook}: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )


def run_one_clip(
    source: Path,
    *,
    transcript: dict[str, Any],
    out_dir: Path,
    recipes: dict[str, Any] | None = None,
    clip_id: str | None = None,
    dry_run: bool = False,
) -> RunResult:
    """Full pipeline for a single source clip."""
    recipes = recipes or load_recipes()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = clip_id or source.stem
    probe = probe_media(source)

    desk = desk_write(transcript)
    validation = validate_desk_result(desk)
    plans = plan_variants(desk, total_duration_s=probe.duration_s, recipes=recipes, clip_stem=stem)

    if desk.get("mode") != "write" or not validation.get("ok"):
        reason = desk.get("reason") or "; ".join(validation.get("issues") or [])
        return RunResult(
            clip_id=stem,
            source=source,
            out_dir=out_dir,
            desk=desk,
            validation=validation,
            variants_planned=len(plans),
            variants_rendered=0,
            outputs=[],
            blocked=True,
            message=f"desk blocked: {reason}",
        )

    if dry_run:
        return RunResult(
            clip_id=stem,
            source=source,
            out_dir=out_dir,
            desk=desk,
            validation=validation,
            variants_planned=len(plans),
            variants_rendered=0,
            outputs=[],
            blocked=False,
            message=f"dry-run: {len(plans)} variants planned",
        )

    work = out_dir / ".work" / stem
    work.mkdir(parents=True, exist_ok=True)
    rehooks_s = tuple(recipes.get("rehooks_s") or ())
    outputs: list[Path] = []

    # Group plans by cut window — one precut per unique (start, length).
    cut_cache: dict[tuple[float, float], Path] = {}

    for plan in plans:
        cut_key = (round(plan.cut_start_s, 4), round(plan.cut_length_s, 4))
        if cut_key not in cut_cache:
            precut_path = work / f"precut_{cut_key[0]:.3f}_{cut_key[1]:.3f}.mp4"
            precut_clip(
                source,
                precut_path,
                start_s=plan.cut_start_s,
                duration_s=plan.cut_length_s,
                has_audio=probe.has_audio,
            )
            cut_cache[cut_key] = precut_path

        precut = cut_cache[cut_key]
        precut_probe = probe_media(precut)
        out_path = out_dir / plan.output_name
        ass_path = work / f"{plan.hook}_{plan.stack}.ass"

        render_variant(
            precut,
            out_path,
            plan,
            ass_path=ass_path,
            probe=precut_probe,
            rehooks_s=rehooks_s,
        )
        outputs.append(out_path)

    return RunResult(
        clip_id=stem,
        source=source,
        out_dir=out_dir,
        desk=desk,
        validation=validation,
        variants_planned=len(plans),
        variants_rendered=len(outputs),
        outputs=outputs,
        blocked=False,
        message=f"rendered {len(outputs)}/{len(plans)} variants",
    )


def run_batch(
    sources: list[Path],
    *,
    transcript_path: Path | None,
    out_dir: Path,
    recipes: dict[str, Any] | None = None,
    dry_run: bool = False,
    score_path: Path | None = None,
) -> list[RunResult]:
    """Run all collected sources (expected: one clip)."""
    transcript: dict[str, Any] | None = None
    if transcript_path is not None:
        transcript = load_transcript(transcript_path)

    results: list[RunResult] = []
    for src in sources:
        if transcript is None:
            raise RuntimeError(
                f"no transcript for {src.name} — pass --transcript or pre-render Whisper JSON"
            )
        result = run_one_clip(
            src,
            transcript=transcript,
            out_dir=out_dir,
            recipes=recipes,
            clip_id=src.stem,
            dry_run=dry_run,
        )
        results.append(result)

    if score_path is not None and results:
        payloads = []
        for result in results:
            for out in result.outputs:
                card_texts = tuple(
                    str(c.get("text") or "") for c in result.desk.get("cards") or []
                )
                payloads.append(
                    {
                        "clip_id": result.clip_id,
                        "desk": result.desk,
                        "output_path": str(out),
                        "attested_words": card_texts,
                    }
                )
        score = score_run(
            clip_payloads=payloads,
            stacks_landed=sum(r.variants_rendered for r in results),
            file_count=sum(r.variants_rendered for r in results),
            require_cover=False,
        )
        write_score_report(score, score_path)

    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trial reels runner — ship 15–20 hook×stack vertical cuts from one clip.",
    )
    parser.add_argument("--file", help="Single source media file")
    parser.add_argument("--folder", help="Directory of media files")
    parser.add_argument("--from-fanops", help="Resolve clip id under FANOPS_CLIPS_ROOT")
    parser.add_argument("--in-dir", default="in", help="Default ingest folder when no selectors")
    parser.add_argument(
        "--transcript",
        type=Path,
        help="Attested transcript JSON (lines/segments or dual-ear primary+secondary)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Output directory")
    parser.add_argument("--recipes", type=Path, help="Override recipes.json")
    parser.add_argument("--score", type=Path, help="Write pipeline score JSON")
    parser.add_argument("--dry-run", action="store_true", help="Plan variants without ffmpeg render")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    recipes = load_recipes(args.recipes) if args.recipes else None

    sources = collect_sources(args)
    if not sources:
        print("no media sources found", file=sys.stderr)
        return 2
    if len(sources) > 1:
        print(
            f"warning: runner expects one clip; processing first of {len(sources)}",
            file=sys.stderr,
        )
        sources = sources[:1]

    if args.transcript is None:
        print("--transcript is required (Whisper ears are opt-in via separate tooling)", file=sys.stderr)
        return 2

    results = run_batch(
        sources,
        transcript_path=args.transcript,
        out_dir=args.out,
        recipes=recipes,
        dry_run=args.dry_run,
        score_path=args.score,
    )

    ok = all(not r.blocked and r.variants_rendered > 0 for r in results)
    for result in results:
        print(
            f"{result.clip_id}: {result.message} "
            f"(planned={result.variants_planned}, rendered={result.variants_rendered})"
        )
    return 0 if ok or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
