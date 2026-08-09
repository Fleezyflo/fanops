# src/fanops/settings.py — MOL-292: typed env boundary (constructed per Config(), never import-cached)
from __future__ import annotations
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from pydantic import BeforeValidator, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The boolean-word vocabulary is declared ONCE, in config.py, and imported here — this module used to
# keep its own `_ON`/`_OFF` copy, so the declaration boundary and the runtime read path could disagree
# about what "on" means without either side being wrong on its own terms. The dependency runs THIS way
# because config.py is stdlib-only (30 ms to import) while this module pulls pydantic (a further
# 143 ms); the ~50 modules that import config must not pay that for a four-word frozenset.
from fanops.config import bool_word, env_bool

_log = logging.getLogger("fanops.settings")

_VALID_BACKENDS = frozenset({"dryrun", "postiz", "zernio"})
_VALID_RESPONDERS = frozenset({"llm", "manual"})
_VALID_LLM_TRANSPORTS = frozenset({"claude", "cursor"})
PosterBackend = Literal["dryrun", "postiz", "zernio"]
_STRIP_STR_FIELDS = (
    "FANOPS_POSTER", "FANOPS_LIVE", "FANOPS_RESPONDER", "FANOPS_LLM_TRANSPORT", "FANOPS_LLM_MODEL", "FANOPS_ARTIST_NAME",
    "FANOPS_CLIP_PROFILE", "FANOPS_WHISPER_MODEL", "FANOPS_ASR_MODEL", "FANOPS_ASR_LANGUAGE",
    "FANOPS_SUBTITLE_FONT", "ZERNIO_API_URL", "META_GRAPH_URL", "FANOPS_OPERATOR_TZ",
)


def _strip_opt(v: object) -> str | None:
    if v is None: return None
    s = str(v).strip()
    return s or None


def _validate_bool_word(v: object) -> str:
    if v is None: return ""
    s = str(v).strip()
    if not s: return ""
    if bool_word(s) is None:
        raise ValueError(f"unrecognized bool value {s!r}; valid: 1/0, true/false, yes/no, on/off")
    return s


@dataclass(frozen=True)
class EnvVar:
    """THE registration record for one env var, declared ON its `Settings` field via `Annotated`.

    Adding a FANOPS_* flag used to mean editing nine hand-maintained sites that each restated the same
    name: a `_BOOL_ENV_FIELDS` tuple here, `config_introspect._STUDIO_SETTABLE`, a bespoke
    `golive.set_*` setter, the conftest scrub list, and so on. Every one was a copy that could rot
    independently — the same defect class as `_CLI_PRINT_COUNT`'s seven copies, with no scanner. The field
    declaration below is now the ONLY place these facts are stated; the sets at the bottom of this
    module DERIVE from `Settings.model_fields[...].metadata`, so a copy cannot drift out of agreement
    with the declaration because there is no copy left to drift.

    Not derivable from here, by construction (each needs a fact this record does not carry):
      - `config.py`'s read site — it states the flag's SEMANTIC default (`env_bool(..., default=)`),
        which this record cannot hold: `config` is imported BY this module, so the dependency cannot
        run the other way, and `ARCH-003` reads the env surface out of the AST, which needs a literal
        `os.getenv("NAME")` to see the var at all.
      - a Go-Live ROUTE + its template button (a URL and an endpoint name), and a `GoLiveStatus`
        field (a template contract). Both are hand-written per flag; `golive.set_flag` is the
        registry-gated writer they share.
    """
    bool_word: bool = False     # value is a boolean word (1/0, true/false, yes/no, on/off) -> BOOL_ENV_FIELDS
    studio: bool = False        # shown as operator-settable in `fanops config` / Studio -> STUDIO_SETTABLE
    operator_flag: bool = False # `golive.set_flag` may dual-write it -> OPERATOR_FLAGS. Deliberately FALSE
                                # for FANOPS_LIVE: go_live is its ONLY setter (confirm-gated, golive
                                # invariant #2), so the generic toggle must never reach it.


