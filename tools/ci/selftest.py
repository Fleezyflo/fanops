"""Negative controls — inject exactly one defect, assert the named DC fires with evidence that was
ABSENT before. ONE implementation, invoked by the CLI `selftest` verb AND
tests/test_ci_registry_validator.py, so the two can never disagree (the lesson tools/arch learned:
a copied check drifts). READ THE ASSERTION, NOT THE NAME.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from . import checks
from .registry import load_registry
from .workflows import discover_jobs


@dataclass(frozen=True)
class Control:
    id: str
    expect_dc: str
    defect: str


CONTROLS = [
    Control("NC-DC1", "DC-1", "rename a required job so its context no longer matches"),
    Control("NC-DC2-phantom", "DC-2", "add a control pointing at a non-existent job"),
    Control("NC-DC2-unknown", "DC-2", "drop a control so its job becomes unregistered"),
    Control("NC-DC3-drift", "DC-3", "live required set diverges from declared current"),
    Control("NC-DC4-prose", "DC-4", "a doc calls a required context advisory"),
    Control("NC-DC5-dup", "DC-5", "a duplicate_group names an unknown member"),
    Control("NC-DC6-timeout", "DC-6", "a job loses its timeout"),
    Control("NC-DC6-float", "DC-6", "a job uses a floating action tag"),
]


def _blocking(findings, dc=None):
    return {f.render() for f in findings
            if f.blocking and not f.skipped and (dc is None or f.dc == dc)}


def _first_required_ctx(reg):
    return next(c["branch_protection_context"] for c in reg["controls"]
               if c.get("classification") == "required" and not c.get("parent"))


def detect(ctrl: Control):
    """Return (fired, detail): fired == the named DC produced a NEW blocking finding vs baseline."""
    reg, jobs = load_registry(), discover_jobs()

    if ctrl.id == "NC-DC1":
        before = checks.dc1_renamed_required_context(reg, jobs)
        req = _first_required_ctx(reg)
        j2 = copy.deepcopy(jobs)
        for j in j2:
            if j["name"] == req:
                j["name"] = req + " RENAMED"
        new = _blocking(checks.dc1_renamed_required_context(reg, j2), "DC-1") - _blocking(before, "DC-1")
        return bool(new), "; ".join(sorted(new)) or "no new DC-1 finding"

    if ctrl.id == "NC-DC2-phantom":
        before = checks.dc2_registry_jobs_bijection(reg, jobs)
        r2 = copy.deepcopy(reg)
        r2["controls"].append({
            "id": "PHANTOM", "name": "ghost", "invariant": "phantom-only", "owner": "ci-lane",
            "classification": "advisory", "trigger": ["pull_request"], "justification": "x",
            "deletion_consequence": "x", "adr": ["ADR-0100"], "failure_evidence": "x",
            "status": "active", "workflow": ".github/workflows/ci.yml", "job": "no-such-job"})
        new = _blocking(checks.dc2_registry_jobs_bijection(r2, jobs), "DC-2") - _blocking(before, "DC-2")
        return bool(new), "; ".join(sorted(new)) or "no new DC-2 finding"

    if ctrl.id == "NC-DC2-unknown":
        before = checks.dc2_registry_jobs_bijection(reg, jobs)
        r2 = copy.deepcopy(reg)
        victim = next(i for i, c in enumerate(r2["controls"])
                      if c.get("workflow") and c.get("job") and not c.get("parent"))
        del r2["controls"][victim]
        new = _blocking(checks.dc2_registry_jobs_bijection(r2, jobs), "DC-2") - _blocking(before, "DC-2")
        return bool(new), "; ".join(sorted(new)) or "no new DC-2 finding"

    if ctrl.id == "NC-DC3-drift":
        current = list(reg.get("current_required_contexts", []))
        before = checks.dc3_deployed_state(reg, current)
        new = _blocking(checks.dc3_deployed_state(reg, ["only-one-context"])) - _blocking(before)
        return bool(new), "; ".join(sorted(new)) or "no new DC-3 blocking finding"

    if ctrl.id == "NC-DC4-prose":
        req = _first_required_ctx(reg)
        with TemporaryDirectory() as d:
            doc = Path(d) / "AGENTS.md"
            doc.write_text(f"The `{req}` check is advisory and never blocks.\n", encoding="utf-8")
            before = checks.dc4_prose_matches_classification(reg, [])
            after = checks.dc4_prose_matches_classification(reg, [doc])
        new = _blocking(after) - _blocking(before)
        return bool(new), "; ".join(sorted(new)) or "no new DC-4 finding"

    if ctrl.id == "NC-DC5-dup":
        before = checks.dc5_duplicate_ownership(reg)
        r2 = copy.deepcopy(reg)
        r2.setdefault("duplicate_groups", {})["bogus"] = {
            "reason": "injected", "members": ["CI-UNIT", "DOES-NOT-EXIST"],
            "distinct_boundaries": {"CI-UNIT": "x", "DOES-NOT-EXIST": "x"}}
        new = _blocking(checks.dc5_duplicate_ownership(r2)) - _blocking(before)
        return bool(new), "; ".join(sorted(new)) or "no new DC-5 finding"

    if ctrl.id == "NC-DC6-timeout":
        j2 = copy.deepcopy(jobs)
        for j in j2:                       # clean baseline: every job has a timeout
            j["timeout"] = j["timeout"] or 10
        base = _blocking(checks.dc6_workflow_hygiene(reg, j2), "DC-6")
        j2[0]["timeout"] = None            # inject ONE missing timeout
        new = _blocking(checks.dc6_workflow_hygiene(reg, j2), "DC-6") - base
        return bool(new), "; ".join(sorted(new)) or "no new DC-6 timeout finding"

    if ctrl.id == "NC-DC6-float":
        j2 = copy.deepcopy(jobs)
        for j in j2:                       # clean baseline: pin every action to a fake 40-hex SHA
            j["uses"] = [u.split("@")[0] + "@" + ("a" * 40) for u in j["uses"]]
        base = _blocking(checks.dc6_workflow_hygiene(reg, j2), "DC-6")
        for j in j2:                       # inject ONE floating ref
            if j["uses"]:
                j["uses"][0] = j["uses"][0].split("@")[0] + "@v7"
                break
        new = _blocking(checks.dc6_workflow_hygiene(reg, j2), "DC-6") - base
        return bool(new), "; ".join(sorted(new)) or "no new DC-6 float finding"

    return False, f"unknown control {ctrl.id}"
