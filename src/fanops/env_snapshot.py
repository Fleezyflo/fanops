# src/fanops/env_snapshot.py — MOL-292: frozen env snapshot (single parse per Config)
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from fanops.settings import Settings


@dataclass(frozen=True, slots=True)
class EnvSnapshot:
    _s: Settings
    _resolved_secrets: dict[str, str | None]

    def secret(self, key: str) -> str | None:
        return self._resolved_secrets.get(key)


def load_env_snapshot(root: Path) -> EnvSnapshot:
    s, secrets = Settings.runtime_load(root)
    return EnvSnapshot(_s=s, _resolved_secrets=secrets)
