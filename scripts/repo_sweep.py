#!/usr/bin/env python3
"""repo_sweep.py — READ-ONLY full-repository "mess" report for the delegation-only orchestrator.

Scope is not limited to the listed Linear tasks: the orchestrator must drive the WHOLE repo to pristine.
This script enumerates what's outstanding — open PRs (with conflict/behind state), stale branches, and
leftover build artifacts / conflict-marker files — so the orchestrator can spawn sub-agents to resolve each.
It changes NOTHING (no fetch, no delete); it only reports. `--json` for machine consumption.

The `gh`/`git` calls are thin wrappers; the classification logic is pure and unit-tested
(`tests/test_repo_sweep.py`).
"""
import argparse, json, subprocess, sys, time
from pathlib import Path

_ARTIFACT_SUFFIXES = (".orig", ".rej", ".bak", ".swp", ".tmp", "~", ".uploadpart")
_ARTIFACT_NAMES = (".DS_Store",)
_CONFLICT_MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")


def classify_pr(mergeable: str, merge_state: str) -> str:
    """Collapse GitHub's mergeable + mergeStateStatus into an action label."""
    ms = (merge_state or "").upper()
    if (mergeable or "").upper() == "CONFLICTING" or ms == "DIRTY": return "conflict"
    if ms == "BEHIND": return "behind"
    if ms == "BLOCKED": return "blocked"
    if ms == "UNSTABLE": return "unstable"
    if ms == "CLEAN" or (mergeable or "").upper() == "MERGEABLE": return "clean"
    return "unknown"


def is_artifact(path: str) -> bool:
    """True for leftover build/merge junk that should not linger in a pristine repo."""
    name = path.rsplit("/", 1)[-1]
    if name in _ARTIFACT_NAMES: return True
    if name.startswith(".env.") and name.endswith(".bak"): return True
    return any(name.endswith(sfx) for sfx in _ARTIFACT_SUFFIXES)


def has_conflict_markers(text: str) -> bool:
    """True if a file's text carries git conflict markers (an unresolved merge left behind)."""
    lines = (text or "").splitlines()
    return any(any(ln.startswith(m) for m in _CONFLICT_MARKERS) for ln in lines)


def stale_branches(refs, now_epoch: float, days: int = 30) -> list:
    """refs: list of (name, last_commit_epoch). Return names older than `days`, excluding main/HEAD."""
    cutoff = now_epoch - days * 86400
    out = []
    for name, ts in refs:
        base = name.rsplit("/", 1)[-1]
        if base in ("main", "HEAD", "master"): continue
        if ts < cutoff: out.append(name)
    return out


def artifact_paths(paths) -> list:
    """The subset of `paths` that look like leftover build/merge junk (pure; sorted, de-duped)."""
    return sorted({p for p in paths if is_artifact(p)})


# ---- thin I/O wrappers ------------------------------------------------------

