#!/usr/bin/env python3
"""Blocking CI SLO gate — compare pytest wall-clock to CI_UNIT_PYTEST_BUDGET_S. Stdlib-only."""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from typing import IO, Union

from ci_timing_report import parse_pytest_summary

LogSource = Union[Path, IO[str]]


def parse_wall_seconds(source: LogSource) -> float:
    """Return pytest wall seconds from a log file path or text stream."""
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8", errors="replace")
    else:
        text = source.read()
    return parse_pytest_summary(text)[1]


def check_budget(seconds: float, budget_s: float) -> str | None:
    """Return an error message when over budget, else None."""
    if seconds <= budget_s: return None
    over = seconds - budget_s
    return (f"unit pytest SLO exceeded: {seconds:.2f}s > {budget_s:.0f}s budget "
            f"(+{over:.2f}s over)")


def run_gate(log: LogSource, *, budget_s: float) -> int:
    seconds = parse_wall_seconds(log)
    msg = check_budget(seconds, budget_s)
    if msg:
        print(msg, file=sys.stderr)
        return 1
    print(f"unit pytest SLO ok: {seconds:.2f}s <= {budget_s:.0f}s budget")
    return 0


def _resolve_budget(args: argparse.Namespace) -> float:
    raw = args.budget or os.environ.get("CI_UNIT_PYTEST_BUDGET_S", "")
    if not raw:
        print("CI_UNIT_PYTEST_BUDGET_S is required (or pass --budget)", file=sys.stderr)
        sys.exit(2)
    try: return float(raw)
    except ValueError:
        print(f"invalid CI_UNIT_PYTEST_BUDGET_S: {raw!r}", file=sys.stderr)
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Blocking unit pytest SLO gate")
    p.add_argument("--log", type=Path, help="pytest log file (default: stdin)")
    p.add_argument("--budget", help="override CI_UNIT_PYTEST_BUDGET_S")
    args = p.parse_args(argv)
    budget_s = _resolve_budget(args)
    source: LogSource = args.log if args.log else sys.stdin
    return run_gate(source, budget_s=budget_s)


if __name__ == "__main__":
    sys.exit(main())
