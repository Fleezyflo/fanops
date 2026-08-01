"""DC-1, DC-2, DC-3, DC-4, DC-6, DC-7, DC-8, DC-9 — the registry-integrity divergences.

Each is a PURE function of (registry, discovered jobs) [+ the live GitHub reading for DC-3/8/9], so
the negative-control selftest can inject exactly one defect into a copy and assert the named DC
fires. Every Finding carries the control id and the EXACT divergence (actionable output).
"""
from __future__ import annotations

import re

from .common import DEFAULT_REPO, Finding

_SHA40 = re.compile(r"[0-9a-f]{40}$")

# GitHub's workflow `state` values, spelled out so a finding says WHO switched it off. The two that
# matter read identically in the API and have opposite causes: `disabled_manually` is a decision,
# `disabled_inactivity` is GitHub retiring a cron in a repo that went quiet for 60 days — which
# would silently take a scheduled control with it.
_WORKFLOW_STATES = {
    "disabled_manually": "switched off by an operator",
    "disabled_inactivity": "auto-disabled by GitHub after repository inactivity",
    "disabled_fork": "disabled because this repository is a fork",
}

# The exact re-enable command per declared setting. Dependabot security updates REQUIRE Dependabot
# alerts, and the alerts toggle is a separate endpoint — enabling only the fixes silently does
# nothing on a repo whose alerts are off, so both PUTs are named.
_SECURITY_REMEDIATION = {
    "dependabot_security_updates":
        f"gh api -X PUT repos/{DEFAULT_REPO}/vulnerability-alerts && "
        f"gh api -X PUT repos/{DEFAULT_REPO}/automated-security-fixes",
    "secret_scanning":
        "repository Settings -> Code security -> Secret scanning -> Enable",
    "secret_scanning_push_protection":
        "repository Settings -> Code security -> Push protection -> Enable",
}
# Valid GITHUB_TOKEN permission keys. Deliberately the UNION of every authoritative source, not any
# one of them: this list REJECTS, so an over-narrow entry reddens the required unit lane on a
# workflow GitHub would have accepted. Sources disagree only at the edges and never on the failure
# this guards (`administration` is in NONE of them):
#   docs.github.com/actions/reference/workflows-and-actions/workflow-syntax#permissions
#     -> has artifact-metadata / code-quality / vulnerability-alerts, omits models, repository-projects
#   docs.github.com/actions/how-tos/security-for-github-actions/security-guides/use-github_token-in-workflows
#     -> has models
#   schemastore.org/github-workflow.json (what actionlint and editors validate against)
#     -> has repository-projects (classic projects: dropped from the docs, still parsed)
# All three checked 2026-08-01.
_GITHUB_TOKEN_PERMISSION_KEYS = frozenset({
    "actions", "artifact-metadata", "attestations", "checks", "code-quality", "contents",
    "deployments", "discussions", "id-token", "issues", "models", "packages", "pages",
    "pull-requests", "repository-projects", "security-events", "statuses", "vulnerability-alerts",
})


def _required_top(reg: dict) -> list[dict]:
    return [c for c in reg["controls"] if c.get("classification") == "required"]


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
    for ctx in reg.get("required_contexts") or []:
        if ctx not in names:
            out.append(Finding("DC-1", "-",
                f"required_contexts entry {ctx!r} matches no workflow job name", True))
    return out


def dc2_registry_jobs_bijection(reg: dict, jobs: list[dict]) -> list[Finding]:
    """(1) phantom control: a workflow-backed control whose (workflow, job) is not a real job;
    (2) unknown job: a workflow job with no registry control mapping to it."""
    out: list[Finding] = []
    real = {(j["workflow"], j["job_id"]) for j in jobs}
    mapped: set[tuple] = set()
    for c in reg["controls"]:
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
    """Registry `required_contexts` vs live GitHub required contexts.

    A live-probe failure is an explicit non-authoritative SKIP, never a pass — the caller decides
    whether that is tolerable (local) or a hard failure (--require-live, the authenticated job)."""
    if live_error is not None:
        return [Finding("DC-3", "-",
            f"NON-AUTHORITATIVE: live protection unreadable ({live_error}) — deployed-state not verified",
            blocking=False, skipped=True)]
    declared = set(reg.get("required_contexts") or [])
    live = set(live_contexts or [])
    if live == declared:
        return []
    return [Finding("DC-3", "-",
        f"live required != declared — missing={sorted(declared - live)} unexpected={sorted(live - declared)}", True)]


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



def dc6_workflow_hygiene(reg: dict, jobs: list[dict]) -> list[Finding]:
    """Every job has a timeout, every action is SHA-pinned, and permission keys are valid."""
    out: list[Finding] = []
    ctx_to_id = {c.get("branch_protection_context"): c.get("id") for c in reg["controls"]}
    checked_workflows: set[str] = set()
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
        scopes = [(f"job {j['job_id']} in {j['workflow']}", j.get("job_permission_keys", []))]
        if j["workflow"] not in checked_workflows:
            checked_workflows.add(j["workflow"])
            scopes.append((f"workflow {j['workflow']}", j.get("workflow_permission_keys", [])))
        for scope, keys in scopes:
            for key in keys:
                if key not in _GITHUB_TOKEN_PERMISSION_KEYS:
                    out.append(Finding("DC-6", cid,
                        f"{scope} declares unknown GITHUB_TOKEN permission {key!r} — GitHub rejects "
                        f"the workflow before creating jobs", True))
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
              for c in reg["controls"] if c.get("workflow") and c.get("job")}
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


