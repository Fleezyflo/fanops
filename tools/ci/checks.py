"""DC-1, DC-2, DC-3, DC-4, DC-6, DC-7 — the registry-integrity divergences.

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
                f"PLANNED TRANSITION — {len(gap)} context(s) pending Operational Governance Deployment: {sorted(gap)}",
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


# DC-5 (duplicate ownership) was deleted 2026-07-26 along with the `duplicate_groups` block and the
# `invariant` field it keyed on. It policed a duplication the registry itself manufactured: of its four
# groups, two paired a real job with a `LOCAL-*` git-hook row that existed for no other purpose, and
# the other two paired a job with a sub-row describing a STEP of another job. Delete the sub-rows and
# the hook rows — neither was ever verified against anything — and every group empties.
#
# The duplication that actually mattered here was COMPUTE: the `gate` job re-running, per PR, what the
# required unit lane already blocks on. No declared-invariant string could have caught that, and it is
# fixed by deleting the job, not by registering the overlap.


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


def dc7_advisory_must_not_hard_fail(reg: dict, jobs: list[dict]) -> list[Finding]:
    """An ADVISORY job that can fail the workflow. Red nobody is able to act on.

    Nothing blocks on an advisory context, so its failure cannot stop a merge — it can only train
    people to merge past red, and that habit does not stay confined to the advisory board. It is
    also precisely the decoration docs/ENFORCEMENT.md forbids in its own first paragraph: authority
    claimed without a mechanism.

    A job gets exactly two honest shapes:
      required  -> it may fail, and its failure blocks.
      advisory  -> it reports; `continue-on-error: true` keeps the failure a visible annotation
                   instead of a red check nobody can clear.

    Why no control caught this before: DC-1/2/5 reconcile NAMES and ownership, DC-3 reconciles the
    deployed context list, DC-4 reconciles PROSE against classification, DC-6 checks hygiene
    (timeouts, pinned actions). Every one of them compares a declaration to another declaration.
    None asked what the job DOES when it fails. This one does."""
    # Key on (workflow, job) exactly as DC-2's bijection does. Keying on the control id or the
    # branch-protection context instead SILENTLY SKIPS every control whose id differs from its
    # job_id — which is most of them, including the two worst offenders. A control that quietly
    # covers less than it claims is the failure this whole module exists to catch.
    by_job = {(c["workflow"].split("/")[-1], c["job"]): c
              for c in reg["controls"] if c.get("workflow") and c.get("job") and not c.get("parent")}
    out: list[Finding] = []
    for j in jobs:
        c = by_job.get((j["workflow"], j["job_id"]))
        if c is None or c.get("classification") != "advisory" or j.get("continue_on_error"):
            continue
        out.append(Finding("DC-7", c["id"],
            f"job {j['job_id']} ({j['name']!r}, {j['workflow']}) is classified ADVISORY but can "
            f"FAIL the workflow — a red no one can block on. Either mark the job "
            f"`continue-on-error: true` so it reports, or make it a required context.", True))
    return out


def run_static(reg: dict, jobs: list[dict], prose_docs) -> list[Finding]:
    """Static plane: registry <-> workflow implementation (no network). DC-1/2/4/6/7."""
    return (dc1_renamed_required_context(reg, jobs)
            + dc2_registry_jobs_bijection(reg, jobs)
            + dc4_prose_matches_classification(reg, prose_docs)
            + dc6_workflow_hygiene(reg, jobs)
            + dc7_advisory_must_not_hard_fail(reg, jobs))


def run_deployed(reg: dict, live_contexts, live_error: str | None = None) -> list[Finding]:
    """Deployed-state plane: registry <-> live GitHub protection. DC-3."""
    return dc3_deployed_state(reg, live_contexts, live_error)
