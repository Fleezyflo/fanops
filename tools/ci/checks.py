"""DC-1..DC-6 — the six registry-integrity divergences (ADR-0100).

Each is a PURE function of (registry, discovered jobs) [+ live contexts for DC-3], so the
negative-control selftest can inject exactly one defect into a copy and assert the named DC fires.
Every Finding carries the control id and the EXACT divergence (ADR requirement: actionable output).
"""
from __future__ import annotations

import re

from .common import Finding

_SHA40 = re.compile(r"[0-9a-f]{40}$")


def _required_top(reg: dict) -> list[dict]:
    return [c for c in reg["controls"]
            if c.get("classification") == "required" and not c.get("parent")]


def dc1_renamed_required_context(reg: dict, jobs: list[dict]) -> list[Finding]:
    """A required context (or a declared context-list entry) that matches no workflow job name.
    The anti-silent-detach guard: an unmirrored rename deadlocks the merge queue (fails closed)."""
    names = {j["name"] for j in jobs}
    out: list[Finding] = []
    for c in _required_top(reg):
        ctx = c.get("branch_protection_context")
        if ctx not in names:
            out.append(Finding("DC-1", c["id"],
                f"required context {ctx!r} matches no workflow job name — rename/detach risk", True))
    for key in ("current_required_contexts", "intended_required_contexts"):
        for ctx in reg.get(key, []) or []:
            if ctx not in names:
                out.append(Finding("DC-1", "-",
                    f"{key} entry {ctx!r} matches no workflow job name", True))
    return out


def dc2_registry_jobs_bijection(reg: dict, jobs: list[dict]) -> list[Finding]:
    """(1) phantom control: a workflow-backed control whose (workflow, job) is not a real job;
    (2) unknown job: a workflow job with no registry control mapping to it."""
    out: list[Finding] = []
    real = {(j["workflow"], j["job_id"]) for j in jobs}
    mapped: set[tuple] = set()
    for c in reg["controls"]:
        if c.get("parent"):
            continue
        wf, job = c.get("workflow"), c.get("job")
        if wf and job:
            key = (wf.split("/")[-1], job)
            if key not in real:
                out.append(Finding("DC-2", c["id"],
                    f"phantom control — names workflow job {job!r} in {wf} that does not exist", True))
            else:
                mapped.add(key)
    for j in jobs:
        if (j["workflow"], j["job_id"]) not in mapped:
            out.append(Finding("DC-2", "-",
                f"unknown workflow job {j['job_id']!r} in {j['workflow']} — no registry control maps to it", True))
    return out


def dc3_deployed_state(reg: dict, live_contexts, live_error: str | None = None) -> list[Finding]:
    """Registry (declared) vs live GitHub required contexts. Rollout-aware, so it never
    self-deadlocks: DC-3 requires live == `current_required_contexts` (what SHOULD be live now) and
    reports the current->intended gap as a PLANNED TRANSITION (informational) until phase==enforced.
    A live-probe failure is an explicit non-authoritative SKIP, never a pass."""
    if live_error is not None:
        return [Finding("DC-3", "-",
            f"NON-AUTHORITATIVE: live protection unreadable ({live_error}) — deployed-state not verified",
            blocking=False, skipped=True)]
    phase = (reg.get("rollout") or {}).get("phase", "transitioning")
    current = set(reg.get("current_required_contexts", []) or [])
    intended = set(reg.get("intended_required_contexts", []) or [])
    live = set(live_contexts or [])
    out: list[Finding] = []
    if live != current:
        out.append(Finding("DC-3", "-",
            f"live required != declared current — missing={sorted(current - live)} unexpected={sorted(live - current)}", True))
    gap = intended - current
    if phase != "enforced":
        if gap:
            out.append(Finding("DC-3", "-",
                f"PLANNED TRANSITION — {len(gap)} context(s) pending Phase E: {sorted(gap)}",
                blocking=False))
    elif current != intended:
        out.append(Finding("DC-3", "-",
            "phase=enforced but current_required_contexts != intended_required_contexts", True))
    return out


