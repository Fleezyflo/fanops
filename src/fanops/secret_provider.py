# src/fanops/secret_provider.py — optional keyring reads for Config secrets (MOL-359, READ-only)
"""Consult the OS keyring FIRST for operator secrets; fail-open to Settings/.env when the keyring
extra is absent, the backend is unavailable, or no entry exists. Nothing writes here yet."""
from __future__ import annotations
import logging

_log = logging.getLogger("fanops.secret_provider")
_SERVICE = "fanops"


def _keyring_get_password(service: str, username: str) -> str | None:
    import keyring  # noqa: PLC0415 — lazy; optional [keyring] extra
    return keyring.get_password(service, username)


def get_secret(env_key: str) -> str | None:
    """Return the trimmed keyring value for `env_key`, or None when absent/unavailable."""
    try:
        raw = _keyring_get_password(_SERVICE, env_key)
    except Exception as exc:
        _log.warning("keyring read unavailable for %s (fail-open to env): %s", env_key, exc)
        return None
    if raw is None:
        return None
    s = raw.strip()
    return s or None


def resolve_secret(env_key: str, fallback: str | None) -> str | None:
    """Keyring wins when set; else return `fallback` unchanged (Settings / os.environ)."""
    kr = get_secret(env_key)
    return kr if kr is not None else fallback
