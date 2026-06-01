"""Flat active-account registry — non-secret metadata only (the Blotato account_id is a
non-secret identifier; the API key lives in .env). No lanes: every active account
participates. surfaces() yields each (handle, account_id, platform). resolve_account_id()
maps a handle to its numeric Blotato id (FIX F06: v1 passed the handle straight to Blotato)."""
from __future__ import annotations
import json
from enum import Enum
from typing import Optional, NamedTuple
from pydantic import BaseModel, Field
from fanops.config import Config
from fanops.errors import ControlFileError, reason as _reason
from fanops.models import Platform

class AccountStatus(str, Enum):
    planned = "planned"; warming = "warming"; active = "active"; retired = "retired"

class Account(BaseModel):
    handle: str
    account_id: str = ""                   # Blotato NUMERIC id; required when active
    platforms: list[Platform] = Field(default_factory=list)
    status: AccountStatus = AccountStatus.planned
    access: str = "blotato"                # METHOD, never a credential
    persona: Optional[str] = None

class Surface(NamedTuple):
    account: str
    account_id: str
    platform: Platform

class Accounts:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.accounts: list[Account] = []

    @classmethod
    def load(cls, cfg: Config) -> "Accounts":
        a = cls(cfg)
        p = cfg.accounts_path
        if p.exists():
            text = p.read_text()                       # an I/O error here is a real problem, not "invalid"
            try:
                raw = json.loads(text)
                a.accounts = [Account(**x) for x in raw.get("accounts", [])]
            except Exception as e:
                # Hand-edit typo (the documented "paste account_id, set status:active" step).
                # Clear one-liner instead of a raw traceback.
                raise ControlFileError(f"{p.name} invalid: {_reason(e)}") from e
        return a

    def active(self) -> list[Account]:
        return [a for a in self.accounts if a.status is AccountStatus.active]

    def resolve_account_id(self, handle: str) -> str:
        for a in self.accounts:
            if a.handle == handle:
                if not a.account_id:
                    # Known handle but no Blotato id yet (e.g. planned/warming): fail loud
                    # rather than return "" — an empty accountId must never reach Blotato.
                    raise KeyError(f"{handle} has no account_id (status={a.status.value})")
                return a.account_id
        raise KeyError(handle)

    def validate(self) -> list[str]:
        """Config problems to surface before a run (e.g. active account missing Blotato id)."""
        problems = []
        for a in self.active():
            if not a.account_id:
                problems.append(f"active account {a.handle} has no account_id")
            if not a.platforms:
                problems.append(f"active account {a.handle} has no platforms")
        seen = set()
        for a in self.accounts:
            if a.handle in seen:
                problems.append(f"duplicate handle {a.handle} (handles must be unique)")
            seen.add(a.handle)
        return problems

    def surfaces(self) -> list[Surface]:
        return [Surface(a.handle, a.account_id, p) for a in self.active() for p in a.platforms]
