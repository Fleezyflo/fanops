# src/fanops/config.py
"""Filesystem layout + env. Never stores a secret in code; reads .env at runtime.
Trims ONLY surrounding whitespace from the key (FIX F80: the v1 'keep trailing =' advice
was wrong)."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

_STAGE = {
    "control": "00_control", "inbox": "01_inbox", "sources": "02_sources",
    "clips": "03_clips", "agent_io": "04_agent_io", "scheduled": "05_scheduled",
    "published": "06_published", "reports": "07_reports",
}

class Config:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else Path.cwd()
        load_dotenv(self.root / ".env")
        self.base = self.root / "MohFlow-FanOps"
        for attr, name in _STAGE.items():
            setattr(self, attr, self.base / name)
        self.ledger_path = self.control / "ledger.json"
        self.lock_path = self.control / "ledger.lock"
        self.digest_path = self.control / "ledger_digest.md"
        self.accounts_path = self.control / "accounts.json"
        self.context_path = self.control / "context.md"
        self.log_path = self.reports / "run.log"

    @property
    def blotato_api_key(self) -> str | None:
        v = os.getenv("BLOTATO_API_KEY")
        return v.strip() if v and v.strip() else None

    @property
    def poster_backend(self) -> str:
        return os.getenv("FANOPS_POSTER") or "dryrun"

    @property
    def escalation_budget_usd(self) -> float:
        try: return float(os.getenv("FANOPS_ESCALATION_BUDGET_USD") or 0.0)
        except ValueError: return 0.0

    @property
    def responder_mode(self) -> str:
        return os.getenv("FANOPS_RESPONDER") or "manual"
