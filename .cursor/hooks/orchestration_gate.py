#!/usr/bin/env python3
"""orchestration_gate.py — the MECHANICAL enforcement of the delegation-only orchestrator contract.

Wired from `.cursor/hooks.json` (Cursor-native hooks; cloud-executed, `failClosed: true`). It reads the
hook event JSON on stdin and prints a native decision object `{"permission": "allow"|"deny", ...}`.

Because Cursor's `preToolUse` payload carries NO caller identity (docs/hooks.md), a Write-deny cannot be
scoped to "orchestrator only" without also blocking workers. So enforcement lives at boundaries this
gate CAN judge deterministically from the command string / event payload, caller-agnostic:

  before-shell (beforeShellExecution):
    * LAND-to-main (`gh pr merge …`) is DENIED unless every Linear unit the PR carries has a sub-agent
      VERIFICATION RECORD (guardrail: the orchestrator cannot land work a sub-agent has not verified).
    * destructive git (`reset --hard`, force-push/direct-push to main, re-cut `checkout -B … origin/main`)
      is DENIED (repo safety, mirrors .githooks/pre-push).
    * everything else (worker commits/pushes to feature branches, reads) is allowed — workers must work.
  subagent-start / subagent-stop (subagentStart/subagentStop):
    * append an ATTRIBUTION LEDGER entry (guardrail: record which sub-agent did each unit); always allow
      the spawn (delegation is the point).

Fail posture: security decisions are emitted explicitly; on an unexpected internal error the script
exits non-zero so `failClosed: true` in hooks.json turns it into a BLOCK (never silently allow a land).

State lives under `.orchestration/state/` (see .orchestration/SPEC.md): `verified/<UNIT>.json` records +
`ledger.jsonl`. Root = --root arg, else $CURSOR_PROJECT_DIR, else cwd.
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

_MOL_RE = re.compile(r"(?i)\bmol-(\d+)\b")


def _root(arg_root=None) -> Path:
    return Path(arg_root or os.environ.get("CURSOR_PROJECT_DIR") or os.getcwd())


# ---- pure classification ----------------------------------------------------

def classify_command(cmd: str) -> str:
    """One of: 'land' (gh pr merge), 'destructive' (forbidden), 'read' (safe read-only), 'other'."""
    c = " ".join((cmd or "").split())
    cl = c.lower()
    # destructive / forbidden (mirror .githooks/pre-push + AGENTS.md guardrail #1)
    if re.search(r"\bgit\s+reset\s+--hard\b", cl): return "destructive"
    if re.search(r"\bgit\s+push\b.*\s(--force|-f|--force-with-lease)\b", cl) and "main" in cl: return "destructive"
    if re.search(r"\bgit\s+push\b[^|&]*\borigin\s+main\b", cl): return "destructive"
    # case-SENSITIVE: only `-B` (force re-cut) is destructive; `-b`/`worktree add -b … origin/main` is sanctioned
    if re.search(r"\bgit\s+checkout\s+-B\b[^|&]*origin/main\b", c): return "destructive"
    # land to main
    if re.search(r"\bgh\s+pr\s+merge\b", cl): return "land"
    # read-only inspection
    if re.match(r"^(git\s+(status|log|diff|show|branch|fetch|remote|rev-parse|ls-files|merge-base)"
                r"|gh\s+(pr|run|issue)\s+(list|view|checks|diff|status)"
                r"|ls|cat|rg|grep|head|tail|pwd|echo|find|wc)\b", cl): return "read"
    return "other"


def parse_pr_merge(cmd: str):
    """Return the PR number string from a `gh pr merge <n>` command, else None."""
    m = re.search(r"\bgh\s+pr\s+merge\b(?:\s+--\S+)*\s+(\d+)", " ".join((cmd or "").split()))
    return m.group(1) if m else None


def unit_ids_from_text(text: str) -> list:
    """Canonical Linear ids (e.g. MOL-190) found in a branch/PR title/body, de-duped in order."""
    out, seen = [], set()
    for m in _MOL_RE.finditer(text or ""):
        u = f"MOL-{m.group(1)}"
        if u not in seen: seen.add(u); out.append(u)
    return out


def is_unit_verified(unit_id: str, root) -> tuple:
    """A unit is verified iff .orchestration/state/verified/<UNIT>.json exists, is valid, passed==true,
    and names a NON-orchestrator verifier sub-agent. Returns (ok, reason)."""
    p = _root(root) / ".orchestration" / "state" / "verified" / f"{unit_id}.json"
    if not p.exists(): return False, f"no verification record for {unit_id}"
    try:
        rec = json.loads(p.read_text())
    except Exception as exc:
        return False, f"corrupt verification record for {unit_id}: {type(exc).__name__}"
    if rec.get("passed") is not True: return False, f"{unit_id} verification not passed"
    verifier = str(rec.get("verifier") or "").strip()
    if not verifier or verifier.lower() == "orchestrator":
        return False, f"{unit_id} verifier must be a sub-agent, not the orchestrator (got {verifier!r})"
    return True, "verified"


def land_decision(unit_ids: list, root) -> tuple:
    """Allow a land only when at least one unit is identified AND every identified unit is verified."""
    if not unit_ids:
        return False, ("land refused: no Linear unit id found on the PR/branch — cannot confirm a "
                       "sub-agent verified this work. Tag the unit (MOL-xxx) and have a verifier sub-agent "
                       "write its record.")
    for u in unit_ids:
        ok, reason = is_unit_verified(u, root)
        if not ok:
            return False, f"land refused: {reason}. A verifier sub-agent must record verification first."
    return True, "all units verified"


def append_ledger(root, entry: dict) -> None:
    d = _root(root) / ".orchestration" / "state"
    d.mkdir(parents=True, exist_ok=True)
    import datetime
    entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), **entry}
    with (d / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


# ---- I/O wrapper (gh lookup for a PR's units) -------------------------------

def _pr_units(pr_number: str) -> list:
    """Units a PR carries = MOL ids in its head branch + title + body (best-effort via gh)."""
    try:
        out = subprocess.run(["gh", "pr", "view", pr_number, "--json", "headRefName,title,body"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0: return []
        d = json.loads(out.stdout)
        return unit_ids_from_text(" ".join([d.get("headRefName", ""), d.get("title", ""), d.get("body", "")]))
    except Exception:
        return []


def _emit(permission: str, agent_message: str = "", user_message: str = "") -> int:
    obj = {"permission": permission}
    if agent_message: obj["agent_message"] = agent_message
    if user_message: obj["user_message"] = user_message
    print(json.dumps(obj))
    return 0


# ---- event handlers ---------------------------------------------------------

def handle_before_shell(data: dict, root) -> int:
    cmd = data.get("command", "")
    kind = classify_command(cmd)
    if kind == "destructive":
        return _emit("deny", agent_message=(
            "REFUSED (orchestration gate): destructive/forbidden git command. Never reset --hard, "
            "force-push, push directly to main, or re-cut a branch. Land via `gh pr merge` on a verified PR."))
    if kind == "land":
        pr = parse_pr_merge(cmd)
        units = _pr_units(pr) if pr else []
        allow, reason = land_decision(units, root)
        if not allow:
            return _emit("deny", agent_message=f"REFUSED (orchestration gate): {reason}")
        append_ledger(root, {"event": "land", "pr": pr, "units": units, "command": cmd})
        return _emit("allow", agent_message=f"land allowed: {', '.join(units)} verified by sub-agent(s).")
    return _emit("allow")


def handle_subagent_start(data: dict, root) -> int:
    append_ledger(root, {"event": "subagent_start", "subagent_id": data.get("subagent_id"),
                         "subagent_type": data.get("subagent_type"), "task": data.get("task"),
                         "parent_conversation_id": data.get("parent_conversation_id"),
                         "is_parallel_worker": data.get("is_parallel_worker"),
                         "git_branch": data.get("git_branch")})
    return _emit("allow")


def handle_subagent_stop(data: dict, root) -> int:
    append_ledger(root, {"event": "subagent_stop", "subagent_type": data.get("subagent_type"),
                         "task": data.get("task"), "status": data.get("status"),
                         "modified_files": data.get("modified_files"),
                         "summary": (data.get("summary") or "")[:500]})
    return _emit("allow")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("event", choices=["before-shell", "subagent-start", "subagent-stop"])
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        # malformed payload on a security event -> non-zero so failClosed blocks (never silent-allow a land)
        print(json.dumps({"permission": "deny", "agent_message": "orchestration gate: unreadable hook payload"}))
        return 0
    if args.event == "before-shell": return handle_before_shell(data, args.root)
    if args.event == "subagent-start": return handle_subagent_start(data, args.root)
    if args.event == "subagent-stop": return handle_subagent_stop(data, args.root)
    return _emit("allow")


if __name__ == "__main__":
    raise SystemExit(main())
