# src/fanops/secret_provider.py — keyring read/write for operator secrets (MOL-359 read, MOL-360 write)
"""Consult the OS keyring FIRST for operator secrets; fail-open to Settings/.env on READ when the
keyring extra is absent, the backend is unavailable, or no entry exists. WRITES for the three
secret env keys route ONLY to keyring (never plaintext .env) via golive._dual_write."""
from __future__ import annotations
import logging

_log = logging.getLogger("fanops.secret_provider")
_SERVICE = "fanops"
_SECRET_KEYS = frozenset({"POSTIZ_API_KEY", "ZERNIO_API_KEY", "META_GRAPH_TOKEN"})
_PER_HANDLE_TOKEN_PREFIX = "META_GRAPH_TOKEN__"


def is_secret_env_key(key: str) -> bool:
    """True for the three operator-secret env keys (+ per-handle META_GRAPH_TOKEN__<slug>)."""
    return key in _SECRET_KEYS or key.startswith(_PER_HANDLE_TOKEN_PREFIX)


def _keyring_get_password(service: str, username: str) -> str | None:
    import keyring  # noqa: PLC0415 — lazy; optional [keyring] extra
    return keyring.get_password(service, username)


def _keyring_set_password(service: str, username: str, password: str) -> None:
    import keyring  # noqa: PLC0415 — lazy; optional [keyring] extra
    keyring.set_password(service, username, password)


def _keyring_delete_password(service: str, username: str) -> None:
    import keyring  # noqa: PLC0415 — lazy; optional [keyring] extra
    try:
        keyring.delete_password(service, username)
    except Exception:
        pass  # absent entry is fine


def get_secret(env_key: str, *, quiet: bool = False) -> str | None:
    """Return the trimmed keyring value for `env_key`, or None when absent/unavailable."""
    try:
        raw = _keyring_get_password(_SERVICE, env_key)
    except Exception as exc:
        if not quiet:
            _log.warning("keyring read unavailable for %s (fail-open to env): %s", env_key, exc)
        return None
    if raw is None:
        return None
    s = raw.strip()
    return s or None


def resolve_secret(env_key: str, fallback: str | None, *, quiet: bool = False) -> str | None:
    """Keyring wins when set; else return `fallback` unchanged (Settings / os.environ)."""
    kr = get_secret(env_key, quiet=quiet)
    return kr if kr is not None else fallback


def set_secret(env_key: str, value: str) -> None:
    """Persist `value` to the OS keyring for `env_key`. Raises ValueError on newline-bearing values
    (mirrors autopilot.set_env_var) or when the keyring backend is unavailable."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"set_secret: value for {env_key!r} contains a newline — rejected")
    try:
        _keyring_set_password(_SERVICE, env_key, value)
    except Exception as exc:
        raise OSError(f"could not write {env_key} to keyring: {exc}") from exc


def delete_secret(env_key: str) -> None:
    """Remove `env_key` from the OS keyring. No-op when absent or keyring unavailable."""
    try:
        _keyring_delete_password(_SERVICE, env_key)
    except Exception as exc:
        _log.warning("keyring delete unavailable for %s (ignored): %s", env_key, exc)
