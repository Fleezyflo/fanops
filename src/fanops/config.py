# src/fanops/config.py
"""Filesystem layout + env. Never stores a secret in code; reads .env at runtime.
Trims ONLY surrounding whitespace from the key (FIX F80: the v1 'keep trailing =' advice
was wrong)."""
from __future__ import annotations
import json
import logging
import math
import os
from pathlib import Path
from dotenv import load_dotenv

_log = logging.getLogger("fanops.config")

_STAGE = {
    "control": "00_control", "review": "00_review", "inbox": "01_inbox", "sources": "02_sources",
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
        # VESTIGIAL (2026-06-04): the autonomous responder now uses the operator's EXISTING `claude`
        # subscription via plain `claude -p` (NOT `--bare`), so it rides the OAuth/keychain login and
        # does NOT need ANTHROPIC_API_KEY. The preflight (cli._check_preflight) therefore keys off
        # `claude` being on PATH, NOT this var. Kept (harmless) for any third-party/Bedrock setup that
        # exports the key, and for backward compat — but it is NOT required for the default subscription
        # path. If `ANTHROPIC_API_KEY` happens to be set, `claude` will use it; if not, it uses the login.
        v = os.getenv("ANTHROPIC_API_KEY")
        return v.strip() if v and v.strip() else None

    @property
    def poster_backend(self) -> str:
        return os.getenv("FANOPS_POSTER") or "dryrun"

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
    def subtitle_font(self) -> str:
        # Operator override for the .ass subtitle font. Default "Arial Unicode MS" — an
        # Arabic-capable face so RTL captions render; change it (FANOPS_SUBTITLE_FONT) if the
        # host lacks that font or the operator prefers another Unicode/Arabic typeface.
        v = os.getenv("FANOPS_SUBTITLE_FONT")
        return v.strip() if v and v.strip() else "Arial Unicode MS"

    @property
    def creative_variation(self) -> bool:
        # Per-account creative variation (v1, observe-only): with this ON, each active account
        # gets a genuinely different caption + burned-in on-screen hook per clip. DEFAULT OFF
        # (opt-in) — the OPPOSITE of burn_subs — because it adds a per-account ffmpeg pass and is
        # an A/B experiment, not a baseline behavior. Only the explicit on-words enable it; unset,
        # empty, or anything else stays OFF (today's shared-clip behavior).
        v = (os.getenv("FANOPS_CREATIVE_VARIATION") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_learning(self) -> bool:
        # Creative variation v2 (closing the learning loop): with this ON, request_captions biases
        # the next caption toward the per-account hook variant that has earned a TRUSTWORTHY win
        # (>= variant_min_posts analyzed posts AND beating the runner-up by >= variant_min_gap).
        # DEFAULT OFF (opt-in), INDEPENDENT of FANOPS_CREATIVE_VARIATION — same off-by-default,
        # fail-open posture as that toggle. Only the explicit on-words enable it; unset, empty, or
        # anything else stays OFF (today's behavior, no hint injected, loop stays open).
        v = (os.getenv("FANOPS_VARIANT_LEARNING") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_min_posts(self) -> int:
        # Trust-gate part 1 for variant_learning: minimum analyzed posts a hook variant must have
        # before its measured lift is trusted enough to bias the next caption. DEFAULT 3 (the
        # early-noise guard — with 2 accounts, acting on 1-2 data points is the noise-amplification
        # trap). A non-int env falls back to the default rather than crashing an autonomous run.
        try:
            return int(os.getenv("FANOPS_VARIANT_MIN_POSTS", "3"))
        except ValueError:
            return 3

    @property
    def variant_min_gap(self) -> float:
        # Trust-gate part 2 for variant_learning: the leader's mean lift_score must beat the
        # runner-up's by at least this margin to emit a hint. DEFAULT 10.0 (same lift_score scale
        # as the HOLD-gate lift floor — a real margin, not noise). A non-float env falls back to
        # the default rather than crashing.
        try:
            return float(os.getenv("FANOPS_VARIANT_MIN_GAP", "10"))
        except ValueError:
            return 10.0

    @property
    def variant_amplify(self) -> bool:
        # Creative variation v3 (variant-gated amplification): with this ON, a per-account hook
        # variant that has earned a SUSTAINED, well-evidenced win auto-amplifies its source (the
        # existing adjust.amplify path), carrying the winning hook into the moment-request guidance.
        # This is the FIRST feature to touch the amplify/cascade machinery (audit C1), so it is the
        # KILL SWITCH: DEFAULT OFF (opt-in). Only the explicit on-words enable it; unset/empty/other
        # stays OFF (today's behavior — no variant-driven amplify). Amplify-only: never feeds retire.
        v = (os.getenv("FANOPS_VARIANT_AMPLIFY") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_amplify_min_posts(self) -> int:
        # v3 trust-gate part 1 (stronger than v2's variant_min_posts=3): the winning hook must have
        # at least this many analyzed posts on the surface before its win is trusted enough to AMPLIFY
        # (a far more consequential act than v2's caption-bias). DEFAULT 8. Non-int env -> default.
        try:
            return int(os.getenv("FANOPS_VARIANT_AMPLIFY_MIN_POSTS", "8"))
        except ValueError:
            return 8

    @property
    def variant_amplify_min_gap(self) -> float:
        # v3 trust-gate part 2 (stronger than v2's variant_min_gap=10): the winner's mean lift must
        # beat the runner-up's by at least this margin. DEFAULT 25.0 (same lift_score scale).
        # Non-float env -> default.
        try:
            return float(os.getenv("FANOPS_VARIANT_AMPLIFY_MIN_GAP", "25"))
        except ValueError:
            return 25.0

    @property
    def variant_amplify_min_streak(self) -> int:
        # v3 trust-gate part 3 (the core NEW safety property — has no v2 analogue): the SAME hook must
        # have led the gate across at least this many DISTINCT evidence windows (new analyzed-post
        # batches) before amplifying. >= 2 means "never act on a single window". DEFAULT 3.
        # Non-int env -> default.
        try:
            return int(os.getenv("FANOPS_VARIANT_AMPLIFY_MIN_STREAK", "3"))
        except ValueError:
            return 3

    @property
    def variant_ucb(self) -> bool:
        # Creative variation v3 (the bandit): with this ON, the OWN-surface caption bias is chosen
        # by a deterministic UCB1 multi-armed bandit (variant_learning.ucb_rank) instead of v2's
        # gated-greedy best_hooks — balancing exploit (proven hooks) against explore (under-sampled
        # ones), and never silent once any variant data exists. DEFAULT OFF (opt-in), INDEPENDENT of
        # FANOPS_VARIANT_LEARNING (still the master gate — UCB is inert if learning is off). Does NOT
        # affect variant_amplify, which keeps using best_hooks as its safety floor. Only the explicit
        # on-words enable it; unset/empty/other stays OFF (v2 greedy behavior).
        v = (os.getenv("FANOPS_VARIANT_UCB") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_ucb_c(self) -> float:
        # The UCB1 exploration weight `c` in score = mean_lift + c*sqrt(ln N / n). DEFAULT sqrt(2)
        # (the UCB1 literature standard — balanced). Larger c => more exploration of under-sampled
        # hooks; c == 0 => pure greedy (degenerates to v2-greedy's "highest mean wins"). A negative
        # c would INVERT exploration into anti-exploration (always pick the most-sampled) — guard it:
        # a non-float OR negative env falls back to the default rather than crashing an autonomous run.
        try:
            v = float(os.getenv("FANOPS_VARIANT_UCB_C", ""))
        except ValueError:
            return math.sqrt(2)
        return v if v >= 0 else math.sqrt(2)

    @property
    def variant_transfer(self) -> bool:
        # Cross-account / cross-surface learning transfer (the v2 follow-up): with this ON,
        # request_captions may bias a COLD recipient surface (one with no trustworthy winner of its
        # own yet) toward a hook STYLE proven on OTHER same-platform surfaces. INDEPENDENT of both
        # FANOPS_CREATIVE_VARIATION and FANOPS_VARIANT_LEARNING. DEFAULT OFF (opt-in), fail-open:
        # unset/empty/other -> today's behavior, no transferred prior injected.
        v = (os.getenv("FANOPS_VARIANT_TRANSFER") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_transfer_min_donors(self) -> int:
        # Transfer gate (stricter than v2's): a hook style transfers to a cold recipient only if it
        # is the v2-gated winner on at least this many DISTINCT other same-platform donor surfaces.
        # DEFAULT 2 — one surface's local win is not yet a platform-level signal. A non-int env
        # falls back to the default rather than crashing an autonomous run.
        try:
            return int(os.getenv("FANOPS_VARIANT_TRANSFER_MIN_DONORS", "2"))
        except ValueError:
            return 2

    @property
    def variant_transfer_max_hooks(self) -> int:
        # Cap on how many borrowed styles a single caption request may carry, so even a popular
        # style-cluster cannot flood one caption (anti-homogenization). DEFAULT 2. A non-int env
        # falls back to the default.
        try:
            return int(os.getenv("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", "2"))
        except ValueError:
            return 2

    @property
    def publish_lead_minutes(self) -> int:
        # The editorial window (spec §4): a CONSTANT offset added to every post's deterministic
        # scheduled_time at CROSSPOST time, so a freshly-queued post sits in `queued` for ~lead
        # minutes before publish_due ships it. DEFAULT 0 == today's exact behavior (every post due
        # immediately under a past base-time). A non-int OR negative env -> 0: unlike the other int
        # knobs, a negative lead would shift the anchor before `base` and corrupt the window, so it
        # is explicitly clamped (the variant_ucb_c precedent), not merely caught.
        try:
            v = int(os.getenv("FANOPS_PUBLISH_LEAD_MINUTES", "0"))
        except ValueError:
            return 0
        return v if v >= 0 else 0
