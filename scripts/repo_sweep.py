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


def _tracked_artifacts(repo_root):
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=repo_root, timeout=30)
    return [p for p in out.stdout.splitlines() if is_artifact(p)]


def sweep(repo, repo_root, days=30):
    prs = _gh_open_prs(repo)
    pr_report = [{"number": p["number"], "title": p["title"], "branch": p["headRefName"],
                  "state": classify_pr(p.get("mergeable"), p.get("mergeStateStatus")),
                  "draft": p.get("isDraft")} for p in prs]
    refs = _remote_branch_ages(repo_root)
    stale = stale_branches(refs, time.time(), days)
    artifacts = _tracked_artifacts(repo_root)
    return {
        "open_prs": pr_report,
        "conflicts": [p for p in pr_report if p["state"] == "conflict"],
        "behind": [p for p in pr_report if p["state"] == "behind"],
        "stale_branches": stale,
        "artifacts": artifacts,
    }


def _pristine(rep) -> bool:
    return not (rep["conflicts"] or rep["stale_branches"] or rep["artifacts"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="read-only full-repo mess report")
    ap.add_argument("--repo", default="Fleezyflo/fanops")
    ap.add_argument("--root", default=".")
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        rep = sweep(args.repo, Path(args.root), args.stale_days)
    except Exception as exc:
        print(f"[repo-sweep] could not complete sweep ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(rep, indent=2)); return 0
    print(f"[repo-sweep] {args.repo}")
    print(f"  open PRs: {len(rep['open_prs'])}  (conflict: {len(rep['conflicts'])}, behind: {len(rep['behind'])})")
    for p in rep["open_prs"]:
        print(f"    #{p['number']} [{p['state']}]{' draft' if p['draft'] else ''}  {p['branch']}  — {p['title']}")
    print(f"  stale branches (>{args.stale_days}d): {len(rep['stale_branches'])}")
    for b in rep["stale_branches"]: print(f"    {b}")
    print(f"  leftover artifacts: {len(rep['artifacts'])}")
    for a in rep["artifacts"]: print(f"    {a}")
    print(f"  => repo pristine: {'YES' if _pristine(rep) else 'NO — drive the above to resolution via sub-agents'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
