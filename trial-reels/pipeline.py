"""Trial-reels inbox pipeline — live door.

Drain ``in/``, write ``desk.json`` per clip, render hook×stack variants, score cover OCR.
Pass bar: TARGET_VARIANTS distinct attested on-screen texts proven in desk.json and
cover JPGs when the transcript honestly supports them; fail closed otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lib.desk import TARGET_VARIANTS, contract_met
from lib.desk_swarm import validate_desk_result
from lib.ingest import collect_sources
from lib.pipeline import score_run, write_score_report
from lib.runner import run_batch

EXIT_BLOCKED = 1
EXIT_OK = 0


def _desk_hook_texts(desk: dict[str, Any]) -> list[str]:
    if desk.get("mode") != "write":
        return []
    cards = desk.get("cards") or []
    return [str(card.get("text") or "").strip() for card in cards if str(card.get("text") or "").strip()]


def run_inbox(
    *,
    in_dir: Path,
    out_root: Path,
    transcript_map: dict[str, dict[str, Any]] | None = None,
    dry_run: bool = False,
    require_cover: bool = False,
) -> dict[str, Any]:
    """Process every clip in *in_dir*; success requires the 20-hook contract when writing."""
    sources = collect_sources(argparse.Namespace(in_dir=str(in_dir)))
    out_root.mkdir(parents=True, exist_ok=True)

    results = run_batch(
        sources,
        out_root=out_root,
        transcript_map=transcript_map,
        dry_run=dry_run,
        require_cover=require_cover,
    )

    all_hook_texts: list[str] = []
    for result in results:
        all_hook_texts.extend(_desk_hook_texts(result.desk))

    unique_hooks = len(set(all_hook_texts))
    shipped = sum(r.variants_rendered for r in results)
    contract_ok = all(
        result.desk.get("mode") != "write" or contract_met(result.desk) for result in results
    )

    return {
        "clips": len(results),
        "shipped": shipped,
        "unique_hook_texts": unique_hooks,
        "target_variants": TARGET_VARIANTS,
        "contract_ok": contract_ok,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trial-reels inbox pipeline (desk → encode → score).")
    parser.add_argument("--in-dir", default="in", help="Inbox folder of source media")
    parser.add_argument("--out", default="out", help="Output root directory")
    parser.add_argument("--transcript", help="Pre-baked transcript JSON for the sole inbox clip")
    parser.add_argument("--dry-run", action="store_true", help="Plan variants without ffmpeg render")
    parser.add_argument("--require-cover", action="store_true", help="Fail unless cover OCR passes")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary on stdout")
    args = parser.parse_args(argv)

    in_dir = Path(args.in_dir)
    out_root = Path(args.out)

    transcript_map: dict[str, dict[str, Any]] | None = None
    if args.transcript:
        payload = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
        sources = collect_sources(argparse.Namespace(in_dir=str(in_dir)))
        if sources:
            transcript_map = {str(sources[0].resolve()): payload}

    summary = run_inbox(
        in_dir=in_dir,
        out_root=out_root,
        transcript_map=transcript_map,
        dry_run=args.dry_run,
        require_cover=args.require_cover,
    )

    clip_payloads: list[dict[str, Any]] = []
    for result in summary["results"]:
        clip_payloads.extend(result.clip_payloads)

    score = score_run(
        clip_payloads=clip_payloads,
        stacks_landed=summary["shipped"],
        file_count=summary["shipped"],
        require_cover=args.require_cover,
    )
    write_score_report(score, out_root / "score.json")

    payload = {
        "clips": summary["clips"],
        "shipped": summary["shipped"],
        "unique_hook_texts": summary["unique_hook_texts"],
        "distinct_verified_texts": score.distinct_verified_texts,
        "target_variants": TARGET_VARIANTS,
        "success": score.success and summary["contract_ok"],
        "message": score.message,
        "results": [
            {
                "clip_id": r.clip_id,
                "desk_mode": r.desk.get("mode"),
                "unique_texts": r.desk.get("unique_texts"),
                "variants_rendered": r.variants_rendered,
                "success": r.success,
                "message": r.message,
            }
            for r in summary["results"]
        ],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for result in summary["results"]:
            desk = result.desk
            validation = validate_desk_result(desk)
            filled = _desk_hook_texts(desk)
            print(
                f"{result.clip_id}: desk={desk.get('mode')} "
                f"cards={len(filled)} unique={len(set(filled))} "
                f"rendered={result.variants_rendered}/{result.variants_planned} "
                f"{'ok' if validation['ok'] else validation.get('issues')}"
            )
        print(payload["message"])

    if payload["success"]:
        return EXIT_OK
    return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
