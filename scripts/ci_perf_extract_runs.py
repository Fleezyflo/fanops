#!/usr/bin/env python3
"""Extract CI step wall times and pytest summary lines from GitHub Actions runs."""
from __future__ import annotations
import json
import re
import subprocess
import sys
from datetime import datetime


def step_secs(s: dict) -> float:
    t0 = datetime.fromisoformat(s["startedAt"].replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(s["completedAt"].replace("Z", "+00:00"))
    return (t1 - t0).total_seconds()


def job_wall(job: dict) -> float:
    t0 = datetime.fromisoformat(job["startedAt"].replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(job["completedAt"].replace("Z", "+00:00"))
    return (t1 - t0).total_seconds()


def fetch_run(run_id: str) -> dict:
    out = subprocess.check_output(["gh", "run", "view", run_id, "--json", "headSha,jobs,conclusion,createdAt"], text=True)
    return json.loads(out)


def fetch_pytest_summaries(run_id: str) -> list[str]:
    log = subprocess.check_output(["gh", "run", "view", run_id, "--log"], text=True, stderr=subprocess.DEVNULL)
    return re.findall(r"\d+ passed.*(?:deselected|skipped).*(?:\n|$)", log)


def main():
    runs = sys.argv[1:] if len(sys.argv) > 1 else ["29060145209", "29088497078", "29090531793"]
    for rid in runs:
        d = fetch_run(rid)
        print(f"=== run {rid} sha={d['headSha'][:8]} conclusion={d['conclusion']} ===")
        for job in d["jobs"]:
            print(f"JOB {job['name']}: wall={job_wall(job):.0f}s")
            for s in job["steps"]:
                if s["conclusion"] in ("success", "failure") and not s["name"].startswith("Post ") and s["name"] not in ("Set up job", "Complete job"):
                    dt = step_secs(s)
                    if dt >= 0.5 or s["conclusion"] == "failure":
                        print(f"  {dt:6.1f}s  [{s['conclusion']}] {s['name']}")
        for line in fetch_pytest_summaries(rid):
            print(f"  PYTEST: {line.strip()}")
        print()


if __name__ == "__main__":
    main()
