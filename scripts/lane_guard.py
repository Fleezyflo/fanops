#!/usr/bin/env python3
"""Lane file-ownership guard — the MECHANICAL enforcement of the AGENTS.md 'disjoint hot files'
rule (previously advisory prose in the lane briefs). Consumed by .githooks/pre-push (local, fail-open
on infra errors) and .github/workflows/lane-guard.yml (CI, authoritative). Source of truth for lanes +
hot-file ownership is .agents/lanes.json.

Contract (kept green by tests/test_lane_guard.py):
  * OPT-IN by branch prefix. A branch whose name matches no lane prefix is IGNORED (exit 0) — normal
    branches (cursor/*, bycreamco/mol-*, human branches) are never blocked.
  * Only paths listed in guard.hot_files are restricted; every other path is unrestricted.
  * A change is a STRAY iff it edits a hot file whose owner lane(s) do NOT include the branch's lane.
    (A hot file may have a LIST of owners — a file shared between lanes and coordinated in TIME by the
    orchestrator, which this static guard cannot see.)
  * Infra errors (no git, missing/broken manifest) FAIL OPEN with a warning; a detected stray FAILS
    CLOSED (exit 1). Mirrors the repo norm: local hooks degrade, CI is the hard gate.

Lane resolution order (first hit wins): explicit --lane  >  branch prefix (`<lane>/`)  >  (with
--use-linear) the branch's MOL id looked up in Linear -> its label/project -> lane. The Linear step is
what lets this engage on your real per-ticket branches (`cursor/mol-156-…`, `fix/mol-169-…`) that carry
no lane prefix. It is BEST-EFFORT: no LINEAR_API_KEY / any network error -> lane stays unresolved and the
guard SKIPs (fail-open); the cross-PR collision guard (scripts/pr_collision_guard.py) is the always-on
protection that needs no Linear.

Usage:
  lane_guard.py [--branch REF] [--base REF] [--lane NAME] [--manifest PATH] [--changed a,b,c] [--use-linear]
"""
import argparse, json, os, re, subprocess, sys, urllib.request
from pathlib import Path

_MOL_RE = re.compile(r"(?i)\bmol-(\d+)\b")


def _repo_root() -> Path:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip(): return Path(out.stdout.strip())
    except Exception: pass
    return Path(__file__).resolve().parents[1]


def load_manifest(path=None) -> dict:
    p = Path(path) if path else (_repo_root() / ".agents" / "lanes.json")
    return json.loads(Path(p).read_text())


def lane_for_branch(branch: str, manifest: dict):
    """First lane whose branch_prefix is a prefix of `branch`; None if none match (guard no-ops)."""
    if not branch: return None
    for name, cfg in manifest.get("lanes", {}).items():
        for pref in cfg.get("branch_prefixes", []):
            if branch.startswith(pref): return name
    return None


def mol_id_from_branch(branch: str):
    """Extract the canonical Linear id (e.g. 'MOL-156') from any branch name, or None."""
    if not branch: return None
    m = _MOL_RE.search(branch)
    return f"MOL-{m.group(1)}" if m else None


def _lane_from_issue_fields(labels, project, manifest: dict):
    """Pure: map an issue's label names + project name to a lane via each lane's `linear` block."""
    labelset = set(labels or [])
    for name, cfg in manifest.get("lanes", {}).items():
        lin = cfg.get("linear", {})
        if project and lin.get("project") and project == lin["project"]: return name
        if labelset & set(lin.get("labels", [])): return name
    return None


def _parse_issue_payload(data: dict):
    """Pure: pull (label names, project name) out of a Linear GraphQL `issues` response."""
    nodes = (((data or {}).get("data") or {}).get("issues") or {}).get("nodes") or []
    if not nodes: return [], None
    n = nodes[0]
    labels = [x.get("name") for x in ((n.get("labels") or {}).get("nodes") or []) if x.get("name")]
    project = (n.get("project") or {}).get("name")
    return labels, project


