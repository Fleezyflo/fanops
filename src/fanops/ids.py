# src/fanops/ids.py
"""Deterministic, collision-resistant, CONTENT-ADDRESSED ids so re-running any stage is
idempotent ACROSS PROCESSES. We never use Python's builtin hash() — it is salted per
interpreter (PEP 456) and would make ids differ every run, causing duplicate posts."""
import hashlib

def _hash(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()[:12]

def make_id(kind: str, source: str) -> str:
    """Top-level id from a stable source string (e.g. a sha256 digest or a path)."""
    return f"{kind}_{_hash(kind, source)}"

def child_id(kind: str, parent_id: str, content_token: str) -> str:
    """Child id from parent + a STABLE content token (e.g. '14.00-21.00' for a moment,
    or a surface_key for a post). Never pass a positional index or a hash()."""
    return f"{kind}_{_hash(kind, parent_id, content_token)}"

def content_id(kind: str, parent_id: str, content_token: str) -> str:
    """Alias used where the 'content-addressed' intent should be explicit at the call site."""
    return child_id(kind, parent_id, content_token)

def surface_key(account: str, platform: str) -> str:
    """The canonical, stable key for an (account, platform) posting surface. Used as the
    content token for post ids AND as the per-surface schedule seed."""
    return f"{account}|{platform}"