def dc4_prose_matches_classification(reg: dict, prose_docs) -> list[Finding]:
    """A hand-maintained doc that names a required context but calls it advisory (or vice versa).
    Deterministic: exact context-string match plus a contradicting status word."""
    ctx_to_class = {c.get("branch_protection_context"): c.get("classification")
                    for c in reg["controls"] if c.get("branch_protection_context")}
    out: list[Finding] = []
    for doc in prose_docs:
        if not doc.exists():
            continue
        for i, line in enumerate(doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            for ctx, cls in ctx_to_class.items():
                if not ctx or ctx not in line:
                    continue
                if cls == "required" and "advisory" in low:
                    out.append(Finding("DC-4", ctx,
                        f"{doc.name}:{i} calls a REQUIRED context 'advisory'", True))
                elif cls == "advisory" and re.search(r"\brequired\b", low) and "not required" not in low:
                    out.append(Finding("DC-4", ctx,
                        f"{doc.name}:{i} calls an ADVISORY context 'required'", True))
    return out


def dc5_duplicate_ownership(reg: dict) -> list[Finding]:
    """Two controls sharing an invariant without an explicit, complete duplicate_group; and
    duplicate_group referential integrity (>=2 real members, each with a distinct boundary)."""
    out: list[Finding] = []
    ids = {c["id"] for c in reg["controls"]}
    grouped: dict[str, set] = {}
    for gname, g in (reg.get("duplicate_groups") or {}).items():
        members = g.get("members") or []
        boundaries = g.get("distinct_boundaries") or {}
        if len(members) < 2:
            out.append(Finding("DC-5", gname, "duplicate_group has fewer than 2 members", True))
        for m in members:
            if m not in ids:
                out.append(Finding("DC-5", gname, f"duplicate_group names unknown control {m!r}", True))
            if m not in boundaries:
                out.append(Finding("DC-5", gname, f"member {m!r} has no distinct_boundaries entry", True))
            grouped.setdefault(m, set()).add(gname)
    by_inv: dict[str, list[str]] = {}
    for c in reg["controls"]:
        by_inv.setdefault(c.get("invariant"), []).append(c["id"])
    for inv, members in by_inv.items():
        if len(members) < 2:
            continue
        common = set.intersection(*(grouped.get(m, set()) for m in members))
        if not common:
            out.append(Finding("DC-5", ",".join(sorted(members)),
                "controls share a byte-identical invariant but are not in a common duplicate_group", True))
    return out


def dc6_workflow_hygiene(reg: dict, jobs: list[dict]) -> list[Finding]:
    """Every job has a timeout; every action `uses:` is pinned to a 40-hex SHA."""
    out: list[Finding] = []
    ctx_to_id = {c.get("branch_protection_context"): c.get("id") for c in reg["controls"]}
    for j in jobs:
        cid = ctx_to_id.get(j["name"]) or j["job_id"]
        if j["timeout"] is None:
            out.append(Finding("DC-6", cid,
                f"job {j['job_id']} in {j['workflow']} has no timeout-minutes", True))
        for u in j["uses"]:
            ref = u.split("@", 1)[1] if "@" in u else ""
            if not _SHA40.match(ref):
                out.append(Finding("DC-6", cid,
                    f"job {j['job_id']} uses floating action {u!r} (not a 40-hex SHA pin)", True))
    return out


def run_static(reg: dict, jobs: list[dict], prose_docs) -> list[Finding]:
    """Static plane: registry <-> workflow implementation (no network). DC-1/2/4/5/6."""
    return (dc1_renamed_required_context(reg, jobs)
            + dc2_registry_jobs_bijection(reg, jobs)
            + dc4_prose_matches_classification(reg, prose_docs)
            + dc5_duplicate_ownership(reg)
            + dc6_workflow_hygiene(reg, jobs))


def run_deployed(reg: dict, live_contexts, live_error: str | None = None) -> list[Finding]:
    """Deployed-state plane: registry <-> live GitHub protection. DC-3."""
    return dc3_deployed_state(reg, live_contexts, live_error)