def dc8_declared_workflow_disabled(reg: dict, live_states, live_error: str | None = None) -> list[Finding]:
    """A workflow this registry declares, held DISABLED in GitHub — declared CI that cannot run.

    The declaration needs no new field: DC-2 already enforces a bijection between registry controls
    and real workflow jobs, so `{c["workflow"]}` IS the set of workflows this repo commits to
    running. Every control inside a disabled workflow is inert while its file still reads as live
    governance — the exact shape of a rule that looks enforced and is not, and the one the static
    plane structurally cannot see because it never leaves the tree.

    Three verdicts, deliberately distinct:
      state != active     BLOCKING — the workflow exists and was switched off.
      absent from live    INFO      — GitHub holds no record (a workflow added on this branch and
                                      never merged to the default branch reads exactly like this).
                                      Conflating it with 'disabled' would redden every PR that adds
                                      a workflow.
      state == active     clean.

    NOT reported: a live workflow the registry does not declare. GitHub keeps a record of every
    workflow file ever pushed, forever — this repo carries three such ghosts from deleted files —
    so that direction is stale remote history, not repository drift. DC-2 already owns the real
    question (a tracked job with no control)."""
    if live_error is not None:
        return [Finding("DC-8", "-",
            f"NON-AUTHORITATIVE: live workflow states unreadable ({live_error}) — enablement not verified",
            blocking=False, skipped=True)]
    states = live_states or {}
    out: list[Finding] = []
    for path in sorted({c["workflow"] for c in reg["controls"] if c.get("workflow")}):
        ids = sorted(c["id"] for c in reg["controls"] if c.get("workflow") == path)
        if path not in states:
            out.append(Finding("DC-8", ",".join(ids),
                f"declared workflow {path} has NO live GitHub record — not yet on the default "
                f"branch, or deleted remotely. Not read as disabled.", blocking=False))
            continue
        state = states[path]
        if state == "active":
            continue
        out.append(Finding("DC-8", ",".join(ids),
            f"declared workflow {path} is {state!r} in GitHub ({_WORKFLOW_STATES.get(state, 'not active')}) "
            f"— controls {ids} declare it runs, and it cannot. Re-enable with "
            f"`gh workflow enable {path}`, or delete the rows that declare it.", True))
    return out


def dc9_repo_security_settings(reg: dict, live_settings, live_error: str | None = None) -> list[Finding]:
    """A repository security setting this registry declares required, disabled in live GitHub.

    Same class as DC-8 and invisible for the same reason — the state lives only in GitHub settings,
    so no amount of reading the tree finds it. `required_security_settings` is the declaration.

    A setting the live reading does not carry at all is BLOCKING, not skipped: the probe already
    refuses a partial read (live.probe_security), so a key missing from a reading it certified
    complete means GitHub does not offer that setting on this repo — a declaration naming something
    unenforceable, which is the decoration this whole module exists to catch."""
    if live_error is not None:
        return [Finding("DC-9", "-",
            f"NON-AUTHORITATIVE: live security settings unreadable ({live_error}) — not verified",
            blocking=False, skipped=True)]
    settings = live_settings or {}
    out: list[Finding] = []
    for name in reg.get("required_security_settings") or []:
        status = settings.get(name)
        if status == "enabled":
            continue
        if status is None:
            out.append(Finding("DC-9", name,
                f"declared security setting {name!r} is absent from the live reading — GitHub does "
                f"not report it for this repository; the declaration names nothing enforceable", True))
            continue
        out.append(Finding("DC-9", name,
            f"declared security setting {name!r} is {status!r} in GitHub — declared required and "
            f"switched off. Enable it: {_SECURITY_REMEDIATION.get(name, 'repository Settings -> Code security')}", True))
    return out


def run_static(reg: dict, jobs: list[dict], prose_docs) -> list[Finding]:
    """Static plane: registry <-> workflow implementation (no network). DC-1/2/4/6/7."""
    return (dc1_renamed_required_context(reg, jobs)
            + dc2_registry_jobs_bijection(reg, jobs)
            + dc4_prose_matches_classification(reg, prose_docs)
            + dc6_workflow_hygiene(reg, jobs)
            + dc7_advisory_must_not_hard_fail(reg, jobs))


def run_deployed(reg: dict, live_contexts, live_error: str | None = None,
                 workflow_states=None, workflow_error: str | None = None,
                 security_settings=None, security_error: str | None = None) -> list[Finding]:
    """Deployed-state plane: registry <-> live GitHub. DC-3 (protection), DC-8 (workflow
    enablement), DC-9 (repository security settings). Each probe carries its OWN error so one
    unreadable plane never masks another as clean."""
    return (dc3_deployed_state(reg, live_contexts, live_error)
            + dc8_declared_workflow_disabled(reg, workflow_states, workflow_error)
            + dc9_repo_security_settings(reg, security_settings, security_error))
