"""Discover workflow jobs from .github/workflows/*.yml — the IMPLEMENTATION plane.

Each job's `name:` is its status-check context string. Workflow- and job-level permission keys are
also normalized for DC-6. (The PyYAML `on: -> True` boolean-key gotcha is irrelevant here.)
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .common import WORKFLOWS


def _permission_keys(value) -> list:
    return list(value) if isinstance(value, dict) else []


def discover_jobs(workflows: Path = WORKFLOWS) -> list[dict]:
    jobs: list[dict] = []
    for wf in sorted(workflows.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        workflow_permission_keys = _permission_keys(doc.get("permissions"))
        for job_id, job in (doc.get("jobs") or {}).items():
            job = job or {}
            steps = job.get("steps") or []
            uses = [s["uses"] for s in steps if isinstance(s, dict) and s.get("uses")]
            jobs.append({
                "workflow": wf.name,
                "job_id": job_id,
                "name": job.get("name", job_id),
                "timeout": job.get("timeout-minutes"),
                "uses": uses,
                "workflow_permission_keys": workflow_permission_keys,
                "job_permission_keys": _permission_keys(job.get("permissions")),
                # DC-7 reads this. A job that can fail the workflow while nothing can block on it
                # emits red no one is able to act on. Step-level continue-on-error does NOT count:
                # it lets the job keep going, but the JOB still reports failure to the check API.
                "continue_on_error": bool(job.get("continue-on-error", False)),
            })
    return jobs
