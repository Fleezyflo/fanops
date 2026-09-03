"""Cross-open-PR hot-file collision detection."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from guards.manifest import load_manifest


def hot_set(changed, hot_files) -> set:
    """The subset of `changed` paths that are hot files (everything else is unrestricted)."""
    return {f for f in changed if f in hot_files}


def find_collisions(this_hot: set, others: dict) -> dict:
    """this_hot: set of hot paths this PR touches. others: {pr_id: set(hot paths)}.
    Return {hot_path: sorted[pr_id]} for every hot path this PR shares with another open PR."""
    out: dict = {}
    for pr, files in others.items():
        for f in set(files) & this_hot:
            out.setdefault(f, set()).add(pr)
    return {f: sorted(prs) for f, prs in out.items()}


def _gh_json(args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "gh failed")
    return json.loads(out.stdout)


def _pr_files(repo: str, num) -> list:
    data = _gh_json(["pr", "view", str(num), "--repo", repo, "--json", "files"])
    return [f["path"] for f in data.get("files", [])]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cross-open-PR hot-file collision guard")
    ap.add_argument("--pr", default=None, help="the PR number under test")
    ap.add_argument("--repo", default=None, help="owner/name")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--this-files", default=None, help="offline: comma/space list of this PR's changed paths")
    ap.add_argument("--others-json", default=None, help='offline: {"pr": ["file", ...], ...}')
    args = ap.parse_args(argv)

    try:
        hot = load_manifest(args.manifest).get("guard", {}).get("hot_files", {})
    except Exception as exc:
        print(f"[collision-guard] SKIP: could not load manifest ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0

    try:
        if args.this_files is not None:
            this_changed = [c for c in args.this_files.replace(",", " ").split() if c]
        else:
            this_changed = _pr_files(args.repo, args.pr)
        this_hot = hot_set(this_changed, hot)

        if args.others_json is not None:
            others = {k: hot_set(v, hot) for k, v in json.loads(args.others_json).items()}
        else:
            open_prs = _gh_json(
                ["pr", "list", "--repo", args.repo, "--state", "open", "--base", "main", "--json", "number"]
            )
            others = {
                p["number"]: hot_set(_pr_files(args.repo, p["number"]), hot)
                for p in open_prs
                if str(p["number"]) != str(args.pr)
            }
    except Exception as exc:
        print(
            f"[collision-guard] SKIP: could not gather PR file lists ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return 0

    if not this_hot:
        print("[collision-guard] OK — this PR touches no hot files; no cross-PR collision possible.")
        return 0

    collisions = find_collisions(this_hot, others)
    if not collisions:
        print(f"[collision-guard] OK — {len(this_hot)} hot file(s) touched; none shared with another open PR.")
        return 0

    print(
        "[collision-guard] REFUSED — hot files also being modified by other OPEN PR(s) to main:",
        file=sys.stderr,
    )
    for f, prs in sorted(collisions.items()):
        print(f"    {f}  also in PR(s): {', '.join('#' + str(p) for p in prs)}", file=sys.stderr)
    print(
        "[collision-guard] Two in-flight PRs on one hot file = merge drift. Land one, re-sync the other, "
        "then re-run — or split the work so the hot file lives in only one PR.",
        file=sys.stderr,
    )
    return 1