BoolEnv = Annotated[str, BeforeValidator(_validate_bool_word), EnvVar(bool_word=True)]
BoolFlag = Annotated[str, BeforeValidator(_validate_bool_word), EnvVar(bool_word=True, studio=True, operator_flag=True)]
LiveSwitch = Annotated[str, BeforeValidator(_validate_bool_word), EnvVar(bool_word=True, studio=True)]
StudioStr = Annotated[str, EnvVar(studio=True)]
StudioOpt = Annotated[str | None, EnvVar(studio=True)]


def _validate_poster(v: object) -> str:
    if v is None: return ""
    return str(v).strip()


def _validate_responder(v: object) -> str:
    if v is None: return ""
    s = str(v).strip().lower()
    if not s: return ""
    if s not in _VALID_RESPONDERS:
        raise ValueError(f"unrecognized FANOPS_RESPONDER={s!r}; valid: llm, manual")
    return s


def _validate_llm_transport(v: object) -> str:
    if v is None: return ""
    s = str(v).strip().lower()
    if not s: return ""
    if s not in _VALID_LLM_TRANSPORTS:
        raise ValueError(f"unrecognized FANOPS_LLM_TRANSPORT={s!r}; valid: claude, cursor")
    return s


def _strict_validate_poster(v: object) -> str:
    if v is None: return ""
    s = str(v).strip()
    if not s: return ""
    if s not in _VALID_BACKENDS:
        raise ValueError(f"unrecognized FANOPS_POSTER={s!r}; valid: {', '.join(sorted(_VALID_BACKENDS))}")
    return s


def _strict_validate_responder(v: object) -> str:
    if v is None: return ""
    s = str(v).strip().lower()
    if not s: return ""
    if s not in _VALID_RESPONDERS:
        raise ValueError(f"unrecognized FANOPS_RESPONDER={s!r}; valid: llm, manual")
    return s


def _strict_validate_llm_transport(v: object) -> str:
    if v is None: return ""
    s = str(v).strip().lower()
    if not s: return ""
    if s not in _VALID_LLM_TRANSPORTS:
        raise ValueError(f"unrecognized FANOPS_LLM_TRANSPORT={s!r}; valid: claude, cursor")
    return s


