# src/fanops/settings.py — MOL-292: typed env boundary (constructed per Config(), never import-cached)
from __future__ import annotations
import logging
import math
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger("fanops.config")
_ON = frozenset({"1", "true", "yes", "on"})
_VALID_BACKENDS = frozenset({"dryrun", "postiz", "zernio"})
PosterBackend = Literal["dryrun", "postiz", "zernio"]


def _strip_opt(v: object) -> str | None:
    if v is None: return None
    s = str(v).strip()
    return s or None


def _env_on(v: object, *, default: bool) -> bool:
    if v is None: return default
    s = str(v).strip().lower()
    if not s: return default
    return s in _ON


def _parse_int(v: object, default: int) -> int:
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return default
    return int(v)  # ValidationError on bad input


def _parse_int_failopen(v: object, default: int) -> int:
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _parse_float(v: object, default: float) -> float:
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return default
    return float(v)


class Settings(BaseSettings):
    """Every FANOPS_* / credential env key with explicit type + default. Built fresh per Config()
    after load_dotenv(override=True) so go-live dual-writes are visible on the next Config()."""
    model_config = SettingsConfigDict(extra="ignore")

    ANTHROPIC_API_KEY: str | None = None
    FANOPS_POSTER: str = ""
    FANOPS_LIVE: str = ""
    POSTIZ_URL: str | None = None
    POSTIZ_API_KEY: str | None = None
    FANOPS_MEDIA_PUBLIC_BASE: str | None = None
    R2_ACCOUNT_ID: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET: str | None = None
    ZERNIO_API_URL: str = ""
    ZERNIO_API_KEY: str | None = None
    META_GRAPH_TOKEN: str | None = None
    META_IG_USER_ID: str | None = None
    META_GRAPH_URL: str = ""
    FANOPS_HASHTAG_TRENDS: str = ""
    FANOPS_REQUIRE_FULL_OBJECTIVE: str = ""
    FANOPS_RESPONDER: str = ""
    FANOPS_LLM_MODEL: str = ""
    FANOPS_ARTIST_NAME: str = ""
    FANOPS_CLIP_PROFILE: str = ""
    FANOPS_VISUAL_START: str = ""
    FANOPS_SMART_FRAMING: str = ""
    FANOPS_WHISPER_MODEL: str = ""
    FANOPS_ASR_MODEL: str = ""
    FANOPS_ASR_LANGUAGE: str = ""
    FANOPS_ISOLATE_VOCALS: str = ""
    FANOPS_BURN_SUBS: str = ""
    FANOPS_AWARE_REFRAME: str = ""
    FANOPS_SUBTITLE_FONT: str = ""
    FANOPS_ACCOUNT_CASTING: str = ""
    FANOPS_HOOK_ROUTER: str = ""
    FANOPS_IMPACT_CUT: str = ""
    FANOPS_INTRO_TEASE: str = ""
    FANOPS_VARIANT_LEARNING: str = ""
    FANOPS_VARIANT_MIN_POSTS: int = 3
    FANOPS_VARIANT_MIN_GAP: float = 10.0
    FANOPS_VARIANT_AMPLIFY: str = ""
    FANOPS_VARIANT_AMPLIFY_MIN_POSTS: int = 8
    FANOPS_VARIANT_AMPLIFY_MIN_GAP: float = 25.0
    FANOPS_VARIANT_AMPLIFY_MIN_STREAK: int = 3
    FANOPS_VARIANT_UCB: str = ""
    FANOPS_VARIANT_UCB_C: float = Field(default_factory=lambda: math.sqrt(2))
    FANOPS_VARIANT_TRANSFER: str = ""
    FANOPS_VARIANT_TRANSFER_MIN_DONORS: int = 2
    FANOPS_VARIANT_TRANSFER_MAX_HOOKS: int = 2
    FANOPS_ADJUST_PER_SURFACE: str = ""
    FANOPS_P4_DIM_BIAS: str = ""
    FANOPS_TIMING_BIAS: str = ""
    FANOPS_IG_RETENTION_PROOF: str = ""
    FANOPS_MOMENT_HOOK_LEARNING: str = ""
    FANOPS_P4_MIN_REACH_GAP: float = 0.0
    FANOPS_GC_KEEP_DAYS: int = 30
    FANOPS_UPLOAD_MAX_MB: int = 2048
    FANOPS_OPERATOR_TZ: str = ""
    FANOPS_REALISTIC_CADENCE: str = ""
    FANOPS_PUBLISH_LEAD_MINUTES: int = 0
    FANOPS_ZERNIO_MAX_UPLOAD_MB: int = 4
    FANOPS_POSTIZ_PUBLISH_PER_MIN: int = 4
    FANOPS_CONCURRENT_SOURCES: str = ""
    FANOPS_CONCURRENT_WORKERS: int = 4

    @field_validator("ANTHROPIC_API_KEY", "POSTIZ_URL", "POSTIZ_API_KEY", "FANOPS_MEDIA_PUBLIC_BASE",
                     "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET",
                     "ZERNIO_API_KEY", "META_GRAPH_TOKEN", "META_IG_USER_ID", mode="before")
    @classmethod
    def _opt_str(cls, v): return _strip_opt(v)

    @field_validator("FANOPS_VARIANT_MIN_POSTS", mode="before")
    @classmethod
    def _vmin_posts(cls, v): return _parse_int(v, 3)

    @field_validator("FANOPS_VARIANT_AMPLIFY_MIN_POSTS", mode="before")
    @classmethod
    def _vamp_posts(cls, v): return _parse_int(v, 8)

    @field_validator("FANOPS_VARIANT_AMPLIFY_MIN_STREAK", mode="before")
    @classmethod
    def _vamp_streak(cls, v): return _parse_int(v, 3)

    @field_validator("FANOPS_VARIANT_TRANSFER_MIN_DONORS", mode="before")
    @classmethod
    def _vtr_donors(cls, v): return _parse_int(v, 2)

    @field_validator("FANOPS_VARIANT_TRANSFER_MAX_HOOKS", mode="before")
    @classmethod
    def _vtr_hooks(cls, v): return _parse_int(v, 2)

    @field_validator("FANOPS_GC_KEEP_DAYS", mode="before")
    @classmethod
    def _gc_days(cls, v): return _parse_int(v, 30)

    @field_validator("FANOPS_UPLOAD_MAX_MB", mode="before")
    @classmethod
    def _upload_mb(cls, v): return _parse_int_failopen(v, 2048)

    @field_validator("FANOPS_ZERNIO_MAX_UPLOAD_MB", mode="before")
    @classmethod
    def _zernio_mb(cls, v): return _parse_int_failopen(v, 4)

    @field_validator("FANOPS_POSTIZ_PUBLISH_PER_MIN", mode="before")
    @classmethod
    def _postiz_pm(cls, v): return _parse_int(v, 4)

    @field_validator("FANOPS_VARIANT_MIN_GAP", mode="before")
    @classmethod
    def _vmin_gap(cls, v): return _parse_float(v, 10.0)

    @field_validator("FANOPS_VARIANT_AMPLIFY_MIN_GAP", mode="before")
    @classmethod
    def _vamp_gap(cls, v): return _parse_float(v, 25.0)

    @field_validator("FANOPS_VARIANT_UCB_C", mode="before")
    @classmethod
    def _ucb_c(cls, v):
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return math.sqrt(2)
        fv = float(v)
        return fv if fv >= 0 else math.sqrt(2)

    @field_validator("FANOPS_P4_MIN_REACH_GAP", mode="before")
    @classmethod
    def _p4_gap(cls, v):
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return 0.0
        fv = float(v)
        return fv if fv >= 0 else 0.0

    @field_validator("FANOPS_PUBLISH_LEAD_MINUTES", mode="before")
    @classmethod
    def _lead(cls, v):
        iv = _parse_int(v, 0)
        return iv if iv >= 0 else 0

    @field_validator("FANOPS_CONCURRENT_WORKERS", mode="before")
    @classmethod
    def _workers(cls, v):
        iv = _parse_int(v, 4)
        return iv if iv >= 1 else 1

    @field_validator("FANOPS_GC_KEEP_DAYS", mode="after")
    @classmethod
    def _gc_clamp(cls, v): return v if v >= 1 else 30

    @field_validator("FANOPS_UPLOAD_MAX_MB", "FANOPS_ZERNIO_MAX_UPLOAD_MB", mode="after")
    @classmethod
    def _mb_clamp(cls, v): return max(1, v)

    @field_validator("FANOPS_POSTIZ_PUBLISH_PER_MIN", mode="after")
    @classmethod
    def _postiz_throttle(cls, v): return v if v >= 0 else 4

    def poster_backend(self) -> PosterBackend:
        v = (self.FANOPS_POSTER or "").strip()
        if not v: return "dryrun"
        if v not in _VALID_BACKENDS:
            _log.warning("ignoring unknown FANOPS_POSTER=%r (using dryrun); valid: %s",
                         v, ", ".join(sorted(_VALID_BACKENDS)))
            return "dryrun"
        return v  # type: ignore[return-value]

    def responder_mode(self) -> str:
        v = (self.FANOPS_RESPONDER or "").strip().lower()
        if not v: return "manual"
        if v not in {"llm", "manual"}:
            _log.warning("ignoring unknown FANOPS_RESPONDER=%r (using manual); valid: llm, manual", v)
            return "manual"
        return v

    def opt_on(self, raw: str, *, default: bool) -> bool:
        return _env_on(raw, default=default)
