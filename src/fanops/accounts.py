"""Flat active-account registry — non-secret metadata only (the Blotato account_id is a
non-secret identifier; the API key lives in .env). No lanes: every active account
participates. surfaces() yields each (handle, account_id, platform). resolve_account_id()
maps a handle to its numeric Blotato id (FIX F06: v1 passed the handle straight to Blotato)."""
from __future__ import annotations
import json
import os
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
    account_id: str = ""                   # shared/legacy id (Blotato numeric, or a Postiz integration);
                                           # the FALLBACK when a platform has no per-platform id below
    platforms: list[Platform] = Field(default_factory=list)
    status: AccountStatus = AccountStatus.planned
    access: str = "blotato"                # METHOD, never a credential
    persona: Optional[str] = None
    # Per-platform poster ids keyed by Platform.value (e.g. {"instagram": "ig_1", "tiktok": "tk_9"}).
    # A handle's Instagram and TikTok are DIFFERENT Postiz integrations, so each (handle, platform) must
    # resolve to its OWN id. ADDITIVE: empty on a legacy account, which then resolves via account_id —
    # no migration. A platform absent here falls back to account_id (so a partly-mapped account works).
    integrations: dict[str, str] = Field(default_factory=dict)

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

    def resolve_account_id(self, handle: str, platform: Optional[Platform] = None) -> str:
        """The poster id for a handle, per-platform when `platform` is given. Prefers the platform's own
        integrations[platform] id, else the shared account_id fallback (back-compat). A known handle whose
        chosen id is empty fails loud rather than returning "" — an empty id must never reach the poster
        (FIX F06). `platform=None` keeps the legacy handle-only behavior (returns account_id)."""
        for a in self.accounts:
            if a.handle == handle:
                chosen = (a.integrations.get(platform.value) if platform else None) or a.account_id
                if not chosen:
                    where = platform.value if platform else "any platform"
                    raise KeyError(f"{handle} has no account_id for {where} (status={a.status.value})")
                return chosen
        raise KeyError(handle)

    def validate(self) -> list[str]:
        """Config problems to surface before a run. Per-platform: each active account's every platform
        must resolve to an id (its integrations[platform] OR the shared account_id) — so a multi-platform
        handle with one channel unmapped is flagged by name, while a legacy single-account_id account
        still passes via the fallback."""
        problems = []
        for a in self.active():
            if not a.platforms:
                problems.append(f"active account {a.handle} has no platforms")
            for p in a.platforms:
                if not (a.integrations.get(p.value) or a.account_id):
                    problems.append(f"active account {a.handle} has no account_id for {p.value}")
        seen = set()
        for a in self.accounts:
            if a.handle in seen:
                problems.append(f"duplicate handle {a.handle} (handles must be unique)")
            seen.add(a.handle)
        return problems

    def surfaces(self) -> list[Surface]:
        # Each (handle, platform) carries its OWN poster id: the platform's integrations id, else the
        # shared account_id fallback — so a multi-platform handle posts each platform to its own channel.
        return [Surface(a.handle, a.integrations.get(p.value) or a.account_id, p)
                for a in self.active() for p in a.platforms]


def write_account_id(cfg: Config, handle: str, account_id: str | int) -> str:
    """Set ONE account's `account_id` (the Postiz INTEGRATION id for a postiz deployment) in
    accounts.json and persist atomically, so the Studio Go-Live tab can map an account to a Postiz
    integration WITHOUT the operator hand-editing JSON (the one non-technical win). Mutates the RAW
    parsed dict — NOT Account.model_dump() — so any unknown/future field on the target account, and
    every sibling account, is preserved exactly; only the target handle's account_id changes (a fresh
    int/str is coerced to str, the form accounts.json stores). Unknown handle -> KeyError (the caller
    turns it into a clean ActionResult). Written via temp file + os.replace so a crash mid-write never
    leaves a torn accounts.json. Absent file -> KeyError (no account to map yet)."""
    p = cfg.accounts_path
    raw = json.loads(p.read_text()) if p.exists() else {"accounts": []}
    accounts = raw.get("accounts") if isinstance(raw, dict) else None
    if not isinstance(accounts, list):
        raise ControlFileError(f"{p.name} invalid: expected a top-level 'accounts' list")
    for a in accounts:
        if isinstance(a, dict) and a.get("handle") == handle:
            a["account_id"] = str(account_id)
            break
    else:
        raise KeyError(handle)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n")     # readable for the operator who still hand-edits
    os.replace(tmp, p)                                   # atomic: never a half-written accounts.json
    return handle