def _strict_validate_bool_word(v: object) -> str:
    if v is None: return ""
    s = str(v).strip()
    if not s: return ""
    if bool_word(s) is None:
        raise ValueError(f"unrecognized bool value {s!r}; valid: 1/0, true/false, yes/no, on/off")
    return s


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
    """Every FANOPS_* / credential env key with explicit type + default, and — via the `EnvVar` marker
    each field's annotation carries — THE single registration point for the var: `BOOL_ENV_FIELDS`,
    `STUDIO_SETTABLE` and `OPERATOR_FLAGS` are derived from these declarations, never restated.
    Built fresh per Config() after load_dotenv(override=True) so go-live dual-writes are visible on
    the next Config()."""
    model_config = SettingsConfigDict(extra="ignore")

    ANTHROPIC_API_KEY: str | None = None
    FANOPS_POSTER: str = ""
    FANOPS_LIVE: LiveSwitch = ""
    POSTIZ_URL: StudioOpt = None
    POSTIZ_API_KEY: StudioOpt = None
    FANOPS_MEDIA_PUBLIC_BASE: str | None = None
    R2_ACCOUNT_ID: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET: str | None = None
    ZERNIO_API_URL: str = ""
    ZERNIO_API_KEY: StudioOpt = None
    META_GRAPH_TOKEN: StudioOpt = None
    META_IG_USER_ID: str | None = None
    META_GRAPH_URL: str = ""
    FANOPS_CORPUS_TARGET: int = 80
    FANOPS_HASHTAG_SCRAPE_TRY_CAP: int = 25
    FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE: int = 40
    FANOPS_HASHTAG_SCRAPE_PARALLEL: int = 1
    FANOPS_HASHTAG_SCRAPE_DELAY: str = ""
    FANOPS_IG_SCRAPE_USER: str | None = None
    FANOPS_IG_SCRAPE_PASSWORD: str | None = None
    FANOPS_REQUIRE_FULL_OBJECTIVE: BoolEnv = ""
    FANOPS_RESPONDER: StudioStr = ""
    FANOPS_LLM_TRANSPORT: str = ""
    FANOPS_LLM_MODEL: str = ""
    FANOPS_ARTIST_NAME: str = ""
    FANOPS_CLIP_PROFILE: StudioStr = ""
    FANOPS_VISUAL_START: BoolEnv = ""
    FANOPS_SMART_FRAMING: BoolEnv = ""
    FANOPS_QUEUE_GATE: BoolEnv = ""
    FANOPS_SHOW_EXTRAS: BoolEnv = ""
    FANOPS_WHISPER_MODEL: str = ""
    FANOPS_ASR_MODEL: str = ""
    FANOPS_ASR_LANGUAGE: str = ""
    FANOPS_ISOLATE_VOCALS: BoolEnv = ""
    FANOPS_BURN_SUBS: BoolEnv = ""
    FANOPS_AWARE_REFRAME: BoolEnv = ""
    FANOPS_SUBTITLE_FONT: str = ""
    FANOPS_ACCOUNT_CASTING: BoolFlag = ""
    FANOPS_HOOK_ROUTER: BoolEnv = ""
    FANOPS_IMPACT_CUT: BoolEnv = ""
    FANOPS_INTRO_TEASE: BoolEnv = ""
    FANOPS_VARIANT_LEARNING: BoolFlag = ""
    FANOPS_VARIANT_MIN_POSTS: int = 3
    FANOPS_VARIANT_MIN_GAP: float = 10.0
    FANOPS_LEARN_AMPLIFY: BoolFlag = ""
    FANOPS_LEARN_RETIRE: BoolFlag = ""
    FANOPS_VARIANT_AMPLIFY: BoolFlag = ""
    FANOPS_VARIANT_AMPLIFY_MIN_POSTS: int = 8
    FANOPS_VARIANT_AMPLIFY_MIN_GAP: float = 25.0
    FANOPS_VARIANT_AMPLIFY_MIN_STREAK: int = 3
    FANOPS_VARIANT_UCB: BoolFlag = ""
    FANOPS_VARIANT_UCB_C: float = Field(default_factory=lambda: math.sqrt(2))
    FANOPS_VARIANT_TRANSFER: BoolFlag = ""
    FANOPS_VARIANT_TRANSFER_MIN_DONORS: int = 2
    FANOPS_VARIANT_TRANSFER_MAX_HOOKS: int = 2
    FANOPS_ADJUST_PER_SURFACE: BoolEnv = ""
    FANOPS_P4_DIM_BIAS: BoolEnv = ""
    FANOPS_TIMING_BIAS: BoolEnv = ""
    FANOPS_IG_RETENTION_PROOF: BoolEnv = ""
    FANOPS_MOMENT_HOOK_LEARNING: BoolEnv = ""
    FANOPS_P4_MIN_REACH_GAP: float = 0.0
    FANOPS_GC_KEEP_DAYS: int = 30
    FANOPS_UPLOAD_MAX_MB: int = 2048
    FANOPS_SOURCE_SHARD_MIN: int = 45
    FANOPS_OPERATOR_TZ: str = ""
    FANOPS_REALISTIC_CADENCE: BoolEnv = ""
    FANOPS_PUBLISH_LEAD_MINUTES: int = 0
    FANOPS_ZERNIO_MAX_UPLOAD_MB: int = 4
    FANOPS_POSTIZ_PUBLISH_PER_MIN: int = 4
    FANOPS_CONCURRENT_SOURCES: BoolEnv = ""
    FANOPS_CONCURRENT_WORKERS: int = 4
    FANOPS_POSTIZ_AUTOSTART: BoolEnv = ""
    FANOPS_POSTIZ_COMPOSE_DIR: str | None = None
    XDG_CACHE_HOME: str | None = None

    @field_validator("ANTHROPIC_API_KEY", "POSTIZ_URL", "POSTIZ_API_KEY", "FANOPS_MEDIA_PUBLIC_BASE",
                     "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET",
                     "ZERNIO_API_KEY", "META_GRAPH_TOKEN", "META_IG_USER_ID",
                     "FANOPS_IG_SCRAPE_USER", "FANOPS_IG_SCRAPE_PASSWORD", "FANOPS_POSTIZ_COMPOSE_DIR",
                     "XDG_CACHE_HOME", mode="before")
    @classmethod
    def _opt_str(cls, v): return _strip_opt(v)

    @field_validator("FANOPS_CORPUS_TARGET", mode="before")
    @classmethod
    def _corpus_target(cls, v):
        iv = _parse_int(v, 80)
        return iv if iv >= 1 else 80

    @field_validator("FANOPS_HASHTAG_SCRAPE_TRY_CAP", mode="before")
    @classmethod
    def _scrape_try_cap(cls, v):
        iv = _parse_int(v, 25)
        return iv if iv >= 1 else 25

    @field_validator("FANOPS_HASHTAG_SCRAPE_COTAG_ENQUEUE", mode="before")
    @classmethod
    def _scrape_cotag_cap(cls, v):
        iv = _parse_int(v, 40)
        return iv if iv >= 0 else 40

    @field_validator("FANOPS_HASHTAG_SCRAPE_PARALLEL", mode="before")
    @classmethod
    def _scrape_parallel(cls, v):
        iv = _parse_int(v, 1)
        return iv if iv >= 1 else 1

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

    @field_validator("FANOPS_SOURCE_SHARD_MIN", mode="before")
    @classmethod
    def _shard_min(cls, v): return _parse_int_failopen(v, 45)

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

    @field_validator("FANOPS_SOURCE_SHARD_MIN", mode="after")
    @classmethod
    def _shard_clamp(cls, v): return v if v >= 0 else 0

    @field_validator("FANOPS_UPLOAD_MAX_MB", "FANOPS_ZERNIO_MAX_UPLOAD_MB", mode="after")
    @classmethod
    def _mb_clamp(cls, v): return max(1, v)

    @field_validator("FANOPS_POSTIZ_PUBLISH_PER_MIN", mode="after")
    @classmethod
    def _postiz_throttle(cls, v): return v if v >= 0 else 4

    @field_validator(*_STRIP_STR_FIELDS, mode="before")
    @classmethod
    def _strip_str(cls, v):
        if v is None: return ""
        s = str(v).strip()
        return s

    @field_validator("FANOPS_POSTER", mode="before")
    @classmethod
    def _poster(cls, v): return _validate_poster(v)

    @field_validator("FANOPS_RESPONDER", mode="before")
    @classmethod
    def _responder(cls, v): return _validate_responder(v)

    @field_validator("FANOPS_LLM_TRANSPORT", mode="before")
    @classmethod
    def _llm_transport(cls, v): return _validate_llm_transport(v)

    # The bool-word check that used to live here as `@field_validator(*_BOOL_ENV_FIELDS)` now rides on
    # the `BoolEnv`/`BoolFlag`/`LiveSwitch` annotations themselves, so declaring a field bool and
    # validating it bool are the same act — a name can no longer be in one and missing from the other.

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
        if v not in _VALID_RESPONDERS:
            _log.warning("ignoring unknown FANOPS_RESPONDER=%r (using manual); valid: llm, manual", v)
            return "manual"
        return v

    def llm_transport(self) -> str:
        v = (self.FANOPS_LLM_TRANSPORT or "").strip().lower()
        if not v: return "claude"
        if v not in _VALID_LLM_TRANSPORTS:
            _log.warning("ignoring unknown FANOPS_LLM_TRANSPORT=%r (using claude); valid: claude, cursor", v)
            return "claude"
        return v

    def opt_on(self, raw: str, *, default: bool) -> bool:
        # Delegates to the shared parser. The local copy this replaced read an UNRECOGNIZED word as
        # `raw in _ON` — i.e. False even when `default` was True — so `opt_on(x, default=True)`
        # silently contradicted its own default for a typo. The shared rule falls back to `default`
        # for every unrecognized word, which is what every Config boolean property already does.
        return env_bool(raw, default=default)

    @classmethod
    def runtime_load(cls, root: Path) -> tuple[Settings, dict[str, str | None]]:
        load_dotenv(root / ".env", override=True)
        enriched = _enriched_env(dict(os.environ))
        secrets = {k: enriched.get(k) for k in enriched if _is_resolved_secret_key(k)}
        try:
            return cls.model_validate(enriched), secrets
        except ValidationError as exc:
            return _coerce_from_errors(enriched, exc), secrets

    @classmethod
    def strict_validate(cls, env: dict[str, str] | None = None) -> Settings:
        raw = dict(env if env is not None else os.environ)
        enriched = _enriched_env(raw)
        data = dict(enriched)
        if (v := data.get("FANOPS_POSTER")):
            data["FANOPS_POSTER"] = _strict_field("FANOPS_POSTER", _strict_validate_poster, v)
        if (v := data.get("FANOPS_RESPONDER")):
            data["FANOPS_RESPONDER"] = _strict_field("FANOPS_RESPONDER", _strict_validate_responder, v)
        if (v := data.get("FANOPS_LLM_TRANSPORT")):
            data["FANOPS_LLM_TRANSPORT"] = _strict_field("FANOPS_LLM_TRANSPORT", _strict_validate_llm_transport, v)
        for name in BOOL_ENV_FIELDS:
            if (v := data.get(name)):
                data[name] = _strict_field(name, _strict_validate_bool_word, v)
        return cls.model_validate(data)


