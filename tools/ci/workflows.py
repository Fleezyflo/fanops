"""Discover workflow jobs from .github/workflows/*.yml — the IMPLEMENTATION plane.

Each job's `name:` is its status-check context string. (Only `jobs:` is read, so the PyYAML
`on: -> True` boolean-key gotcha is irrelevant here.)
"""
from __future__ import annotations

import yaml

from .common import WORKFLOWS


def discover_jobs() -> list[dict]:
    jobs: list[dict] = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        wf_concurrency = "concurrency" in doc
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
                "concurrency": ("concurrency" in job) or wf_concurrency,
            })
    return jobs


def job_names(jobs: list[dict]) -> set[str]:
    return {j["name"] for j in jobs}
