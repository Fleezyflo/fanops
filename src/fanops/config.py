# src/fanops/config.py
"""Filesystem layout + env. Never stores a secret in code; reads .env at runtime.
Trims ONLY surrounding whitespace from the key (FIX F80: the v1 'keep trailing =' advice
was wrong)."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

_log = logging.getLogger("fanops.config")

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
        self.tuning_path = self.control / "tuning.json"
        self.log_path = self.reports / "run.log"

    def tuning(self) -> dict:
        """Operator overrides for the HOLD gate + optimization target, read from the OPTIONAL
        00_control/tuning.json (audit b). Shape:
            {"offbrand_en": [...regex...], "offbrand_ar": [...regex...],
             "lift_weights": {"saves": 4.0, ...}}
        Absent file or a missing key -> the in-code DEFAULT is used (caption._OFFBRAND_EN/_AR,
        track._W), so existing behavior is unchanged and no new REQUIRED file is introduced.
        Unlike a control file (accounts.json / ledger.json -> ControlFileError), this file is
        OPTIONAL: a corrupt/unreadable tuning.json must NEVER crash an autonomous run — we log a
        warning and fall back to {} (i.e. all defaults). Not cached: each call re-reads, so an
        operator edit takes effect on the next stage without a process restart (the file is tiny
        and read at most once per stage)."""
        p = self.tuning_path
        if not p.exists():
            return {}
        try:
            raw = json.loads(p.read_text())
        except Exception as e:                              # malformed JSON / unreadable
            _log.warning("ignoring %s (using built-in defaults): %s", p.name, e)
            return {}
        if not isinstance(raw, dict):                       # e.g. a top-level list/number
            _log.warning("ignoring %s (expected a JSON object, using built-in defaults)", p.name)
            return {}
        return raw

    @property
    def blotato_api_key(self) -> str | None:
        v = os.getenv("BLOTATO_API_KEY")
        return v.strip() if v and v.strip() else None

    @property
    def anthropic_api_key(self) -> str | None:
        # Mirrors blotato_api_key. The autonomous responder shells out to `claude --bare`, which
        # reads ONLY ANTHROPIC_API_KEY (it ignores the OAuth login / keychain) — so its presence is
        # the difference between real content and a silent zero-output run. Surfaced as a property
        # for symmetry/testability and consumed by cli._check_preflight.
        v = os.getenv("ANTHROPIC_API_KEY")
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

    @property
    def artist_name(self) -> str:
        # Operator override for the artist DISPLAY NAME used as the YouTube title fallback when a
        # post has no explicit title (audit h). Default "Moh Flow" — unchanged from the old
        # hardcoded value in payload.default_target_fields, so existing behavior is identical; an
        # operator running FanOps for a different artist sets FANOPS_ARTIST_NAME. NOTE: this is the
        # display name, DISTINCT from tagging.ARTIST_HANDLE (the @mohflow caption mention) — they
        # have different sources and are intentionally not unified.
        v = os.getenv("FANOPS_ARTIST_NAME")
        return v.strip() if v and v.strip() else "Moh Flow"

    @property
    def whisper_model(self) -> str:
        # Operator override for the local Whisper model. Default "turbo" (fast, good
        # timestamps). Pin a smaller model (e.g. "tiny"/"base") for offline / air-gapped /
        # CI hosts where the larger checkpoints cannot be downloaded.
        v = os.getenv("FANOPS_WHISPER_MODEL")
        return v.strip() if v and v.strip() else "turbo"

    @property
    def burn_subs(self) -> bool:
        # On/off toggle for the burned-in subtitle feature. DEFAULT ON: an unset env (the common
        # case) burns subs, so the feature is live without operator action. Only the explicit
        # off-words "0"/"false"/"no"/"off" (case-insensitive, surrounding ws trimmed) disable it;
        # everything else — including a typo — stays ON, the safe default for a content pipeline.
        v = os.getenv("FANOPS_BURN_SUBS")
        return (v or "").strip().lower() not in {"0", "false", "no", "off"}

    @property
    def creative_variation(self) -> bool:
        # Per-account creative variation (caption + burned-in hook A/B). DEFAULT OFF (opposite of
        # burn_subs): only an explicit on-word opts in, so a host that never sets the env keeps
        # today's shared-clip behavior. Mirrors the off-word matching of burn_subs, inverted.
        v = (os.getenv("FANOPS_CREATIVE_VARIATION") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def subtitle_font(self) -> str:
        # Operator override for the .ass subtitle font. Default "Arial Unicode MS" — an
        # Arabic-capable face so RTL captions render; change it (FANOPS_SUBTITLE_FONT) if the
        # host lacks that font or the operator prefers another Unicode/Arabic typeface.
        v = os.getenv("FANOPS_SUBTITLE_FONT")
        return v.strip() if v and v.strip() else "Arial Unicode MS"
