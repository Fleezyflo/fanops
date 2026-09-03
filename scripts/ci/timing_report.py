#!/usr/bin/env python3
"""Parse pytest stdout, write GitHub Actions job summary + optional ci-timing JSON. Stdlib-only."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

_PASSED_RE = re.compile(
    r"(\d+) passed(?:, \d+ (?:skipped|failed|error|deselected|warnings?))* in ([\d.]+)s(?: \([\d:]+\))?"
)
_SKIPPED_RE = re.compile(r"(\d+) skipped in ([\d.]+)s")

_STEP_LABELS = {"unit": "unit pytest", "e2e_integration": "e2e integration", "e2e_slow": "e2e slow"}


def parse_pytest_summary(text: str) -> tuple[int, float]:
    """Return (passed_count, wall_seconds) from pytest stdout."""
    for line in reversed(text.splitlines()):
        s = line.strip()
        m = _PASSED_RE.search(s)
        if m: return int(m.group(1)), float(m.group(2))
        m = _SKIPPED_RE.search(s)
        if m: return 0, float(m.group(2))
    raise ValueError("no pytest summary line found in log")


def step_fields(step: str, passed: int, seconds: float, *, xdist: bool = False) -> dict:
    """Map a CI step name to ci-timing.json fields."""
    if step == "unit": return {"unit_pytest_s": seconds, "test_count": passed, "xdist": xdist}
    if step == "e2e_integration": return {"e2e_integration_s": seconds}
    if step == "e2e_slow": return {"e2e_slow_s": seconds}
    raise ValueError(f"unknown step: {step}")


def format_summary_markdown(step: str, sha: str, passed: int, seconds: float, *, xdist: bool = False) -> str:
    label = _STEP_LABELS.get(step, step)
    lines = [f"## CI timing — {label}", "", f"- **SHA:** `{sha[:12]}`", f"- **Tests passed:** {passed}",
             f"- **Pytest wall:** {seconds:.2f}s"]
    if step == "unit": lines.append(f"- **xdist:** {'yes' if xdist else 'no'}")
    return "\n".join(lines) + "\n"


def write_step_summary(markdown: str, *, summary_file: str | None = None) -> None:
    path = summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if not path: return
    with open(path, "a", encoding="utf-8") as f: f.write(markdown)


def merge_timing_parts(parts_dir: Path, *, sha: str) -> dict:
    merged: dict = {"sha": sha}
    for p in sorted(parts_dir.rglob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for k, v in data.items():
            if k != "sha": merged[k] = v
    return merged


def _report_step(args: argparse.Namespace) -> int:
    text = Path(args.log).read_text(encoding="utf-8", errors="replace")
    passed, seconds = parse_pytest_summary(text)
    sha = args.sha or os.environ.get("GITHUB_SHA", "")
    fields = step_fields(args.step, passed, seconds, xdist=args.xdist)
    if sha: fields = {"sha": sha, **fields}
    md = format_summary_markdown(args.step, sha, passed, seconds, xdist=args.xdist)
    write_step_summary(md, summary_file=args.summary_file)
    if args.json_out:
        out = Path(args.json_out)
        if args.merge_in and out.exists():
            existing = json.loads(out.read_text(encoding="utf-8"))
            existing.update(fields)
            fields = existing
        out.write_text(json.dumps(fields, indent=2) + "\n", encoding="utf-8")
    return 0


def _merge_parts(args: argparse.Namespace) -> int:
    merged = merge_timing_parts(Path(args.merge), sha=args.sha or os.environ.get("GITHUB_SHA", ""))
    Path(args.json_out).write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CI pytest timing report (advisory)")
    p.add_argument("--step", choices=["unit", "e2e_integration", "e2e_slow"])
    p.add_argument("--log")
    p.add_argument("--sha", default="")
    p.add_argument("--xdist", action="store_true")
    p.add_argument("--summary-file")
    p.add_argument("--json-out")
    p.add_argument("--merge-in", action="store_true", help="merge into existing --json-out")
    p.add_argument("--merge", help="directory of partial timing JSON files to merge")
    args = p.parse_args(argv)
    if args.merge:
        if not args.json_out: p.error("--merge requires --json-out")
        return _merge_parts(args)
    if not args.step or not args.log: p.error("--step and --log required unless --merge")
    return _report_step(args)


if __name__ == "__main__":
    sys.exit(main())
