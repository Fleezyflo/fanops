#!/usr/bin/env python3
"""Decide whether the real-tooling E2E lane must do its WORK, from the TRIGGER alone.

THIS IS NOT A RELEVANCE GATE, and it deliberately stopped being one on 2026-07-22. The previous
version asked *"could this change plausibly affect runtime?"* and ran the ~7-minute lane whenever the
answer was yes — which, for any change touching `src/`, `tests/`, `scripts/`, `tools/` or `.github/`,
was always. The practical result was that the full E2E still gated ordinary pushes and every PR
iteration, which is exactly what it was supposed to stop doing. A gate that answers "run" for all
real work is not a gate.

So the question is now the only one that separates the fast path from the slow one honestly: **did
someone explicitly ask for the full lane?** A normal push or pull request never does.

    run   ->  workflow_dispatch (manual) | schedule (the nightly proof lane) | an explicit
              force-e2e request on the pull request
    skip  ->  everything else, which is every ordinary push and pull_request event

THE POLARITY IS INVERTED FROM THE OLD PREDICATE, AND THAT IS THE DECISION, NOT AN OVERSIGHT. The old
one failed toward more testing because an unclassified path might have mattered. This one cannot
"fail toward more testing" without reinstating the behaviour that was removed: an unrecognised event
is by construction not one an operator asked the heavy lane to run on. What that costs is stated in
`.github/ci-control-registry.yml` under `CI-E2E.trigger_gate.disclosed_consequence` and in ADR-0101,
rather than being hidden behind a reassuring default.

The context still REPORTS on every push and PR — it is live in branch protection, and a required
workflow skipped by a `paths:` filter never reports and leaves protection pending forever. Only the
work is conditional.

`decide()` is a pure function so the unit lane can prove both directions without a runner.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

# The events that ARE a request for the full lane. `schedule` is the nightly proof run; a scheduled
# failure blocks nothing and is the signal that the real-tooling path has rotted. `push` and
# `pull_request` are deliberately ABSENT — that absence is the whole change.
ON_DEMAND_EVENTS = ("workflow_dispatch", "schedule")


@dataclass(frozen=True)
class Decision:
    """`run` is what the job acts on; `reason` is what a human reads in the log."""
    run: bool
    reason: str


def decide(*, event: str = "pull_request", forced: bool = False) -> Decision:
    """Ordered rules, first match wins. Only an explicit request runs the lane."""
    if event in ON_DEMAND_EVENTS:
        return Decision(True, f"the `{event}` trigger IS the request for the full lane")
    if forced:
        return Decision(True, "explicitly requested on this pull request (force-e2e)")
    return Decision(False, f"`{event}` did not request the full lane — the real-tooling suite is "
                           f"on-demand only since 2026-07-22")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="decide whether the E2E lane must do its work")
    ap.add_argument("--event", default="pull_request", help="github.event_name")
    ap.add_argument("--forced", action="store_true", help="an explicit force-e2e request fired")
    args = ap.parse_args(argv)

    d = decide(event=args.event, forced=args.forced)
    if d.run:
        print(f"RUNNING THE FULL REAL-TOOLING LANE. Reason: {d.reason}.")
    else:
        print(f"THE FULL REAL-TOOLING SUITE DID NOT RUN. Reason: {d.reason}. This context still "
              f"reported, in seconds, so branch protection resolves and PR iteration is not blocked. "
              f"To run the full lane: dispatch the CI workflow manually (`gh workflow run ci.yml "
              f"--ref <branch>`), add the `force-e2e` label, or put [force-e2e] in the PR title. It "
              f"also runs nightly on `main`.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if d.run else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
