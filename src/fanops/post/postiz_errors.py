"""Postiz failure detail — the guarded read behind reconcile's `errorMessage` enrichment.

The Postiz PUBLIC API hides a post's failure entirely: an ERROR row of GET /public/v1/posts carries
only `state` (probed live 2026-08-10 — no error/errorMessage key), and GET /public/v1/integrations
hides `refreshNeeded` the same way. The only headless source of the real cause is the self-hosted
stack's own Postgres: `"Post".error` holds the Temporal failure envelope with the Graph error body
inside. This module is that read — ONE `docker exec … psql` SELECT per reconcile pass, batched over
the failed submission ids — plus the parser that turns the stored text into (ErrorKind, short reason).

Fail-open BY DESIGN, never silent: no docker binary, a remote (non-self-host) stack, a renamed
container, a timeout, a nonzero exit, or an unparseable row each degrade to {} — reconcile then
stamps exactly what it stamped before this module existed ("no detail") and logs the shortfall
(its `error_detail` event carries failed=N vs detailed=M). Never raises. Read-only: one SELECT.

Container/user/db names are the official Postiz self-host compose defaults (match the live stack,
verified 2026-08-10). A renamed deployment degrades to {} rather than growing config surface.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess

from fanops.models import ErrorKind

_PG_CONTAINER = "postiz-postgres"
_PG_USER = "postiz-user"
_PG_DB = "postiz-db-local"
_TIMEOUT_S = 10

# Postiz post ids are cuids; an id that fails this shape never reaches the SQL string.
_SID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")

# launchd strips the brew/docker dirs from PATH (daemon context), so `which` alone is not enough.
_DOCKER_FALLBACKS = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
)


def _docker_bin() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for path in _DOCKER_FALLBACKS:
        if os.path.exists(path):
            return path
    return None


def fetch_error_details(sids) -> dict[str, str]:
    """{submission_id: raw stored error text} for the ids Postgres knows — {} on any guard rail."""
    ids = sorted({s for s in (sids or []) if isinstance(s, str) and _SID_RE.fullmatch(s)})
    if not ids:
        return {}
    docker = _docker_bin()
    if docker is None:
        return {}
    id_array = ",".join(f"'{s}'" for s in ids)
    # base64 per row (newline-stripped): the stored envelope is multi-line JSON, so the only safe
    # line protocol is <id>|<base64(error)>.
    sql = (
        "SELECT id || '|' || translate(encode(convert_to(coalesce(error,''),'UTF8'),'base64'), chr(10), '') "
        f'FROM "Post" WHERE id = ANY(ARRAY[{id_array}]::text[])'
    )
    try:
        proc = subprocess.run(
            [docker, "exec", _PG_CONTAINER, "psql", "-U", _PG_USER, "-d", _PG_DB, "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if proc.returncode != 0:
        return {}
    wanted = set(ids)
    out: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        sid, sep, blob = line.strip().partition("|")
        if not sep or sid not in wanted:
            continue
        try:
            text = base64.b64decode(blob).decode("utf-8", "replace")
        except ValueError:
            continue
        if text.strip():
            out[sid] = text
    return out


_MEDIA_FETCH_RE = re.compile(r"video download failed with:\s*http error code\s*(\d+)", re.I)
_SUBCODE_2207077_RE = re.compile(r"error_subcode\W{0,4}2207077")
# The envelope nests escaped JSON, so both `"key":"…"` and `\"key\":\"…\"` shapes occur.
_USER_MSG_RE = re.compile(r'error_user_msg\\{0,2}"\s*:\s*\\{0,2}"((?:[^"\\]|\\.){1,300})')
_INNER_MSG_RE = re.compile(r'message\\{0,2}"\s*:\s*\\{0,2}"((?:[^"\\]|\\.){1,300})')
_ENVELOPE_NOISE = {"fatal", "activity task failed"}   # Temporal wrapper strings, not causes


def _clean_snippet(s: str) -> str:
    if "\\u" in s:
        try:
            decoded = s.encode("utf-8").decode("unicode_escape")
        except ValueError:
            decoded = s          # undecodable escapes: keep the escaped form visible, drop nothing
        s = decoded
    return re.sub(r"\s+", " ", s).strip()[:100]


def classify_error_text(text) -> tuple[ErrorKind, str]:
    """(kind, short reason) from a stored Postiz error. ('' reason ⇒ caller keeps its 'no detail')."""
    raw = (text or "").strip()
    if not raw:
        return (ErrorKind.unknown, "")
    flat = raw.replace('\\"', '"')
    low = flat.lower()
    if "refresh channel needed" in low:
        # Workflow-gate refusal: the channel credential is dead (Integration.refreshNeeded). A retry
        # re-fails until the operator reconnects the account, hence ErrorKind.auth (not retryable).
        return (ErrorKind.auth, "refresh channel needed - reconnect the account in Postiz")
    m = _MEDIA_FETCH_RE.search(low)
    if m:
        return (ErrorKind.transient, f"IG could not download the media (HTTP {m.group(1)} from the media host)")
    if _SUBCODE_2207077_RE.search(low):
        return (ErrorKind.transient, "IG could not download the media (media host unreachable)")
    for rx in (_USER_MSG_RE, _INNER_MSG_RE):
        m = rx.search(flat)
        if m:
            snippet = _clean_snippet(m.group(1))
            if snippet and snippet.lower() not in _ENVELOPE_NOISE:
                return (ErrorKind.unknown, snippet)
    return (ErrorKind.unknown, "")