def env_registry(model: type[BaseSettings] = Settings) -> dict[str, EnvVar]:
    """{field name -> its `EnvVar` record} for every field of `model` that carries one. THE reader the
    three sets below are projected through; parameterised by model so a test can register a synthetic
    var on a Settings subclass and prove each projection picks it up (and, removed, drops it) without
    reaching into pydantic internals. A field with no marker is simply absent — unregistered."""
    out: dict[str, EnvVar] = {}
    for name, field in model.model_fields.items():
        for m in field.metadata:
            if isinstance(m, EnvVar):
                out[name] = m
                break
    return out


def _project(pred, registry: dict[str, EnvVar]) -> tuple[str, ...]:
    return tuple(n for n, m in registry.items() if pred(m))


# The three derived surfaces. Each was a hand-maintained literal in a different module; each is now a
# projection of the field declarations above, so a newly registered var lands in all of them at once
# and a removed one leaves all of them at once. Order is declaration order (every consumer treats
# them as sets; only `strict_validate`'s loop iterates, and it is order-independent).
_REGISTRY = env_registry()
BOOL_ENV_FIELDS = _project(lambda m: m.bool_word, _REGISTRY)      # settings: strict_validate's bool-word pass
STUDIO_SETTABLE = frozenset(_project(lambda m: m.studio, _REGISTRY))       # config_introspect: the STUDIO column
OPERATOR_FLAGS = frozenset(_project(lambda m: m.operator_flag, _REGISTRY)) # golive.set_flag: what it may dual-write


