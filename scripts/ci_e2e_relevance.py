#!/usr/bin/env python3
"""Decide whether the real-tooling E2E lane must do its WORK for a given change.

`real-tooling E2E (must run, not skip)` is a LIVE REQUIRED context in branch protection, so the job
must always REPORT — a required workflow that is skipped by a path filter leaves branch protection
pending forever, which is why the filter lives HERE (inside the job) and never on the workflow.

The polarity is deliberate and fail-safe: E2E is skipped ONLY when every changed path is provably
inert (documentation and governance records). Anything else — a new top-level directory, a path this
module has never seen, an unreadable diff — RUNS the full lane. A relevance predicate that fails
toward MORE testing cannot silently lose coverage; one that fails toward less can.

`decide()` is a pure function so the unit lane can prove both directions without a runner.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

# Paths that cannot change runtime, media, rendering, publishing or toolchain behaviour. Keep this
# list SHORT and provably inert; every addition widens the fast lane. `.github/**` is deliberately
# ABSENT — a workflow or CI-registry edit must run the lane it changes.
INERT_PREFIXES = ("docs/", ".claude/", ".reports/")

# Root-level files that are prose only. A root `*.md` is inert; a root `*.py` or `pyproject.toml`
# is not, so this is an extension check on the ROOT level only, never a recursive one.
INERT_ROOT_SUFFIXES = (".md",)


@dataclass(frozen=True)
class Decision:
    """`run` is what the job acts on; `reason` is what a human reads in the log."""
    run: bool
    reason: str


def is_inert(path: str) -> bool:
    """True only for paths proven unable to affect runtime behaviour."""
    p = path.strip()
    if not p:
        return False
    if p.startswith(INERT_PREFIXES):
        return True
    if "/" not in p and p.endswith(INERT_ROOT_SUFFIXES):
        return True
    return False


def decide(paths, *, event: str = "pull_request", forced: bool = False) -> Decision:
    """Ordered rules, first match wins. Every branch except the last one RUNS the lane."""
    if forced:
        return Decision(True, "forced — explicit force-e2e trigger")
    if event != "pull_request":
        return Decision(True, f"event is {event!r}, not a pull request — full verification")
    paths = [p.strip() for p in paths if p and p.strip()]
    if not paths:
        return Decision(True, "no changed paths could be resolved — running rather than assuming")
    live = sorted({p for p in paths if not is_inert(p)})
    if live:
        shown = ", ".join(live[:5]) + (f" (+{len(live) - 5} more)" if len(live) > 5 else "")
        return Decision(True, f"{len(live)} runtime-relevant path(s) changed: {shown}")
    return Decision(False, f"all {len(paths)} changed path(s) are documentation or governance records")


def _read_paths(path_file: str) -> list[str]:
    """An unreadable diff yields NO paths, which `decide` treats as a reason to run the full lane."""
    try:
        with open(path_file, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError as exc:
        print(f"could not read {path_file}: {exc} — running the full lane")
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="decide whether the E2E lane must do its work")
    ap.add_argument("--event", default="pull_request", help="github.event_name")
    ap.add_argument("--changed-from", help="file holding one changed path per line")
    ap.add_argument("--forced", action="store_true", help="an explicit force-e2e trigger fired")
    args = ap.parse_args(argv)

    paths = _read_paths(args.changed_from) if args.changed_from else []
    d = decide(paths, event=args.event, forced=args.forced)
    if d.run:
        print(f"E2E IS RELEVANT — running the full real-tooling lane. Reason: {d.reason}.")
    else:
        print(f"E2E WAS NOT RELEVANT to this change and did not run. Reason: {d.reason}. "
              f"This context still reported so branch protection resolves; to run it anyway, add "
              f"the `force-e2e` label or put [force-e2e] in the pull-request title.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if d.run else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
