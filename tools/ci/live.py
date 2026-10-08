"""The explicit, read-only GitHub probes (the DEPLOYED-STATE plane).

Invoked on purpose by the deployed-state mode — never implicitly. Any failure returns an error
string, never a silent empty pass: the caller decides whether that is an explicit non-authoritative
SKIP (local/PR) or a hard FAIL (the designated authenticated job, via --require-live).

Every call here is a GET. Nothing in this module mutates repository state, and nothing should ever
be added that does — a reconciler that can change what it measures is not a reconciler.
"""
from __future__ import annotations

import json
import os
import subprocess

from .common import DEFAULT_BRANCH, DEFAULT_REPO


def _gh_json(path: str, timeout: int, *, token: str | None = None):
    """GET one gh API path. Returns (parsed, error); error is None on success, a message otherwise.

    `token`, when set, is the subprocess GH_TOKEN and does not replace the parent environment.
    The protection GET needs a credential GITHUB_TOKEN cannot hold."""
    env = None
    if token:
        env = dict(os.environ)
        env["GH_TOKEN"] = token
    try:
        r = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=timeout,
                           env=env)
    except FileNotFoundError:
        return None, "gh CLI not found"
    except subprocess.TimeoutExpired:
        return None, f"gh api timed out after {timeout}s"
    if r.returncode != 0:
        return None, (r.stderr.strip() or f"gh api exit {r.returncode}")
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError as e:
        return None, f"unparseable JSON from {path}: {e}"


def probe_protection(repo: str = DEFAULT_REPO, branch: str = DEFAULT_BRANCH, timeout: int = 30):
    """Returns (data, error). error is None on success; a message on any failure.

    GET /repos/{repo}/branches/{branch}/protection. GITHUB_TOKEN is not granted this read
    (HTTP 403). The credential is PROTECTION_READ_TOKEN. Unset is an error, not a skip."""
    token = (os.environ.get("PROTECTION_READ_TOKEN") or "").strip()
    if not token:
        return None, "PROTECTION_READ_TOKEN is unset — GITHUB_TOKEN cannot GET branch protection"
    return _gh_json(f"repos/{repo}/branches/{branch}/protection", timeout, token=token)


def required_contexts(data: dict) -> list[str]:
    return list((data.get("required_status_checks") or {}).get("contexts") or [])


def probe_workflows(repo: str = DEFAULT_REPO, timeout: int = 30):
    """Live Actions workflow states -> ({path: state}, error).

    Needs only `actions: read`, which IS a grantable GITHUB_TOKEN scope — so unlike the protection
    and security probes this one is authoritative inside the scheduled job, not just on an operator
    terminal.

    A truncated page is an ERROR, never a short dict. Under-reporting would turn a DISABLED
    workflow into an ABSENT one, and absent is the non-blocking verdict — the single way this probe
    could manufacture a pass.
    """
    data, err = _gh_json(f"repos/{repo}/actions/workflows?per_page=100", timeout)
    if err is not None:
        return None, err
    rows = data.get("workflows") or []
    total = data.get("total_count")
    if isinstance(total, int) and total > len(rows):
        return None, f"workflow list truncated ({len(rows)} of {total}) — refusing a partial read"
    return {w.get("path"): w.get("state") for w in rows if w.get("path")}, None


def probe_security(repo: str = DEFAULT_REPO, timeout: int = 30):
    """Repository security settings -> ({setting: status}, error).

    `security_and_analysis` is returned by the repo object ONLY to a caller with admin permission.
    A workflow GITHUB_TOKEN never has it and no `permissions:` block can grant it (same wall DC-3
    hits on branch protection), so in CI this probe reports an explicit SKIP until the operator
    supplies an admin-scoped PAT. It is authoritative on an operator terminal today. The absence of
    the key is treated as an error, not an empty dict: a missing field must never read as "nothing
    is enabled" — that would be a fabricated finding — nor as a pass.
    """
    data, err = _gh_json(f"repos/{repo}", timeout)
    if err is not None:
        return None, err
    block = data.get("security_and_analysis")
    if not isinstance(block, dict):
        return None, "repo object carries no `security_and_analysis` (needs admin permission)"
    return {k: (v or {}).get("status") for k, v in block.items() if isinstance(v, dict)}, None