def _fetch_linear_issue(mol_id: str, api_key: str, timeout: int = 10):
    """Best-effort Linear GraphQL fetch → (labels, project). Filters by team key + issue number."""
    key, num = mol_id.rsplit("-", 1)
    query = ("query($num:Float!,$key:String!){ issues(filter:{ number:{ eq:$num }, "
             "team:{ key:{ eq:$key } } }, first:1){ nodes{ project{ name } labels{ nodes{ name } } } } }")
    body = json.dumps({"query": query, "variables": {"num": float(num), "key": key}}).encode()
    req = urllib.request.Request("https://api.linear.app/graphql", data=body,
        headers={"Content-Type": "application/json", "Authorization": api_key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _parse_issue_payload(json.loads(r.read().decode()))


def lane_from_linear(branch: str, manifest: dict, api_key: str):
    """Resolve a lane from the branch's MOL id via Linear. None on no id / no key / any failure."""
    mol = mol_id_from_branch(branch)
    if not mol or not api_key: return None
    try:
        labels, project = _fetch_linear_issue(mol, api_key)
    except Exception as exc:
        print(f"[lane-guard] Linear lookup for {mol} failed ({type(exc).__name__}) — lane unresolved.", file=sys.stderr)
        return None
    return _lane_from_issue_fields(labels, project, manifest)


def _owners(owner) -> list:
    return [owner] if isinstance(owner, str) else list(owner)


def strays(changed, lane: str, manifest: dict) -> list:
    """Changed paths that edit a hot file NOT owned by `lane`. Stable order: as given in `changed`."""
    hot = manifest.get("guard", {}).get("hot_files", {})
    out = []
    for f in changed:
        if f in hot and lane not in _owners(hot[f]): out.append(f)
    return out


def evaluate(changed, branch: str, manifest: dict, lane_override=None):
    """Return (lane, strays). lane is None (=> no-op, empty strays) when the branch matches no lane."""
    lane = lane_override or lane_for_branch(branch, manifest)
    if lane is None: return None, []
    return lane, strays(changed, lane, manifest)


def _current_branch() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception: return ""


def _merge_base(base_ref: str) -> str:
    try:
        out = subprocess.run(["git", "merge-base", base_ref, "HEAD"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip(): return out.stdout.strip()
    except Exception: pass
    return base_ref


def _changed_files(base: str) -> list:
    out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMRT", base, "HEAD"],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0: raise RuntimeError(out.stderr.strip() or "git diff failed")
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lane file-ownership guard")
    ap.add_argument("--branch", default=None, help="branch name (default: current HEAD)")
    ap.add_argument("--base", default=None, help="base ref/sha to diff against (default: merge-base with origin/main)")
    ap.add_argument("--lane", default=None, help="force a lane, bypassing branch-prefix detection")
    ap.add_argument("--manifest", default=None, help="path to lanes.json (default: <repo>/.agents/lanes.json)")
    ap.add_argument("--changed", default=None, help="comma/space/newline list of changed paths (skip git)")
    ap.add_argument("--use-linear", action="store_true",
                    help="if no lane from --lane/branch-prefix, resolve via the branch's MOL id in Linear "
                         "(needs LINEAR_API_KEY; best-effort, fail-open)")
    args = ap.parse_args(argv)

    # --- Infra layer: FAIL OPEN. A broken manifest or absent git must never brick a push. ---
    try:
        manifest = load_manifest(args.manifest)
    except Exception as exc:
        print(f"[lane-guard] SKIP: could not load manifest ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0

    branch = args.branch or _current_branch()
    lane = args.lane or lane_for_branch(branch, manifest)
    if lane is None and args.use_linear:
        lane = lane_from_linear(branch, manifest, os.environ.get("LINEAR_API_KEY", ""))
        if lane: print(f"[lane-guard] resolved lane={lane} from Linear via {mol_id_from_branch(branch)}")
    if lane is None:
        print(f"[lane-guard] SKIP: branch {branch!r} maps to no lane (no prefix / no Linear match) — nothing to enforce.")
        return 0

    if args.changed is not None:
        changed = [c for c in args.changed.replace(",", "\n").split() if c]
    else:
        try:
            base = args.base or _merge_base("origin/main")
            changed = _changed_files(base)
        except Exception as exc:
            print(f"[lane-guard] SKIP: could not compute changed files ({type(exc).__name__}: {exc})", file=sys.stderr)
            return 0

    bad = strays(changed, lane, manifest)
    print(f"[lane-guard] lane={lane}  changed={len(changed)}  strays={len(bad)}")
    if not bad:
        print("[lane-guard] OK — no hot files owned by another lane were modified.")
        return 0

    hot = manifest["guard"]["hot_files"]
    print("[lane-guard] REFUSED — this branch edits hot files owned by another lane:", file=sys.stderr)
    for f in bad:
        print(f"    {f}  (owner: {', '.join(_owners(hot[f]))}; this lane: {lane})", file=sys.stderr)
    print("[lane-guard] Move this work to the owning lane, or re-assign the file in .agents/lanes.json "
          "if ownership truly changed for this wave.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