def _strict_field(name: str, fn, v: object) -> str:
    try:
        return fn(v)
    except ValueError as exc:
        raise ValidationError.from_exception_data(
            "Settings",
            [{"type": "value_error", "loc": (name,), "input": v, "ctx": {"error": exc}}],
        ) from exc


def _is_resolved_secret_key(key: str) -> bool:
    return key in ("POSTIZ_API_KEY", "ZERNIO_API_KEY", "META_GRAPH_TOKEN") or key.startswith("META_GRAPH_TOKEN__")


_BASE_SECRET_KEYS = frozenset({"POSTIZ_API_KEY", "ZERNIO_API_KEY", "META_GRAPH_TOKEN"})


def _enriched_env(raw: dict[str, str]) -> dict[str, str]:
    from fanops.secret_provider import resolve_secret
    out = {k: v for k, v in raw.items()}
    to_resolve = {k for k in out if _is_resolved_secret_key(k)} | _BASE_SECRET_KEYS
    for key in to_resolve:
        if not _is_resolved_secret_key(key): continue
        v = out.get(key)
        env_val = v.strip() if isinstance(v, str) and v.strip() else None
        quiet = env_val is None and key not in raw
        resolved = resolve_secret(key, env_val, quiet=quiet)
        if resolved is not None:
            out[key] = resolved
    return out


def _coerce_from_errors(data: dict[str, str], exc: ValidationError) -> Settings:
    fixed = dict(data)
    seen: set[str] = set()
    for err in exc.errors():
        loc = err.get("loc", ())
        if not loc: continue
        field = str(loc[0])
        if field in seen or field not in Settings.model_fields: continue
        seen.add(field)
        default = Settings.model_fields[field].get_default(call_default_factory=True)
        _log.warning("env %s: %s (using default %r)", field, err.get("msg"), default)
        fixed[field] = default
    try:
        return Settings.model_validate(fixed)
    except ValidationError as exc2:
        return _coerce_from_errors(fixed, exc2)
