# tests/keyring_fake.py — in-memory keyring for hermetic secret write/read tests (MOL-360)
from __future__ import annotations
import importlib
import sys

from fanops import secret_provider
from fanops.studio import golive


class MemKeyring:
    _store: dict[tuple[str, str], str] = {}

    @classmethod
    def reset(cls):
        cls._store.clear()

    @staticmethod
    def get_password(service, username):
        return MemKeyring._store.get((service, username))

    @staticmethod
    def set_password(service, username, password):
        MemKeyring._store[(service, username)] = password

    @staticmethod
    def delete_password(service, username):
        MemKeyring._store.pop((service, username), None)


def install_mem_keyring(monkeypatch):
    """Reload secret_provider with an in-memory keyring; wire golive to the reloaded module."""
    MemKeyring.reset()
    monkeypatch.setitem(sys.modules, "keyring", MemKeyring)
    sp = importlib.reload(secret_provider)
    monkeypatch.setattr(golive, "secret_provider", sp, raising=False)
    return sp
