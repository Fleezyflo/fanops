"""Trial-reels pipeline scoring — file count is not success.

Scores a run on desk validation, contiguous claim-lock, and per-clip cover OCR.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.desk import TARGET_VARIANTS
from lib.cover_qa import ocr_langs_for_language, qa_cover
from lib.desk_swarm import validate_desk_result


@dataclass
class ClipScore:
    clip_id: str
    desk_ok: bool
    desk_issues: list[str] = field(default_factory=list)
    cover_ok: bool | None = None
    cover_message: str = ""
    tess_langs: str = ""
    output_path: str = ""

    @property
    def shippable(self) -> bool:
        if not self.desk_ok:
            return False
        if self.cover_ok is False:
            return False
        return True


@dataclass
class PipelineScore:
    clips_scored: int
    desk_pass: int
    cover_pass: int
    cover_checked: int
    shippable: int
    stacks_landed: int
    file_count: int
    distinct_verified_texts: int
    target_variants: int
    success: bool
    message: str
    clip_scores: list[ClipScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clips_scored": self.clips_scored,
            "desk_pass": self.desk_pass,
            "cover_pass": self.cover_pass,
            "cover_checked": self.cover_checked,
            "shippable": self.shippable,
            "stacks_landed": self.stacks_landed,
            "file_count": self.file_count,
            "distinct_verified_texts": self.distinct_verified_texts,
            "target_variants": self.target_variants,
            "success": self.success,
            "message": self.message,
            "clips": [
                {
                    "clip_id": c.clip_id,
                    "desk_ok": c.desk_ok,
                    "desk_issues": c.desk_issues,
                    "cover_ok": c.cover_ok,
                    "cover_message": c.cover_message,
                    "tess_langs": c.tess_langs,
                    "output_path": c.output_path,
                    "shippable": c.shippable,
                }
                for c in self.clip_scores
            ],
        }


def score_clip(
    *,
    clip_id: str,
    desk_result: dict[str, Any],
    output_path: str | Path | None = None,
    attested_words: tuple[str, ...] | list[str] | None = None,
    run_cover_qa: bool = True,
    cover_ok: bool | None = None,
    cover_message: str = "",
) -> ClipScore:
    """Score one clip on desk + optional cover OCR."""
    validation = validate_desk_result(desk_result)
    language = desk_result.get("language") or "en"
    tess_langs = ocr_langs_for_language(language)

    score = ClipScore(
        clip_id=clip_id,
        desk_ok=validation["ok"],
        desk_issues=list(validation.get("issues") or []),
        tess_langs=tess_langs,
        output_path=str(output_path) if output_path else "",
    )

    if cover_ok is not None:
        score.cover_ok = cover_ok
        score.cover_message = cover_message
        return score

    if not run_cover_qa or not output_path:
        score.cover_ok = None
        return score

    path = Path(output_path)
    if not path.exists():
        score.cover_ok = False
        score.cover_message = f"output missing: {path}"
        return score

    words = attested_words
    if words is None and desk_result.get("mode") == "write":
        words = tuple(card["text"] for card in desk_result.get("cards") or [])

    cover = qa_cover(path, words, tess_langs=tess_langs, language=language)
    score.cover_ok = cover.ok
    score.cover_message = cover.message
    return score


def _distinct_hook_texts(
    clip_payloads: list[dict[str, Any]],
    clip_scores: list[ClipScore],
    *,
    verified_on_cover_only: bool,
) -> set[str]:
    """Collect distinct on-screen hook texts from shippable (or cover-verified) variants."""
    texts: set[str] = set()
    for payload, clip_score in zip(clip_payloads, clip_scores, strict=False):
        words = payload.get("attested_words")
        if not words:
            continue
        hook_text = str(words[0]).strip()
        if not hook_text:
            continue
        if verified_on_cover_only:
            if clip_score.cover_ok is not True:
                continue
        elif not clip_score.shippable:
            continue
        texts.add(hook_text)
    return texts


def score_run(
    *,
    clip_payloads: list[dict[str, Any]],
    stacks_landed: int = 0,
    file_count: int | None = None,
    require_cover: bool = True,
    cover_checked: int | None = None,
    cover_pass: int | None = None,
) -> PipelineScore:
    """Score a full trial-reels run. Success requires shippable clips, not file count."""
    clip_scores: list[ClipScore] = []
    for payload in clip_payloads:
        clip_scores.append(
            score_clip(
                clip_id=str(payload.get("clip_id") or payload.get("id") or "unknown"),
                desk_result=payload.get("desk") or payload,
                output_path=payload.get("output_path"),
                attested_words=payload.get("attested_words"),
                run_cover_qa=require_cover and bool(payload.get("output_path")),
                cover_ok=payload.get("cover_ok"),
                cover_message=str(payload.get("cover_message") or ""),
            )
        )

    desk_pass = sum(1 for c in clip_scores if c.desk_ok)
    cover_checked_count = cover_checked
    if cover_checked_count is None:
        cover_checked_count = sum(1 for c in clip_scores if c.cover_ok is not None)
    cover_pass_count = cover_pass
    if cover_pass_count is None:
        cover_pass_count = sum(1 for c in clip_scores if c.cover_ok is True)
    shippable = sum(1 for c in clip_scores if c.shippable)
    files = file_count if file_count is not None else stacks_landed

    verified_on_cover = require_cover and cover_checked_count > 0
    distinct_verified = len(
        _distinct_hook_texts(
            clip_payloads,
            clip_scores,
            verified_on_cover_only=verified_on_cover,
        )
    )

    if clip_scores:
        success = shippable > 0 and distinct_verified >= TARGET_VARIANTS
        if require_cover and cover_checked_count:
            success = success and cover_pass_count == cover_checked_count
    else:
        success = False

    if success and distinct_verified >= TARGET_VARIANTS:
        qualifier = "verified on covers" if verified_on_cover else "attested on shipped cuts"
        message = (
            f"{shippable}/{len(clip_scores)} clips shippable; "
            f"{distinct_verified}/{TARGET_VARIANTS} distinct hooks {qualifier}"
        )
    elif success:
        if verified_on_cover:
            detail = (
                f"{distinct_verified} distinct hook text{'s' if distinct_verified != 1 else ''} "
                f"verified on covers (honest subset — transcript cannot fill {TARGET_VARIANTS})"
            )
        else:
            detail = (
                f"{distinct_verified} distinct attested hook text{'s' if distinct_verified != 1 else ''} "
                f"across {shippable} cuts (honest subset — transcript cannot fill {TARGET_VARIANTS})"
            )
        message = f"{shippable}/{len(clip_scores)} clips shippable; {detail}"
    elif stacks_landed and not shippable:
        message = (
            f"stacks landed ({stacks_landed} files) but only {shippable}/{len(clip_scores)} "
            f"clips shippable — file count is not success"
        )
    else:
        message = (
            f"desk {desk_pass}/{len(clip_scores)}, "
            f"cover {cover_pass_count}/{cover_checked_count}, shippable {shippable}"
        )

    return PipelineScore(
        clips_scored=len(clip_scores),
        desk_pass=desk_pass,
        cover_pass=cover_pass_count,
        cover_checked=cover_checked_count,
        shippable=shippable,
        stacks_landed=stacks_landed,
        file_count=files,
        distinct_verified_texts=distinct_verified,
        target_variants=TARGET_VARIANTS,
        success=success,
        message=message,
        clip_scores=clip_scores,
    )


def write_score_report(score: PipelineScore, path: str | Path) -> Path:
    """Write JSON score report."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(score.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p
