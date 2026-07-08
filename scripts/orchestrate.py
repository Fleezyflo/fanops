#!/usr/bin/env python3
"""orchestrate.py — the ONE command for running a delegation-only orchestration wave.

You don't need to know about hooks, verification records, or ledgers to use the environment. Three verbs:

  python scripts/orchestrate.py start    # engage the environment (turns enforcement ON for this run)
  python scripts/orchestrate.py status   # what's still outstanding across the whole repo
  python scripts/orchestrate.py done     # the finish line: exit 0 only when everything is landed & pristine

`start` self-activates (creates .orchestration/state/ACTIVE) so no env-var chore; enforcement then can't be
turned off from inside the run. Human quickstart: ORCHESTRATION.md. Full contract: .orchestration/SPEC.md.
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_sweep  # noqa: E402  (same-dir helper; reuses the sweep + DONE-gate)


def engage(root: Path) -> Path:
    """Turn the orchestration environment ON by creating the ACTIVE marker. Idempotent. Returns its path."""
    marker = Path(root) / ".orchestration" / "state" / "ACTIVE"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("engaged\n")
    return marker


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="run a delegation-only orchestration wave")
    ap.add_argument("command", choices=["start", "status", "done"])
    ap.add_argument("--repo", default="Fleezyflo/fanops")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.command == "start":
        engage(root)
        print("orchestration ENGAGED — enforcement is ON for this run:")
        print("  • nothing lands without a sub-agent verification record")
        print("  • every sub-agent's work is logged (attribution ledger)")
        print("  • destructive git + tampering with the gate/state are blocked")
        print("Now hand your Linear tasks to the fanops-orchestrator agent; it drives them to done.\n")
        print("Current repo state:")
        return repo_sweep.main(["--repo", args.repo, "--root", str(root)])

    if args.command == "status":
        return repo_sweep.main(["--repo", args.repo, "--root", str(root)])

    # done: the completion gate (exit 0 only when landed + pristine, else 3)
    return repo_sweep.main(["--repo", args.repo, "--root", str(root), "--require-pristine"])


if __name__ == "__main__":
    raise SystemExit(main())
