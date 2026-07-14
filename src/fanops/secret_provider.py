# src/fanops/secret_provider.py — keyring read/write for operator secrets (MOL-359 read, MOL-360 write)
"""Consult the OS keyring FIRST for operator secrets; fail-open to Settings/.env on READ when the
keyring extra is absent, the backend is unavailable, or no entry exists. WRITES for the three
secret env keys route ONLY to keyring (never plaintext .env) via golive._dual_write."""
from __future__ import annotations
import logging

_log = logging.getLogger("fanops.secret_provider")
_backend_warned = False   # broken-keyring breadcrumb fires ONCE per process (fail-open house norm)
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
    except ImportError:
        # The [keyring] extra is not installed. This is the DOCUMENTED optional-extra state
        # (pyproject `keyring` extra), NOT a fault — reads fall back to Settings/.env by design.
        # Warning here would spam a WARNING on every secret-property read of a healthy env-only
        # install, so stay silent. A present-but-broken backend is a different case (below).
        return None
    except Exception as exc:
        # keyring IS installed but the backend failed (no Secret Service, locked Keychain, etc.).
        # This CAN mask a secret the operator wrote to keyring, so it is worth a breadcrumb — but
        # get_secret runs on EVERY secret-property read, so warn ONCE per process, not per read (a
        # per-read warning historically flooded studio.err with 64k identical lines). The backend
        # being down is not key-specific, so one global signal suffices.
        global _backend_warned
        if not quiet and not _backend_warned:
            _backend_warned = True
            _log.warning("keyring read unavailable for %s (fail-open to env; further keyring errors "
                         "this process are suppressed): %s", env_key, exc)
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
    """Persist `value` to the OS keyring for `env_key`, then VERIFY it reads back. Raises ValueError
    on newline-bearing values (mirrors autopilot.set_env_var); raises OSError when the backend is
    unavailable OR the just-written value does not round-trip. The read-back is load-bearing: the
    caller (golive._dual_write) SCRUBS the plaintext .env fallback on a successful write, so a write
    that the backend accepts-but-drops would otherwise erase the secret from BOTH stores. 'Written'
    must mean 'retrievable', so a failed verify keeps the fallback intact (caller sees the OSError)."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"set_secret: value for {env_key!r} contains a newline — rejected")
    try:
        _keyring_set_password(_SERVICE, env_key, value)
    except Exception as exc:
        raise OSError(f"could not write {env_key} to keyring: {exc}") from exc
    try:
        readback = _keyring_get_password(_SERVICE, env_key)
    except Exception as exc:
        raise OSError(f"wrote {env_key} to keyring but could not verify it read back: {exc}") from exc
    if readback is None or readback.strip() != value.strip():
        raise OSError(f"wrote {env_key} to keyring but the value did not round-trip (backend dropped it)")


def delete_secret(env_key: str) -> None:
    """Remove `env_key` from the OS keyring. No-op when absent or keyring unavailable."""
    try:
        _keyring_delete_password(_SERVICE, env_key)
    except Exception as exc:
        _log.warning("keyring delete unavailable for %s (ignored): %s", env_key, exc)
