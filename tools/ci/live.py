"""The explicit, read-only branch-protection probe (the DEPLOYED-STATE plane).

Invoked on purpose by the deployed-state mode — never implicitly. Any failure returns an error
string, never a silent empty pass: the caller decides whether that is an explicit non-authoritative
SKIP (local/PR) or a hard FAIL (the designated authenticated job, via --require-live).
"""
from __future__ import annotations

import json
import subprocess

from .common import DEFAULT_BRANCH, DEFAULT_REPO


def probe_protection(repo: str = DEFAULT_REPO, branch: str = DEFAULT_BRANCH, timeout: int = 30):
    """Returns (data, error). error is None on success; a message on any failure."""
    try:
        r = subprocess.run(["gh", "api", f"repos/{repo}/branches/{branch}/protection"],
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "gh CLI not found"
    except subprocess.TimeoutExpired:
        return None, f"gh api timed out after {timeout}s"
    if r.returncode != 0:
        return None, (r.stderr.strip() or f"gh api exit {r.returncode}")
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError as e:
        return None, f"unparseable protection JSON: {e}"


def required_contexts(data: dict) -> list[str]:
    return list((data.get("required_status_checks") or {}).get("contexts") or [])
