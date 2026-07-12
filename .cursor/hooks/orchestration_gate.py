#!/usr/bin/env python3
"""orchestration_gate.py — the MECHANICAL enforcement of the delegation-only orchestrator contract.

Wired from `.cursor/hooks.json` (Cursor-native hooks; cloud-executed, `failClosed: true`). It reads the
hook event JSON on stdin and prints a native decision object `{"permission": "allow"|"deny", ...}`.

Because Cursor's `preToolUse` payload carries NO caller identity (docs/hooks.md), a Write-deny cannot be
scoped to "orchestrator only" without also blocking workers. So enforcement lives at boundaries this
gate CAN judge deterministically from the command string / event payload, caller-agnostic:

  before-shell (beforeShellExecution):
    * LAND-to-main (`gh pr merge …`) is DENIED unless every unit the PR carries has a sub-agent
      VERIFICATION RECORD whose `head_sha` matches the PR's CURRENT head (guardrail: nothing lands
      unverified, and nothing lands on commits the verifier never saw — stale record → re-verify).
    * destructive git (`reset --hard`, force-push/direct-push to main, re-cut `checkout -B … origin/main`)
      is DENIED (repo safety, mirrors .githooks/pre-push).
    * everything else (worker commits/pushes to feature branches, reads) is allowed — workers must work.
  subagent-start (subagentStart):
    * DENY any spawn whose subagent_type is not in _WAVE_AGENTS (guardrail: every wave spawn is a named
      agent whose frontmatter PINS `model: inherit` — ad-hoc types (general-purpose/shell/…) are where a
      spawn-time model takes effect, and a second `fanops-orchestrator` mid-wave is the double-merge
      incident). Allowed spawns are ledgered WITH their subagent_model for audit.
  subagent-stop (subagentStop):
    * append an ATTRIBUTION LEDGER entry (record which sub-agent did each unit; cannot deny).

Fail posture: security decisions are emitted explicitly; on an unexpected internal error the script
exits non-zero so `failClosed: true` in hooks.json turns it into a BLOCK (never silently allow a land).

State lives under `.orchestration/state/` (see .orchestration/SPEC.md): `verified/<UNIT>.json` records +
`ledger.jsonl`. Root = --root arg, else $CURSOR_PROJECT_DIR, else cwd.
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

_MOL_RE = re.compile(r"(?i)\bmol-(\d+)\b")
_SLUG = r"[a-z0-9][a-z0-9-]*"
_UNIT_TAG_RE = re.compile(rf"(?i)\bunit:\s*({_SLUG})")
_BRANCH_SLUG_RE = re.compile(rf"^(?:feat|fix)/({_SLUG})", re.I)
_TITLE_PAREN_RE = re.compile(rf"\(({_SLUG})\)\s*$")

# Paths whose modification would forge a verification record or DISABLE the enforcement itself.
# Writing to any of these via shell is refused for everyone (workers edit src, never the machinery/state).
_PROTECTED_PATHS = (".orchestration/state/", ".cursor/hooks.json", ".cursor/hooks/", ".githooks/",
                    ".claude/settings.json", ".claude/hooks/")
_MUTATING = re.compile(
    r"(>>?|<<|\btee\b|\bsed\s+-i|\bperl\s+-i|\bawk\b[^|]*>|"
    r"\b(cp|mv|rm|ln|dd|truncate|install|chmod|chown|touch|mkdir|rmdir)\b|"
    r"\bpython3?\b|\bgit\s+(rm|checkout|restore|clean|mv)\b)")
    # `<<` + `python3?`: a verifier was observed writing a protected verification record via a
    # `python3 <<'PY' … open(...,'w')` heredoc — interpreters and heredocs count as mutators, so any
    # command that names a protected path AND invokes one is refused (reads use the Read tool instead).

# Enforcement machinery that must NEVER be dirty in the working tree during a wave — changes arrive via
# reviewed PRs (committed), not live edits. The Write tool can't be hooked, so the LAND is where a live
# edit is caught: a wave observed an orchestrator commissioning a Write-tool edit of this very gate to
# get a refused PR through.
_ENFORCEMENT_PATHS = (".cursor/hooks.json", ".cursor/hooks", ".githooks",
                      ".claude/settings.json", ".claude/hooks",
                      "scripts/orchestrate.py", "scripts/repo_sweep.py")


def enforcement_dirty(root) -> list:
    """Enforcement files modified/untracked in the WORKING TREE (tamper signal). Returns the dirty
    status lines, or a '(unverifiable…)' sentinel when git cannot answer — the land fails CLOSED."""
    try:
        out = subprocess.run(["git", "-C", str(_root(root)), "status", "--porcelain", "--"]
                             + list(_ENFORCEMENT_PATHS),
                             capture_output=True, text=True, timeout=15)
        if out.returncode != 0: return ["(unverifiable: git status failed)"]
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        return ["(unverifiable: git unavailable)"]

# The ONLY subagent types an active wave may spawn. Both are named agents whose .cursor/agents/*.md
# frontmatter pins `model: inherit`, so a spawn-time model override is inert by construction. Everything
# else is refused: ad-hoc types (general-purpose, shell, …) take a spawn-time model, and a second
# `fanops-orchestrator` during a wave is the parallel-orchestrator double-merge failure.
_WAVE_AGENTS = {"fanops-worker", "fanops-lander"}

# Local test execution is operator-FORBIDDEN: a wave runs many workers on ONE machine, and N parallel
# pytest suites take it down. Tests run ONLY in GitHub CI on the PR. Matches command-position pytest /
# python -m pytest / check-full.sh (env-var and bash/path prefixes included); check.sh stays allowed —
# it is scoped LINT only.
_LOCAL_TESTS = re.compile(
    r"(?:^|[|&;])\s*(?:[A-Z_]+=\S+\s+)*(?:bash\s+|sh\s+)?(?:\S*/)?"
    r"(?:pytest\b|python3?\s+-m\s+pytest\b|check-full\.sh\b)")


def _root(arg_root=None) -> Path:
    return Path(arg_root or os.environ.get("CURSOR_PROJECT_DIR") or os.getcwd())


def is_active(root=None) -> bool:
    """Is the delegation-only orchestration ENVIRONMENT engaged for this run? The gate enforces ONLY when
    active, so committing this hook to the repo does NOT change behavior for normal / other-agent Cursor
    sessions (no collateral). Activation (set by the OPERATOR, not the orchestrator):
      * env `FANOPS_ORCHESTRATED` in {1,true,yes,on}  — robust (the agent can't delete an env var), OR
      * a marker file `.orchestration/state/ACTIVE`     — convenient (git-ignored, per-run).
    While active, both are protected from tampering (the marker sits under the guarded state dir)."""
    if str(os.environ.get("FANOPS_ORCHESTRATED", "")).strip().lower() in ("1", "true", "yes", "on"):
        return True
    return (_root(root) / ".orchestration" / "state" / "ACTIVE").exists()


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
    # land to main — both `gh pr merge` AND the raw API merge (`gh api … pulls/<n>/merge`)
    if re.search(r"\bgh\s+pr\s+merge\b", cl): return "land"
    if re.search(r"\bgh\s+api\b", cl) and re.search(r"pulls/\d+/merge\b", cl): return "land"
    # read-only inspection
    if re.match(r"^(git\s+(status|log|diff|show|branch|fetch|remote|rev-parse|ls-files|merge-base)"
                r"|gh\s+(pr|run|issue)\s+(list|view|checks|diff|status)"
                r"|ls|cat|rg|grep|head|tail|pwd|echo|find|wc)\b", cl): return "read"
    return "other"


def parse_pr_merge(cmd: str):
    """Return the PR number string from a `gh pr merge <n>` OR `gh api … pulls/<n>/merge` command."""
    c = " ".join((cmd or "").split())
    m = re.search(r"\bgh\s+pr\s+merge\b(?:\s+--?\S+)*\s+(\d+)", c)
    if m: return m.group(1)
    m = re.search(r"pulls/(\d+)/merge\b", c)
    return m.group(1) if m else None


def protected_write_target(cmd: str):
    """If a command would MUTATE a protected path (verification state or the enforcement machinery),
    return that path; else None. This closes the 'forge a verification record / disable the gate via
    shell' bypass (e.g. `echo … > .orchestration/state/verified/X.json`, `rm .cursor/hooks.json`)."""
    c = " ".join((cmd or "").split())
    if not _MUTATING.search(c): return None
    for pp in _PROTECTED_PATHS:
        if pp in c: return pp
    return None


def prefer_units(branch: str, title: str = "", body: str = "") -> list:
    """Authoritative unit(s) for a land: the branch's MOL id if present, else the title's, else the body's.
    Prevents an incidental `MOL-x` mention in a PR body from demanding (or dodging) verification."""
    for src in (branch, title, body):
        u = unit_ids_from_text(src)
        if u: return u
    return []


def unit_ids_from_text(text: str) -> list:
    """Canonical unit ids (e.g. MOL-190 or pipeline-artifact-resume) in branch/PR title/body, de-duped."""
    text = text or ""
    out, seen = [], set()
    for m in _MOL_RE.finditer(text):
        u = f"MOL-{m.group(1)}"
        if u not in seen: seen.add(u); out.append(u)
    if out: return out
    def _add(slug: str):
        slug = slug.lower()
        if slug and slug not in seen: seen.add(slug); out.append(slug)
    for m in _UNIT_TAG_RE.finditer(text): _add(m.group(1))
    m = _BRANCH_SLUG_RE.match(text.strip())
    if m: _add(m.group(1))
    m = _TITLE_PAREN_RE.search(text.strip())
    if m: _add(m.group(1))
    return out


def is_unit_verified(unit_id: str, root, head_sha: str = "") -> tuple:
    """A unit is verified iff .orchestration/state/verified/<UNIT>.json exists, is valid, passed==true,
    names a NON-orchestrator verifier sub-agent, and its head_sha matches the PR's CURRENT head (a
    record for commits the verifier never saw is STALE). Returns (ok, reason)."""
    p = _root(root) / ".orchestration" / "state" / "verified" / f"{unit_id}.json"
    if not p.exists(): return False, f"no verification record for {unit_id}"
    try:
        rec = json.loads(p.read_text())
    except Exception as exc:
        return False, f"corrupt verification record for {unit_id}: {type(exc).__name__}"
    if rec.get("passed") is not True: return False, f"{unit_id} verification not passed"
    verifier = str(rec.get("verifier") or "").strip()
    executor = str(rec.get("executor") or "").strip()
    if not verifier or verifier.lower() == "orchestrator":
        return False, f"{unit_id} verifier must be a sub-agent, not the orchestrator (got {verifier!r})"
    if executor and verifier == executor:
        return False, f"{unit_id} verifier must DIFFER from the executor (no self-verification: {verifier!r})"
    rec_head = str(rec.get("head_sha") or "").strip()
    if not rec_head:
        return False, (f"{unit_id} record has no head_sha — the verifier must pin the PR head commit it "
                       "verified (gh pr view <n> --json headRefOid)")
    if head_sha and rec_head != head_sha:
        return False, (f"{unit_id} verification is STALE: verified head {rec_head[:12]} but the PR is now "
                       f"at {head_sha[:12]} — new commits need ONE re-verify (this is the only re-verify "
                       "trigger; never re-verify an unchanged PR)")
    return True, "verified"


def land_decision(unit_ids: list, root, head_sha: str = "") -> tuple:
    """Allow a land only when at least one unit is identified AND every identified unit is verified
    against the PR's current head."""
    if not unit_ids:
        return False, ("land refused: no unit id found on the PR/branch — cannot confirm a "
                       "sub-agent verified this work. Tag the unit (MOL-xxx or Unit: <slug>) and have a "
                       "verifier sub-agent write its record.")
    for u in unit_ids:
        ok, reason = is_unit_verified(u, root, head_sha)
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

def enforcement_hits(paths) -> list:
    """Subset of `paths` that fall under the enforcement machinery (pure; testable)."""
    out = []
    for p in paths:
        p = (p or "").strip()
        if not p: continue
        if (p == ".cursor/hooks.json" or p.startswith(".cursor/hooks/") or p.startswith(".githooks/")
                or p in ("scripts/orchestrate.py", "scripts/repo_sweep.py")):
            out.append(p)
    return out


def _pr_enforcement_files(pr_number: str) -> list:
    """Enforcement files a PR modifies (best-effort via gh; unverifiable → blocking sentinel)."""
    try:
        out = subprocess.run(["gh", "pr", "view", pr_number, "--json", "files",
                              "-q", ".files[].path"], capture_output=True, text=True, timeout=30)
        if out.returncode != 0: return ["(unverifiable: gh files lookup failed)"]
        return enforcement_hits(out.stdout.splitlines())
    except Exception:
        return ["(unverifiable: gh unavailable)"]


def _pr_units(pr_number: str) -> tuple:
    """(units, head_sha) for a PR: unit ids in its head branch + title + body, and the CURRENT head
    commit (headRefOid) for verification-staleness checks (best-effort via gh)."""
    try:
        out = subprocess.run(["gh", "pr", "view", pr_number, "--json", "headRefName,title,body,headRefOid"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0: return [], ""
        d = json.loads(out.stdout)
        units = prefer_units(d.get("headRefName", ""), d.get("title", ""), d.get("body", ""))
        return units, str(d.get("headRefOid") or "")
    except Exception:
        return [], ""


def _emit(permission: str, agent_message: str = "", user_message: str = "") -> int:
    obj = {"permission": permission}
    if agent_message: obj["agent_message"] = agent_message
    if user_message: obj["user_message"] = user_message
    print(json.dumps(obj))
    return 0


# ---- event handlers ---------------------------------------------------------

def handle_before_shell(data: dict, root) -> int:
    if not is_active(root):
        return _emit("allow")          # inert outside the orchestration environment — no collateral
    cmd = data.get("command", "")
    if re.search(r"orchestrate\.py\s+stop\b", cmd):
        return _emit("deny", agent_message=(
            "REFUSED (orchestration gate): `orchestrate.py stop` is OPERATOR-ONLY (human terminal). "
            "Enforcement cannot be turned off from inside the run — finish the wave; `done` disengages "
            "itself when it exits 0."))
    if _LOCAL_TESTS.search(cmd):
        return _emit("deny", agent_message=(
            "REFUSED (orchestration gate): local test runs are FORBIDDEN — the suite executes ONLY in "
            "GitHub CI on your PR (parallel local suite runs take the operator's machine down). Write "
            "the tests with your change, push the branch, open the PR, and cite the CI run as evidence. "
            "`./scripts/check.sh` (scoped lint) is the only local gate."))
    pp = protected_write_target(cmd)
    if pp:
        return _emit("deny", agent_message=(
            f"REFUSED (orchestration gate): this command would modify a PROTECTED path ({pp}). The "
            "verification state and the enforcement machinery (.cursor/hooks*, .githooks) may not be "
            "written from the shell — that would forge a verification record or disable the gate. A "
            "verifier SUB-AGENT records verification via the Write tool per .agents/_worker-protocol.md."))
    kind = classify_command(cmd)
    if kind == "destructive":
        return _emit("deny", agent_message=(
            "REFUSED (orchestration gate): destructive/forbidden git command. Never reset --hard, "
            "force-push, push directly to main, or re-cut a branch. Land via `gh pr merge` on a verified PR."))
    if kind == "land":
        dirty = enforcement_dirty(root)
        if dirty:
            return _emit("deny", agent_message=(
                "REFUSED (orchestration gate): enforcement machinery is MODIFIED in the working tree ("
                + "; ".join(dirty[:5]) + ") — the gate, hooks, and DONE-gate scripts may never be edited "
                "during a wave, by any tool. Report this to the operator; enforcement changes land via a "
                "reviewed PR before a wave, never mid-wave."))
        pr = parse_pr_merge(cmd)
        if pr:
            touched = _pr_enforcement_files(pr)
            if touched:
                return _emit("deny", agent_message=(
                    "REFUSED (orchestration gate): this PR modifies enforcement machinery ("
                    + ", ".join(touched[:5]) + "). Enforcement changes are OPERATOR-ONLY and merge "
                    "outside a wave — report to the operator; do not attempt another route."))
        units, head_sha = _pr_units(pr) if pr else ([], "")
        allow, reason = land_decision(units, root, head_sha)
        if not allow:
            return _emit("deny", agent_message=f"REFUSED (orchestration gate): {reason}")
        append_ledger(root, {"event": "land", "pr": pr, "units": units, "head_sha": head_sha,
                             "command": cmd})
        return _emit("allow", agent_message=f"land allowed: {', '.join(units)} verified by sub-agent(s).")
    return _emit("allow")


def handle_subagent_start(data: dict, root) -> int:
    stype = str(data.get("subagent_type") or "")
    entry = {"event": "subagent_start", "subagent_id": data.get("subagent_id"),
             "subagent_type": stype, "subagent_model": data.get("subagent_model"),
             "task": data.get("task"),
             "parent_conversation_id": data.get("parent_conversation_id"),
             "is_parallel_worker": data.get("is_parallel_worker"),
             "git_branch": data.get("git_branch")}
    if stype == "fanops-orchestrator":
        # UNCONDITIONAL — wave or not. A nested orchestrator cannot spawn workers, so this spawn always
        # dead-ends; `/fanops-orchestrator <plan>` in a chat is exactly this spawn. The deny redirects
        # the CALLER to take over top-level, converting the broken launch into the supported hand-off.
        msg_active = (
            "REFUSED (orchestration gate): a wave is already ACTIVE — one orchestrator at a time "
            "(parallel orchestrators caused double-merges), and the orchestrator never runs as a "
            "subagent. If the previous wave is over, run `python scripts/orchestrate.py stop` from a "
            "human terminal, then relaunch top-level (ORCHESTRATION.md §1).")
        msg_takeover = (
            "REFUSED (orchestration gate): `fanops-orchestrator` never runs as a subagent — nested, it "
            "cannot spawn workers, so the wave dead-ends. Do NOT retry this spawn and do NOT do the work "
            "yourself. Instead YOU become the orchestrator in THIS conversation: read "
            "`.cursor/agents/fanops-orchestrator.md` and follow it for the user's request, starting with "
            "`python scripts/orchestrate.py start`.")
        if is_active(root):
            append_ledger(root, {**entry, "event": "subagent_denied"})
            return _emit("deny", user_message=msg_active, agent_message=msg_active)
        return _emit("deny", user_message=msg_takeover, agent_message=msg_takeover)
    if not is_active(root):
        return _emit("allow")          # inert outside an orchestration run — no collateral
    if stype not in _WAVE_AGENTS:
        append_ledger(root, {**entry, "event": "subagent_denied"})
        return _emit("deny", user_message=(
            f"REFUSED (orchestration gate): spawn type {stype!r} is not allowed during a wave. Spawn the "
            "named `fanops-worker` agent (is_background: true, brief = unit + role + protocol file) — its "
            "model is pinned in .cursor/agents/fanops-worker.md; never spawn general-purpose/shell types "
            "and never set a model."))
    append_ledger(root, entry)
    return _emit("allow")


def handle_subagent_stop(data: dict, root) -> int:
    if not is_active(root):
        return _emit("allow")
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
        # Per-event fail posture: the SECURITY event (before-shell) fails CLOSED (deny) on a bad payload —
        # but ONLY when the orchestration environment is active, so a parse hiccup never blocks a normal /
        # other-agent session. Ledger events always fail OPEN.
        if args.event == "before-shell" and is_active(args.root):
            return _emit("deny", agent_message="orchestration gate: unreadable hook payload (failing closed)")
        return _emit("allow")
    if args.event == "before-shell": return handle_before_shell(data, args.root)
    if args.event == "subagent-start": return handle_subagent_start(data, args.root)
    if args.event == "subagent-stop": return handle_subagent_stop(data, args.root)
    return _emit("allow")


if __name__ == "__main__":
    raise SystemExit(main())
