"""Flat active-account registry — non-secret metadata only (the Blotato account_id is a
non-secret identifier; the API key lives in .env). No lanes: every active account
participates. surfaces() yields each (handle, account_id, platform). resolve_account_id()
maps a handle to its numeric Blotato id (FIX F06: v1 passed the handle straight to Blotato)."""
from __future__ import annotations
import json
import os
from enum import Enum
from pathlib import Path
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


def _load_raw_accounts(p: Path) -> tuple[dict, list]:
    """Read accounts.json as the RAW parsed dict (absent file -> empty registry) and return (raw, the
    accounts list). Mutating the raw dict — not Account.model_dump() — is how every writer preserves
    unknown/future fields and sibling accounts exactly. A non-list 'accounts' is a corrupt file."""
    raw = json.loads(p.read_text()) if p.exists() else {"accounts": []}
    accounts = raw.get("accounts") if isinstance(raw, dict) else None
    if not isinstance(accounts, list):
        raise ControlFileError(f"{p.name} invalid: expected a top-level 'accounts' list")
    return raw, accounts


def _write_accounts_atomic(p: Path, raw: dict) -> None:
    """Persist the raw accounts dict via temp file + os.replace, so a crash mid-write never leaves a
    torn accounts.json. Indented for the operator who still hand-edits."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n")
    os.replace(tmp, p)                                   # atomic: never a half-written accounts.json


def write_account_id(cfg: Config, handle: str, account_id: str | int) -> str:
    """Set ONE account's shared `account_id` (the fallback poster id) in accounts.json atomically. The
    per-platform `write_integration` below is the preferred Go-Live mapping path; this stays for the
    handle-level fallback + back-compat. Mutates the RAW dict so unknown/future fields and siblings are
    preserved exactly; the id is coerced to str. Unknown handle -> KeyError (the caller turns it into a
    clean ActionResult). Absent file -> KeyError (no account to map yet)."""
    p = cfg.accounts_path
    raw, accounts = _load_raw_accounts(p)
    for a in accounts:
        if isinstance(a, dict) and a.get("handle") == handle:
            a["account_id"] = str(account_id)
            break
    else:
        raise KeyError(handle)
    _write_accounts_atomic(p, raw)
    return handle


def write_integration(cfg: Config, handle: str, platform: str, integration_id: str | int) -> str:
    """Map ONE (handle, platform) channel to its own poster id: set integrations[platform] = id in
    accounts.json atomically — the per-platform Go-Live mapping that replaces hand-editing JSON, so a
    handle's Instagram and TikTok point at their DIFFERENT Postiz integrations. Creates the integrations
    sub-dict if absent; preserves every sibling account, unknown field, and other platform's id. The id
    is coerced to str. Unknown handle -> KeyError (caller -> clean ActionResult)."""
    p = cfg.accounts_path
    raw, accounts = _load_raw_accounts(p)
    for a in accounts:
        if isinstance(a, dict) and a.get("handle") == handle:
            integ = a.get("integrations")
            if not isinstance(integ, dict):
                integ = {}
            integ[str(platform)] = str(integration_id)
            a["integrations"] = integ
            break
    else:
        raise KeyError(handle)
    _write_accounts_atomic(p, raw)
    return handle


def add_account(cfg: Config, handle: str, platforms: list, persona: str = "",
                status: str = "active", access: str = "postiz") -> str:
    """Onboard a BRAND-NEW account into accounts.json atomically — so the Go-Live tab adds an account
    WITHOUT the operator hand-editing JSON. Validates at this control-file boundary: a non-blank handle,
    and every platform a known Platform value (never write an account that won't reload). Rejects a
    duplicate handle. New accounts default to status=active (so they appear in the mapping list at once)
    and access=postiz; account_id stays empty — the per-platform ids are set afterward via
    write_integration / the mapping UI. Returns the handle; raises ValueError on bad input."""
    handle = (handle or "").strip()
    if not handle:
        raise ValueError("handle is required")
    plats = [getattr(x, "value", x) for x in platforms]      # accept Platform enums or value strings
    valid = {pf.value for pf in Platform}
    bad = [x for x in plats if x not in valid]
    if bad:
        raise ValueError(f"unknown platform(s): {', '.join(map(str, bad))}")
    p = cfg.accounts_path
    raw, accounts = _load_raw_accounts(p)
    if any(isinstance(a, dict) and a.get("handle") == handle for a in accounts):
        raise ValueError(f"duplicate handle {handle} (already exists)")
    accounts.append({"handle": handle, "account_id": "", "platforms": plats,
                     "status": str(status), "access": str(access),
                     "persona": persona or "", "integrations": {}})
    _write_accounts_atomic(p, raw)
    return handle