def _gh_open_prs(repo):
    out = subprocess.run(["gh", "pr", "list", "--repo", repo, "--state", "open",
                          "--json", "number,title,headRefName,mergeable,mergeStateStatus,isDraft", "--limit", "100"],
                         capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else []


def _remote_branch_ages(repo_root):
    out = subprocess.run(["git", "for-each-ref", "--format=%(refname:short) %(committerdate:unix)",
                          "refs/remotes/origin"], capture_output=True, text=True, cwd=repo_root, timeout=30)
    refs = []
    for ln in out.stdout.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[1].isdigit(): refs.append((parts[0], int(parts[1])))
    return refs


def _git_lines(repo_root, *args):
    out = subprocess.run(["git", *args], capture_output=True, text=True, cwd=repo_root, timeout=30)
    return out.stdout.splitlines() if out.returncode == 0 else []


def _all_artifacts(repo_root):
    """Leftover junk among BOTH tracked files and untracked-not-ignored files."""
    tracked = _git_lines(repo_root, "ls-files")
    untracked = _git_lines(repo_root, "ls-files", "--others", "--exclude-standard")
    return artifact_paths(tracked + untracked)


def _unmerged(repo_root):
    """Paths with unresolved merge conflicts in the working tree (`git ls-files -u`)."""
    return sorted({ln.split("\t", 1)[-1] for ln in _git_lines(repo_root, "ls-files", "-u") if "\t" in ln})


def sweep(repo, repo_root, days=30):
    prs = _gh_open_prs(repo)
    pr_report = [{"number": p["number"], "title": p["title"], "branch": p["headRefName"],
                  "state": classify_pr(p.get("mergeable"), p.get("mergeStateStatus")),
                  "draft": p.get("isDraft")} for p in prs]
    refs = _remote_branch_ages(repo_root)
    return {
        "open_prs": pr_report,
        "conflicts": [p for p in pr_report if p["state"] == "conflict"],
        "behind": [p for p in pr_report if p["state"] == "behind"],
        "stale_branches": stale_branches(refs, time.time(), days),
        "artifacts": _all_artifacts(repo_root),
        "unresolved_conflicts": _unmerged(repo_root),
    }


def _pristine(rep) -> bool:
    return not (rep["conflicts"] or rep["stale_branches"] or rep["artifacts"] or rep["unresolved_conflicts"])


def _landable_open_prs(open_prs) -> list:
    """Open PRs the orchestrator must drive to land. Drafts are WIP — reported but not blocking."""
    return [p for p in (open_prs or []) if not p.get("draft")]


def is_done(rep) -> bool:
    """The Definition-of-Done proxy the orchestrator cannot self-override: EVERY task landed (no
    ready-for-review open PRs left to drive) AND the repo pristine (no conflicts / stale branches /
    unresolved merges / artifacts). Draft PRs are informational only."""
    return _pristine(rep) and not _landable_open_prs(rep["open_prs"])


def outstanding(rep) -> list:
    """Human-readable reasons the repo is not DONE (empty list == done)."""
    out = []
    landable = _landable_open_prs(rep["open_prs"])
    if landable: out.append(f"{len(landable)} open PR(s) not yet landed")
    if rep["conflicts"]: out.append(f"{len(rep['conflicts'])} conflicting PR(s)")
    if rep["unresolved_conflicts"]: out.append(f"{len(rep['unresolved_conflicts'])} unresolved merge conflict(s)")
    if rep["stale_branches"]: out.append(f"{len(rep['stale_branches'])} stale branch(es)")
    if rep["artifacts"]: out.append(f"{len(rep['artifacts'])} leftover artifact(s)")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="read-only full-repo mess report")
    ap.add_argument("--repo", default="Fleezyflo/fanops")
    ap.add_argument("--root", default=".")
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--require-pristine", action="store_true",
                    help="DONE-gate: exit 0 only if every task is landed AND the repo is pristine; "
                         "else exit 3. Fail-safe: if the sweep can't run, exit 3 (never falsely 'done').")
    args = ap.parse_args(argv)
    try:
        rep = sweep(args.repo, Path(args.root), args.stale_days)
    except Exception as exc:
        print(f"[repo-sweep] could not complete sweep ({type(exc).__name__}: {exc})", file=sys.stderr)
        # a DONE-gate must never report success when it could not even measure the repo
        return 3 if args.require_pristine else 0
    if args.json:
        print(json.dumps(rep, indent=2))
        return _require_pristine_exit(rep) if args.require_pristine else 0
    print(f"[repo-sweep] {args.repo}")
    landable = _landable_open_prs(rep["open_prs"])
    drafts = [p for p in rep["open_prs"] if p.get("draft")]
    print(f"  open PRs: {len(landable)} landable, {len(drafts)} draft"
          f"  (conflict: {len(rep['conflicts'])}, behind: {len(rep['behind'])})")
    for p in rep["open_prs"]:
        tag = ' draft (not blocking done)' if p.get("draft") else ''
        print(f"    #{p['number']} [{p['state']}]{tag}  {p['branch']}  — {p['title']}")
    print(f"  stale branches (>{args.stale_days}d): {len(rep['stale_branches'])}")
    for b in rep["stale_branches"]: print(f"    {b}")
    print(f"  unresolved merge conflicts: {len(rep['unresolved_conflicts'])}")
    for c in rep["unresolved_conflicts"]: print(f"    {c}")
    print(f"  leftover artifacts: {len(rep['artifacts'])}")
    for a in rep["artifacts"]: print(f"    {a}")
    print(f"  => repo pristine: {'YES' if _pristine(rep) else 'NO — drive the above to resolution via sub-agents'}")
    return _require_pristine_exit(rep) if args.require_pristine else 0


def _require_pristine_exit(rep) -> int:
    """DONE-gate verdict: 0 when done, 3 otherwise (with the outstanding reasons)."""
    if is_done(rep):
        print("[repo-sweep] DONE — every task landed and the repo is pristine. Completion is permitted.")
        return 0
    print("[repo-sweep] NOT DONE — completion is NOT permitted; drive these to resolution via sub-agents: "
          + "; ".join(outstanding(rep)), file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
