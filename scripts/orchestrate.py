#!/usr/bin/env python3
"""orchestrate.py — the ONE command for running a delegation-only orchestration wave.

You don't need to know about hooks, verification records, or ledgers to use the environment. Four verbs:

  python scripts/orchestrate.py start    # engage the environment (turns enforcement ON for this run)
  python scripts/orchestrate.py status   # what's still outstanding across the whole repo
  python scripts/orchestrate.py done     # the finish line: exit 0 only when everything is landed & pristine
  python scripts/orchestrate.py stop     # operator-only: disengage a stale/crashed wave (from YOUR terminal)

`start` self-activates (creates .orchestration/state/ACTIVE) so no env-var chore; enforcement then can't be
turned off from inside the run (the gate blocks shell writes to the marker — `stop` only works from a
human terminal outside Cursor). `done` disengages automatically when it exits 0, so enforcement never
lingers after a finished wave. Human quickstart: ORCHESTRATION.md. Full contract: .orchestration/SPEC.md.
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_sweep  # noqa: E402  (same-dir helper; reuses the sweep + DONE-gate)


def _marker(root: Path) -> Path:
    return Path(root) / ".orchestration" / "state" / "ACTIVE"


def engage(root: Path) -> Path:
    """Turn the orchestration environment ON by creating the ACTIVE marker. Idempotent. Returns its path."""
    marker = _marker(root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("engaged\n")
    return marker


def disengage(root: Path) -> bool:
    """Remove the ACTIVE marker (enforcement OFF). Returns True if it was on."""
    marker = _marker(root)
    if marker.exists():
        marker.unlink()
        return True
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="run a delegation-only orchestration wave")
    ap.add_argument("command", choices=["start", "status", "done", "stop"])
    ap.add_argument("--repo", default="Fleezyflo/fanops")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.command == "start":
        engage(root)
        print("orchestration ENGAGED — the hook gate is DORMANT; these are CONVENTIONS, not enforcement:")
        print("  • nothing lands without a sub-agent verification record for the PR's current head")
        print("  • only named wave agents spawn (fanops-worker / fanops-lander); models are pinned")
        print("  • every sub-agent's work is logged (attribution ledger)")
        print("  • destructive git + tampering with the gate/state are off-limits")
        print("What actually blocks: GitHub required checks, .githooks pre-commit/pre-push,")
        print("and (Claude Code only) the permissions.deny list. See .orchestration/SPEC.md.")
        print("Now hand your Linear tasks to the fanops-orchestrator agent; it drives them to done.\n")
        print("Current repo state:")
        return repo_sweep.main(["--repo", args.repo, "--root", str(root)])

    if args.command == "status":
        return repo_sweep.main(["--repo", args.repo, "--root", str(root)])

    if args.command == "stop":
        was_on = disengage(root)
        print("orchestration DISENGAGED — enforcement is OFF." if was_on
              else "orchestration was not engaged — nothing to stop.")
        return 0

    # done: the completion gate (exit 0 only when landed + pristine, else 3);
    # on success the wave is over, so disengage — enforcement must never outlive its wave.
    rc = repo_sweep.main(["--repo", args.repo, "--root", str(root), "--require-pristine"])
    if rc == 0 and disengage(root):
        print("wave complete — orchestration DISENGAGED (enforcement OFF).")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
