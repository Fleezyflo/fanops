# src/fanops/config.py
"""Filesystem layout + env. Never stores a secret in code; properties read `os.environ`;
`.env` is loaded once at process entry (`cli.main`), not in Config.__init__.
Trims ONLY surrounding whitespace from the key (FIX F80: the v1 'keep trailing =' advice
was wrong)."""
from __future__ import annotations
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Literal

_log = logging.getLogger("fanops.config")


def certifi_ssl_env(base: dict | None = None, *, logger: logging.Logger | None = None) -> dict:
    """Subprocess env overlay: point SSL_CERT_FILE/REQUESTS_CA_BUNDLE at certifi (setdefault only).
    When `base` is None, mutates os.environ in place (_fwrun); otherwise mutates the provided dict (vocals)."""
    env = base if base is not None else os.environ
    try:
        import certifi
        env.setdefault("SSL_CERT_FILE", certifi.where())
        env.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError:
        if logger is not None:
            logger.warning("certifi absent — demucs SSL cert fix skipped (fail-open)", exc_info=True)
    return env


def _sanitize_tuning(raw: dict) -> dict:
    """Drop only the INVALID entries from a tuning.json override, keeping the good ones (a single bad
    regex used to make the consumer fall back to ALL defaults, silently losing every valid override).
    Stay fail-open — warn + drop, never raise. offbrand_* entries must be strings that compile as
    regex; lift_weights values must be real numbers (a non-numeric weight would crash lift_score)."""
    out = dict(raw)
    for key in ("offbrand_en", "offbrand_ar"):
        pats = out.get(key)
        if isinstance(pats, list):
            kept = []
            for p in pats:
                try:
                    re.compile(p); kept.append(p)
                except (re.error, TypeError):
                    _log.warning("tuning.json %s: dropping invalid regex %r (using remaining + defaults)", key, p)
            out[key] = kept
    weights = out.get("lift_weights")
    if isinstance(weights, dict):
        kept_w = {}
        for k, v in weights.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                kept_w[k] = v
            else:
                _log.warning("tuning.json lift_weights: dropping non-numeric weight %r=%r", k, v)
        out["lift_weights"] = kept_w
    return out

_STAGE = {
    "control": "00_control", "review": "00_review", "inbox": "01_inbox",
    "thirdparty_inbox": "01_thirdparty_inbox",   # M1: PEER of 01_inbox (NOT under it) — outside the
                                                 # native ingest_drops rglob, so a third-party staged
                                                 # asset can never be mislabeled native.
    "sources": "02_sources",
    "clips": "03_clips", "agent_io": "04_agent_io", "scheduled": "05_scheduled",
    "published": "06_published", "reports": "07_reports",
}

# The recognized poster backends. An unknown/typo'd FANOPS_POSTER resolves to dryrun (W4) — see
# poster_backend. dryrun = posts nothing; postiz = free self-hosted (IG/YouTube); zernio = hosted TikTok.
PosterBackend = Literal["dryrun", "postiz", "zernio"]
_VALID_BACKENDS = frozenset({"dryrun", "postiz", "zernio"})
# Live (real-posting) backends: a per-account backend override pointing at one of these is a real
# "go live for this account" and must be creds-gated + confirmed, like the global go_live (dryrun isn't).
_LIVE_BACKENDS = frozenset({"postiz", "zernio"})

# M2 per-account FRAMING values (Account.framing): the vertical crop bias for the account's render CUT.
# "top" -> head-safe upper-third crop (reframe_filter top_bias=True), "center" -> default centred crop.
# The strict WRITE boundary (add_account refuses anything else); resolve_top_bias maps these to
# the bool top_bias, falling back to the GLOBAL aware_reframe for None/blank/unknown (validate-or-default).
FRAMING_NAMES = frozenset({"top", "center"})

# Which PLATFORMS each live backend serves in THIS deployment. Used ONLY to bound the legacy
# FANOPS_POSTER bridge (accounts.effective_provider): a provider-less channel never falls back to a
# global that doesn't post its platform (H2 — e.g. a TikTok channel must not bridge to the IG-wired
# Postiz global, which would publish to the wrong provider/integration or burn the post). The explicit
# per-channel `backends` override ALWAYS wins first, so this only narrows the back-compat fallback
# (postiz serves IG/YouTube, zernio serves TikTok).
_BACKEND_PLATFORMS = {
    "postiz": frozenset({"instagram", "youtube"}),
    "zernio": frozenset({"tiktok"}),
}

# Per-gate model tier (llm_model_for): M1b splits the moment gate. `moments` (pass 1) chooses the
# WINDOWS; `moment_hooks` (pass 2) is the CREATIVE VISION hook AUTHOR — it SEES the picked window's
# frames and writes the on-screen retention hook (the watch-through driver). BOTH -> opus (picking
# quality unchanged + the hook is the operator's #1 ask). `captions` (hashtags only) stays MECHANICAL
# -> sonnet. FANOPS_LLM_MODEL overrides all.
_GATE_MODEL_DEFAULTS = {"moments": "opus", "moment_hooks": "opus", "captions": "sonnet"}
# Below this SOURCE length (seconds) the duration-aware ASR default upgrades to large-v3 — on little
# audio the most accurate model is cheap; at/above it (or unknown) the faster medium keeps a long
# source's transcription bounded under transcribe._WHISPER_TIMEOUT. ~5min = a short clip, not a full set.
_ASR_SHORT_SOURCE_SECONDS = 300.0
# Expected CPU realtime factors (incident data, MOL-481) — used to reject models that cannot finish
# within the whisper subprocess timeout budget before the kill fires.
_ASR_MODEL_RTF = {"large-v3": 2.5, "medium": 1.6, "small": 1.0, "base": 0.5}
_WHISPER_MODEL_RTF = {"large-v3": 2.5, "turbo": 0.8, "small": 0.6, "base": 0.4, "tiny": 0.2}
_ASR_MODEL_CHAIN = ("large-v3", "medium", "small", "base")
_WHISPER_MODEL_CHAIN = ("large-v3", "turbo", "small", "base", "tiny")
# Mirror transcribe._WHISPER_TIMEOUT / _PREWARM_TIMEOUT_FACTOR — config cannot import transcribe.
_ASR_WHISPER_TIMEOUT = 2700.0
_ASR_PREWARM_TIMEOUT_FACTOR = 1.5
_ASR_TIMEOUT_MARGIN = 60.0   # safety headroom before the subprocess kill

def _asr_timeout_budget(duration_seconds: float | None) -> float:
    if not duration_seconds:
        return _ASR_WHISPER_TIMEOUT
    return max(_ASR_WHISPER_TIMEOUT, float(duration_seconds) * _ASR_PREWARM_TIMEOUT_FACTOR)

def _asr_effective_duration(duration_seconds: float | None) -> float:
    return float(duration_seconds) if duration_seconds else _ASR_WHISPER_TIMEOUT / _ASR_PREWARM_TIMEOUT_FACTOR

def _pick_timeout_aware_model(duration_seconds: float | None, *, chain: tuple[str, ...], rtf: dict[str, float],
                              preferred: str, timeout_attempts: int = 0) -> str:
    dur = _asr_effective_duration(duration_seconds)
    budget = _asr_timeout_budget(duration_seconds) - _ASR_TIMEOUT_MARGIN
    try: start = chain.index(preferred)
    except ValueError: start = 0
    picked = chain[-1]
    for model in chain[start:]:
        if dur * rtf.get(model, 99.0) < budget:
            picked = model; break
    try: idx = chain.index(picked)
    except ValueError: idx = len(chain) - 1
    return chain[min(idx + max(0, timeout_attempts), len(chain) - 1)]

class Config:
    def __init__(self, root: Path | str | None = None):
        env_root = os.environ.get("FANOPS_ROOT")
        if root:
            self.root = Path(root)
        elif env_root:
            self.root = Path(env_root).expanduser().resolve()
        else:
            self.root = Path.cwd()
        self.base = self.root / "MohFlow-FanOps"
        for attr, name in _STAGE.items():
            setattr(self, attr, self.base / name)
        self.ledger_path = self.control / "ledger.sqlite"
        self.legacy_ledger_json_path = self.control / "ledger.json"   # M1-F break-glass: bridge import only
        self.lock_path = self.control / "ledger.lock"                 # vestigial; accounts/personas use flock
        self.digest_path = self.control / "ledger_digest.md"
        self.accounts_path = self.control / "accounts.json"
        self.accounts_lock_path = self.control / "accounts.lock"   # serializes the accounts.json read-modify-write mutators
        self.personas_path = self.control / "personas.json"   # A1 first-class personas (voice/tag_lean/corpus/intake); absent -> inline Account.persona stands
        self.personas_lock_path = self.control / "personas.lock"   # serializes the personas.json read-modify-write mutators
        self.context_path = self.control / "context.md"
        self.tuning_path = self.control / "tuning.json"
        self.hashtags_path = self.control / "hashtags.json"  # M4 dynamic reach-ranked tag store; absent -> frozen pools
        self.hashtag_budget_path = self.control / "hashtag_budget.json"  # M4 Meta Graph 30/7-day search budget counter
        self.hashtag_budget_lock = self.control / "hashtag_budget.lock"  # serializes record_query's read-modify-write (concurrent Studio calls lost writes -> quota over-spend)
        self.cutover_path = self.control / "cutover.json"   # live-cutover harness scratch state; NEVER the ledger
        self.insights_blocked_path = self.control / "insights_blocked.json"  # Leg 2: the LOUD fail-closed breadcrumb when Graph media-insights is refused for lack of the instagram_manage_insights scope; doctor + Home read it, a clean pull clears it
        self.timing_bias_path = self.control / "timing_bias.json"  # Leg 3 (timing): the reach-winning operator-local publish HOUR prior; absent -> no timing bias (byte-identical). apply_timing_bias writes it, surface_time's caller reads it (window-clamped)
        self.learn_doctor_path = self.control / "learn_doctor.json"   # F2 read-only learning field-shape verdict; M4 gates on it
        self.log_path = self.reports / "run.log"

    def render_path(self, batch_id, source_id, render_id: str, aspect) -> str:
        """Per-account Render file location. Hierarchical under clips/ by (batch, source) so every
        ingest BATCH has its own space and the renders are auditable on disk by lineage (the operator's
        'name/file/track all these things properly'); deterministic (same inputs -> same path) and ALWAYS
        under self.base, so the Studio _bounded serve check passes. Creates the subtree (mirrors the
        render_moment mkdir). aspect ('9:16') is colon-sanitized for the filename; the render_id is already
        aspect-specific via its parent clip, so the suffix is a human-scan aid, not the uniqueness."""
        a = str(getattr(aspect, "value", aspect)).replace(":", "x")
        sub = self.clips / (batch_id or "unbatched") / (source_id or "nosrc")
        sub.mkdir(parents=True, exist_ok=True)
        return str(sub / f"{render_id}.{a}.mp4")

    def tuning(self) -> dict:
        """Operator overrides for the HOLD gate + optimization target, read from the OPTIONAL
        00_control/tuning.json (audit b). Shape:
            {"offbrand_en": [...regex...], "offbrand_ar": [...regex...],
             "lift_weights": {"saves": 4.0, ...}}
        Absent file or a missing key -> the in-code DEFAULT is used (caption._OFFBRAND_EN/_AR,
        track._W), so existing behavior is unchanged and no new REQUIRED file is introduced.
        Unlike a control file (accounts.json / ledger.sqlite -> ControlFileError), this file is
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
        return _sanitize_tuning(raw)                         # warn+drop invalid entries, keep good ones

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
    def poster_backend(self) -> PosterBackend:
        # The LEGACY global FANOPS_POSTER. UI-LIE-FIX (R3-followup): under per-channel routing (M3), the
        # truth source for "which backend publishes a channel" is Accounts.effective_provider(handle,
        # platform) — NOT this global. Callers that mean "the per-channel publish provider" must use the
        # new effective_publish_mode() / Accounts.effective_provider() API; this property STILL exists
        # for the legacy bridge fallback (a channel with no explicit provider rides this) and for
        # back-compat reads where the operator literally cares about FANOPS_POSTER (.env diagnostics).
        # An UNKNOWN/typo'd value must not present as live: get_poster falls back to DryRunPoster for
        # any unrecognized backend, so a typo would otherwise show a LIVE banner while posting NOTHING.
        # Validate against the known set + fall back to dryrun + warn (variant_ucb_c posture).
        v = (os.getenv("FANOPS_POSTER") or "").strip()
        if not v:
            return "dryrun"
        if v not in _VALID_BACKENDS:
            _log.warning("ignoring unknown FANOPS_POSTER=%r (using dryrun); valid: %s",
                         v, ", ".join(sorted(_VALID_BACKENDS)))
            return "dryrun"
        return v

    def effective_publish_mode(self) -> str:
        """The per-channel publish-mode label (UI-LIE-FIX root). Single source of truth for every
        status display, hx-confirm gate, and friendly error: resolves the actual providers publishing
        across active accounts via Accounts.live_ready_channels (M3), so a live deployment with
        per-channel routing (IG via postiz + TikTok via zernio + legacy FANOPS_POSTER=dryrun
        bridge) returns 'postiz, zernio' — not 'dryrun'. Not-live -> 'dryrun'; live + no resolved
        channel yet -> 'live'. Fail-open: any accounts read error degrades to 'live' (the is_live
        truth is shown separately by callers).

        This is the canonical replacement for `cfg.poster_backend` at any callsite that means
        'what's actually publishing'. The legacy global stays for the narrow case of .env
        diagnostics."""
        if not self.is_live:
            return "dryrun"
        try:
            # Lazy import: accounts imports config, so import here avoids the cycle.
            from fanops.accounts import Accounts
            provs = sorted({p for _, _, p in Accounts.load(self).live_ready_channels()})
            return ", ".join(provs) if provs else "live"
        except Exception as e:
            # Fail-open label (the is_live truth is shown separately) — but log the degradation so a
            # corrupt registry showing a confident 'live' label is at least traceable, not silent.
            _log.warning("accounts read failed in effective_publish_mode (%s); degrading label to 'live'", e)
            return "live"

    def auth_key_name_for(self, backend: str) -> str:
        """The .env var name for `backend`'s API key — used by the FATAL auth-failure path to tell
        the operator exactly which key to check. UI-LIE-FIX: callers used to derive this with
        `cfg.poster_backend == 'postiz'`, which lied on per-channel deployments and only knew about
        one backend (postiz — zernio didn't exist in the branch). Centralized here so
        adding a backend doesn't require touching every error message."""
        return {"postiz": "POSTIZ_API_KEY", "zernio": "ZERNIO_API_KEY",
                }.get((backend or "").lower(), "FANOPS_POSTER")

    @staticmethod
    def auth_key_name_from_error(exc: Exception) -> str:
        """The STRUCTURAL truth: the auth-error class itself identifies the backend that failed.
        PostizAuthError -> POSTIZ_API_KEY, ZernioAuthError -> ZERNIO_API_KEY, etc. This is
        unambiguous — no per-channel routing lookup, no legacy global, just the exception's class.
        Used by the FATAL-auth handlers in actions.publish_now / run_advance / run_prepare so the
        key name is always right, no matter how the publish was routed."""
        from fanops.errors import PostizAuthError, ZernioAuthError
        if isinstance(exc, PostizAuthError):
            return "POSTIZ_API_KEY"
        if isinstance(exc, ZernioAuthError):
            return "ZERNIO_API_KEY"
        return "FANOPS_POSTER"

    @property
    def is_live(self) -> bool:
        # THE dryrun<->live switch (M2): the operator's intent, independent of WHICH provider publishes a
        # channel (that's per-channel — M3). Sourced from FANOPS_LIVE; when UNSET, derived from the legacy
        # FANOPS_POSTER (a recognized live backend -> live) so the running deployment keeps publishing with
        # NO .env edit. An unknown FANOPS_LIVE is never presented as live (the W4 false-banner guard).
        v = (os.getenv("FANOPS_LIVE") or "").strip().lower()
        if not v:
            return self.poster_backend in _LIVE_BACKENDS          # back-compat: a live FANOPS_POSTER implies live
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        _log.warning("ignoring unknown FANOPS_LIVE=%r (treating as not live); use 1/0", v)
        return False

    @property
    def live_route_exists(self) -> bool:
        # D15 coherence predicate: does ANYTHING actually route live? True iff the legacy global
        # poster_backend is a live backend WITH creds, OR at least one active accounts.json channel
        # resolves to a live provider whose creds are present (live_ready_channels). This exists to catch
        # the HALF-LIVE state: FANOPS_LIVE=1 (is_live True) with a typo'd FANOPS_POSTER (W4 -> dryrun) and
        # no per-channel backend — is_live says LIVE while every publish halts in `queued` (post-M1 dryrun
        # boundary), so the operator believes it's live and it publishes NOTHING. is_live is the operator's
        # INTENT; this is whether that intent has a live PATH. FAIL-OPEN: an unreadable registry must never
        # crash config load / the autonomous run — load_accounts_safe degrades to an empty registry (then
        # only the global-creds branch can be true), never raises. NB: does NOT read is_live — the caller
        # composes them (is_live AND NOT live_route_exists == half-live) so this stays a pure route check.
        if self.backend_has_creds(self.poster_backend):
            return True                                 # a genuinely-live global (byte-identical to legacy)
        from fanops.accounts import load_accounts_safe  # lazy: config<->accounts circular import
        accounts, err = load_accounts_safe(self)
        if err:
            # torn registry + no global creds -> not provably a live route. Fail-safe False, but log WHY
            # (mirrors is_live_backend / effective_publish_mode) so a silent False is diagnosable.
            _log.warning("accounts registry unreadable (%s); live_route_exists -> False (not provably live)", err)
            return False
        return bool(accounts.live_ready_channels())

    @property
    def postiz_url(self) -> str | None:
        # Base URL of a self-hosted (or hosted) Postiz instance, e.g. https://postiz.example.com or
        # https://api.postiz.com. The free, self-hosted poster backend (FANOPS_POSTER=postiz) posts
        # to {postiz_url}/public/v1/... . Trailing slash trimmed by the poster.
        v = os.getenv("POSTIZ_URL")
        return v.strip() if v and v.strip() else None

    @property
    def postiz_api_key(self) -> str | None:
        # Postiz public API key (Settings > Developers > Public API), sent as the Authorization
        # header. is_live_backend is True for a postiz backend WITH this key (M2): postiz both
        # PUBLISHES and now feeds the learning loop via its post analytics (PostizMetricsClient).
        from fanops.secret_provider import resolve_secret
        v = os.getenv("POSTIZ_API_KEY")
        env_val = v.strip() if v and v.strip() else None
        return resolve_secret("POSTIZ_API_KEY", env_val)

    @property
    def media_public_base(self) -> str | None:
        # Public HTTPS origin for mirrored clip media (FANOPS_MEDIA_PUBLIC_BASE). Postiz upload-from-url
        # and Instagram pull-from-URL require a host the backend can reach — localhost / Studio URLs are
        # SSRF-blocked. When set WITH R2_* creds, postiz_upload_media mirrors bytes to R2 first. Trailing
        # slash stripped.
        v = os.getenv("FANOPS_MEDIA_PUBLIC_BASE")
        return v.strip().rstrip("/") if v and v.strip() else None

    @property
    def r2_account_id(self) -> str | None:
        v = os.getenv("R2_ACCOUNT_ID")
        return v.strip() if v and v.strip() else None

    @property
    def r2_access_key_id(self) -> str | None:
        v = os.getenv("R2_ACCESS_KEY_ID")
        return v.strip() if v and v.strip() else None

    @property
    def r2_secret_access_key(self) -> str | None:
        v = os.getenv("R2_SECRET_ACCESS_KEY")
        return v.strip() if v and v.strip() else None

    @property
    def r2_bucket(self) -> str | None:
        v = os.getenv("R2_BUCKET")
        return v.strip() if v and v.strip() else None

    @property
    def zernio_url(self) -> str | None:
        # Base URL of the Zernio API. Zernio is HOSTED (not self-hosted like Postiz), so this defaults
        # to the public endpoint; ZERNIO_API_URL overrides it (parity with the docs' env var, e.g. a
        # regional host or a test double). The poster trims a trailing slash.
        v = (os.getenv("ZERNIO_API_URL") or "").strip()
        return v or "https://zernio.com/api/v1"

    @property
    def zernio_api_key(self) -> str | None:
        # Zernio API key (Settings > API Keys; sk_ + 64 hex), sent as `Authorization: Bearer <key>`.
        # WRITE-ONLY — never logged/echoed (mirrors postiz_api_key). is_live_backend is True for a zernio
        # backend WITH this key. Distinct from the POSTIZ key — they coexist (per-account routing
        # can run IG via Postiz AND TikTok via Zernio at once).
        from fanops.secret_provider import resolve_secret
        v = os.getenv("ZERNIO_API_KEY")
        env_val = v.strip() if v and v.strip() else None
        return resolve_secret("ZERNIO_API_KEY", env_val)

    @property
    def meta_graph_token(self) -> str | None:
        # Meta Graph API access token (IG Business) for the M4 hashtag TREND sampling. WRITE-ONLY —
        # never logged/echoed (mirrors postiz_api_key); meta_graph sends it as the access_token param.
        # Absent -> the Graph store build fails open to the frozen reach floor. Used ONLY by `hashtags
        # refresh`, never on the publish path.
        from fanops.secret_provider import resolve_secret
        v = os.getenv("META_GRAPH_TOKEN")
        env_val = v.strip() if v and v.strip() else None
        return resolve_secret("META_GRAPH_TOKEN", env_val)

    @property
    def meta_ig_user_id(self) -> str | None:
        # The IG Business account id that ig_hashtag_search requires as `user_id`. Absent -> no trends.
        v = os.getenv("META_IG_USER_ID")
        return v.strip() if v and v.strip() else None

    @property
    def meta_graph_url(self) -> str:
        # Meta Graph base (overridable for tests/self-host). Default the current stable Graph version.
        v = (os.getenv("META_GRAPH_URL") or "").strip()
        return (v or "https://graph.facebook.com/v21.0").rstrip("/")

    @property
    def hashtag_trends(self) -> bool:
        # B2 (2026-06-23): the Graph API is now ON BY DEFAULT — sample LIVE Meta Graph hashtag trends during
        # `hashtags refresh`. FAIL-OPEN: without META_GRAPH_TOKEN + META_IG_USER_ID, sample_trends no-ops and
        # the refresh falls open to the frozen reach floor, so default-ON is safe on a deployment with no Meta
        # app. Only the explicit OFF-words disable it (operator escape hatch).
        # NB: this gates the BACKGROUND refresh sampling only; the on-demand operator lookup (meta_graph.
        # tag_metrics) is gated by creds + budget, never this flag. Mirrors account_casting's default-ON shape.
        v = (os.getenv("FANOPS_HASHTAG_TRENDS") or "").strip().lower()
        return v not in {"0", "false", "no", "off"}     # DEFAULT ON; only explicit off-words disable it

    @property
    def require_full_objective(self) -> bool:
        # T4 opt-in: refuse to AMPLIFY a winner whose lift is DEGRADED (a primary weighted metric was
        # absent from its row -> the lift scalar is a partial objective). DEFAULT OFF (learning stays
        # conservative + the 3-window streak is already a proxy); only explicit on-words enable. Purely
        # gates variant_amplify; never recalibrates _W. Mirrors burn_subs.
        v = (os.getenv("FANOPS_REQUIRE_FULL_OBJECTIVE") or "").strip().lower()
        return v in {"1", "true", "yes", "on"}

    @property
    def is_live_backend(self) -> bool:
        # THE "live backend + key" guard, one home (stage-6 audit): it was duplicated verbatim at
        # three call sites (reconcile + both learning passes); drift in any copy would silently
        # enable/disable a pass. Live = a real poster AND a key to talk to it with — backend-aware
        # (M2): a postiz deployment is live on POSTIZ_API_KEY, a zernio deployment on
        # ZERNIO_API_KEY; dryrun (or any unrecognized backend) is never live. NB: this gates the
        # learn/reconcile passes; the speculative actuators stay frozen by learning_validated until cutover.
        # M2: "live" now flows from the is_live switch (FANOPS_LIVE, or the legacy FANOPS_POSTER derivation)
        # AND a backend has its key. Byte-identical when a live GLOBAL poster is configured (legacy path).
        # C1: go_live writes FANOPS_LIVE but NOT FANOPS_POSTER, so poster_backend is dryrun while channels
        # publish live — keying solely off the global silently froze the learn/reconcile passes. Fall
        # through to PER-CHANNEL readiness so this gate tracks what ACTUALLY publishes.
        if not self.is_live:
            return False
        if self.backend_has_creds(self.poster_backend):
            return True                                 # legacy single-global deployment (byte-identical)
        from fanops.accounts import load_accounts_safe  # lazy: config<->accounts circular import
        accounts, err = load_accounts_safe(self)
        if err:
            # torn registry + no global creds -> not provably live. This gate freezes the learn/reconcile
            # passes; returning False SILENTLY left the operator staring at frozen learning with no reason.
            # Keep the fail-safe False, but log WHY (mirrors track.py's load_accounts_safe warning).
            _log.warning("accounts registry unreadable (%s); learn/reconcile stays frozen (not provably live)", err)
            return False
        return bool(accounts.live_ready_channels())

    def backend_has_creds(self, backend: str) -> bool:
        # Does THIS backend have the credential to post live? Per-account routing (Zernio slice 2) asks
        # this about a per-post backend that may differ from the global poster_backend, so the live check
        # is one reusable home keyed by backend name (not just self.poster_backend). postiz->POSTIZ_API_KEY,
        # zernio->ZERNIO_API_KEY; dryrun/unknown -> never live.
        if backend == "postiz": return bool(self.postiz_api_key)
        if backend == "zernio": return bool(self.zernio_api_key)
        return False                                    # dryrun / anything unrecognized

    @property
    def responder_mode(self) -> str:
        # NO haphazard claude (ROOT): the AI responder is an EXPLICIT opt-in, mirroring is_live's .env gate.
        # It is 'llm' ONLY when the operator set FANOPS_RESPONDER=llm (via .env — the Studio AI-Responder
        # toggle, `fanops autopilot`, or `daemon install --responder llm`). The mere PRESENCE of `claude` on
        # PATH must NEVER auto-enable it: that fired `claude -p` on every run/kick/daemon-tick without intent.
        # Default is 'manual' (gates stay pending until llm is explicitly enabled or a human/cron answers) —
        # this single gate makes EVERY downstream responder path (run/respond/run_prepare/kick/daemon/
        # intro_match) opt-in by construction, so no per-site guard is needed. An UNKNOWN value (a .env
        # typo like 'llmm') must NOT slip through: get_responder only matches =='llm', so a typo silently
        # ran manual while the operator believed the AI was on. Validate + warn + fall back to manual,
        # mirroring poster_backend's guard above.
        v = (os.getenv("FANOPS_RESPONDER") or "").strip().lower()
        if not v:
            return "manual"
        if v not in {"llm", "manual"}:
            _log.warning("ignoring unknown FANOPS_RESPONDER=%r (using manual); valid: llm, manual", v)
            return "manual"
        return v

    def llm_model_for(self, kind: str) -> str:
        # V2 M1/F1: the creative brain stays PINNED (an unpinned `claude -p` drifts with the CLI default).
        # But the tier is now PER-GATE, not one blanket "opus": the MECHANICAL gate — hashtags-only
        # `captions` — runs on `sonnet` (fast + plenty for the task). (P11/MOL-152: moment_casting is gone.)
        # The CREATIVE VISION gates — `moments` (the pass-1 WINDOW picks) and `moment_hooks` (the pass-2
        # author of the on-screen RETENTION hook, the watch-through driver) — stay on `opus`.
        # FANOPS_LLM_MODEL forces ONE model for ALL gates (operator escape hatch; set a FULL id
        # like "claude-opus-4-..." for bit-stable repro). Validate-or-default shape (mirrors clip_profile).
        g = os.getenv("FANOPS_LLM_MODEL")
        if g and g.strip():
            return g.strip()
        return _GATE_MODEL_DEFAULTS.get(kind, "sonnet")

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
    def clip_profile(self) -> str:
        # Profile selecting the clip-length BAND (bands.band_for). LENGTH tiers (M2): "short" 8-15s,
        # "medium" 16-26s, "long" 28-45s. Legacy content-type bands stay valid (additive, NOT remapped):
        # "talk" 12-22s, "song" 18-35s. DEFAULT "talk" -> today's behavior unchanged (existing deployments
        # render byte-identically). An unknown value resolves to the talk band in band_for (validate-or-default).
        v = os.getenv("FANOPS_CLIP_PROFILE")
        return v.strip() if v and v.strip() else "talk"

    def resolve_clip_profile(self, account=None) -> str:
        """The clip-length profile (bands.band_for) for THIS account — its own Account.clip_profile when set,
        else the GLOBAL clip_profile (FANOPS_CLIP_PROFILE). This is the M2 per-account length seam: a render
        cut keys its band on resolve_clip_profile(account) instead of the one global knob, so @short ships
        8-15s clips while @long ships 28-45s off the SAME moment. Duck-typed (reads `account.clip_profile`)
        so config never imports accounts — that would be a cycle (accounts imports config). A None account,
        a None/blank override, or a non-str -> the global profile (byte-identical to today's single-knob path)."""
        prof = getattr(account, "clip_profile", None)
        return prof.strip() if isinstance(prof, str) and prof.strip() else self.clip_profile

    def resolve_top_bias(self, account=None) -> bool:
        """Whether THIS account's render CUT biases the vertical crop toward the upper third (head-safe) —
        its own Account.framing when pinned ("top" -> True, "center" -> False), else the GLOBAL aware_reframe
        (FANOPS_AWARE_REFRAME). The M2 per-account FRAMING seam: an account pins its crop independent of the
        single global knob (so @top ships head-safe while the rest inherit the default), and a render whose
        framing differs from the global is cut as its OWN per-account file. Duck-typed (reads `account.framing`)
        so config never imports accounts — that would be a cycle (accounts imports config). A None account, a
        None/blank/unknown framing -> the global aware_reframe (validate-or-default; byte-identical to today)."""
        fr = getattr(account, "framing", None)
        fr = fr.strip().lower() if isinstance(fr, str) else None
        if fr == "top": return True
        if fr == "center": return False
        return self.aware_reframe

    @property
    def visual_start(self) -> bool:
        # P1 strongest-frame cut start (clip.pick_visual_start): refine the cut entry onto the strongest
        # opening FRAME within a small bounded shift — the top muted-autoplay lever after the text hook
        # (a black/flat/transition opener is the weakest still). DEFAULT ON (the weakest link is closed
        # by default, not by remembering a flag) and FAIL-OPEN: with ffmpeg absent
        # or no strong frame, the start is left exactly as the band/transcript-snap chose it (today's
        # behavior). Only the explicit off-words disable it; the decision is cached per-window so the
        # in-lock commit pass re-spawns no frame-probe ffmpeg (Phase D).
        v = (os.getenv("FANOPS_VISUAL_START") or "").strip().lower()
        return v not in ("0", "false", "no", "off")     # DEFAULT ON; unset/empty/other -> True

    @property
    def smart_framing(self) -> bool:
        # Subject-aware reframe (framing.subject_focus): slide the 9:16 crop onto the detected subject
        # instead of the blind top/center bias. DEFAULT ON — but only because the pass is FAIL-OPEN: with
        # the [framing] extra absent or no subject detected, subject_focus returns None and the render
        # crops centered exactly as today, so default-on is never worse than the old behavior. Only the
        # explicit off-words disable it. Mirrors visual_start (the weakest link closed by default).
        v = (os.getenv("FANOPS_SMART_FRAMING") or "").strip().lower()
        return v not in ("0", "false", "no", "off")     # DEFAULT ON; unset/empty/other -> True

    @property
    def whisper_model(self) -> str:
        # The legacy `whisper` CLI model — used ONLY when faster-whisper (the [asr] extra) is absent.
        # Default "turbo" (fast, good timestamps). Pin a smaller model (e.g. "tiny"/"base") for
        # offline / air-gapped / CI hosts where the larger checkpoints cannot be downloaded.
        v = os.getenv("FANOPS_WHISPER_MODEL")
        return v.strip() if v and v.strip() else "turbo"

    @property
    def asr_model(self) -> str:
        # The faster-whisper (CTranslate2) model. Default "medium" — fast enough to transcribe a long
        # (~26min) source within the whisper timeout on CPU, while still strong on music/rap EN+AR. Pin
        # FANOPS_ASR_MODEL="large-v3" for max accuracy on a fast host, or "small" on a slow one.
        v = os.getenv("FANOPS_ASR_MODEL")
        return v.strip() if v and v.strip() else "medium"

    def asr_model_for(self, duration_seconds: float | None, *, timeout_attempts: int = 0) -> str:
        # Duration-aware ASR selection (MOL-481): an explicit FANOPS_ASR_MODEL pin is the operator's call
        # and wins verbatim; otherwise pick the best model whose expected CPU RTF fits inside the whisper
        # subprocess timeout budget, stepping down large-v3->medium->small->base. timeout_attempts steps
        # down further after prior timeout kills (auto-resume doom-loop mitigation).
        if os.getenv("FANOPS_ASR_MODEL", "").strip(): return self.asr_model
        preferred = "large-v3" if (duration_seconds is not None and duration_seconds <= _ASR_SHORT_SOURCE_SECONDS) else "medium"
        return _pick_timeout_aware_model(duration_seconds, chain=_ASR_MODEL_CHAIN, rtf=_ASR_MODEL_RTF,
                                       preferred=preferred, timeout_attempts=timeout_attempts)

    def whisper_model_for(self, duration_seconds: float | None, *, timeout_attempts: int = 0) -> str:
        # Duration-aware selection for the LEGACY `whisper` CLI fallback (audit c0-f2) — the analog of
        # asr_model_for for the [asr]-extra-absent path (CI / air-gapped). An explicit FANOPS_WHISPER_MODEL
        # pin is the operator's call and wins verbatim; otherwise timeout-aware like asr_model_for.
        if os.getenv("FANOPS_WHISPER_MODEL", "").strip(): return self.whisper_model
        preferred = "large-v3" if (duration_seconds is not None and duration_seconds <= _ASR_SHORT_SOURCE_SECONDS) else "turbo"
        return _pick_timeout_aware_model(duration_seconds, chain=_WHISPER_MODEL_CHAIN, rtf=_WHISPER_MODEL_RTF,
                                       preferred=preferred, timeout_attempts=timeout_attempts)

    @property
    def asr_language(self) -> str:
        # Default "en,ar" — a comma list PINS the candidate languages: the runner enables faster-whisper
        # per-segment detection (multilingual) so English directing lines AND Arabic verses in the SAME
        # source both transcribe. A SINGLE value (e.g. "ar") forces one language; "" = unconstrained auto.
        v = os.getenv("FANOPS_ASR_LANGUAGE")
        return v.strip() if v and v.strip() else "en,ar"

    @property
    def isolate_vocals(self) -> bool:
        # Strip the beat with Demucs BEFORE Whisper (vocals.isolate_vocals) — the single biggest
        # transcription-accuracy lever for music/rap: the instrumental is what wrecks the lyrics, and
        # removing it turned near-gibberish Arabic into coherent lines + fixed clear English errors on
        # real clips. DEFAULT ON; only the explicit off-words "0"/"false"/"no"/"off" disable it.
        # Safe to default ON: if demucs/the [asr] extra isn't installed, isolation FAILS OPEN to the
        # raw audio (today's behavior), so this never breaks a host without Demucs.
        v = os.getenv("FANOPS_ISOLATE_VOCALS")
        return (v or "").strip().lower() not in {"0", "false", "no", "off"}

    @property
    def burn_subs(self) -> bool:
        # Toggle for burning the TRANSCRIPT as captions (clip._subtitles_vf). DEFAULT ON: transcript
        # subs ship live with no operator action; music batches opt out per-batch (Batch.burn_subs=False).
        # The on-screen RETENTION HOOK (m.hook) is a SEPARATE layer that burns regardless of this flag;
        # this only adds the transcript on top. Only the explicit off-words "0"/"false"/"no"/"off"
        # disable it; unset/blank/anything else stays ON. Mirrors isolate_vocals' default-on shape.
        v = os.getenv("FANOPS_BURN_SUBS")
        return (v or "").strip().lower() not in {"0", "false", "no", "off"}

    @property
    def aware_reframe(self) -> bool:
        # Theme 2 (pipeline-quality): bias a VERTICAL height-crop toward the upper third so a subject's
        # head isn't cut by ffmpeg's default centre crop (clip.reframe_filter). OPT-IN/DEFAULT OFF —
        # evidence-gated: the artist's content is predominantly vertical (routes to the non-cropping
        # scale path), so this ships dark until an operator sees the decapitation and enables it. Only
        # the explicit on-words enable it; off -> today's centered reframe, byte-identical. Mirrors burn_subs.
        v = (os.getenv("FANOPS_AWARE_REFRAME") or "").strip().lower()
        return v in {"1", "true", "yes", "on"}

    @property
    def subtitle_font(self) -> str:
        # Operator override for the .ass subtitle font. Default "Arial Unicode MS" — an
        # Arabic-capable face so RTL captions render; change it (FANOPS_SUBTITLE_FONT) if the
        # host lacks that font or the operator prefers another Unicode/Arabic typeface.
        v = os.getenv("FANOPS_SUBTITLE_FONT")
        return v.strip() if v and v.strip() else "Arial Unicode MS"


    @property
    def account_casting(self) -> bool:
        # Account-First Studio: per-account MOMENT casting (Face 3). ON -> each active account is cast its OWN
        # LLM-selected moments (RF1 AccountSelection); crosspost then fans a cast moment ONLY to its accounts.
        # DEFAULT ON (per-account selection is the system's purpose) — set
        # FANOPS_ACCOUNT_CASTING=0 to restore the legacy fan-to-all path. NB the wired LLM path is UNCAPPED by
        # design (the operator does not want output capped for cost); there is no per-account moment budget.
        v = (os.getenv("FANOPS_ACCOUNT_CASTING") or "").strip().lower()
        return v not in ("0", "false", "no", "off")     # DEFAULT ON (per-account selection is the wanted path); explicit off-words disable

    @property
    def hook_router(self) -> bool:
        # M2 structural-hooks router: a read-only Moment classifier (runs BEFORE the render loop) that
        # records hook_strategy and RENDERS NOTHING. DEFAULT OFF (opt-in): observe-only, so the annotation
        # is the SOLE delta and feature-off render/post bytes are byte-identical. Only explicit on-words enable it.
        v = (os.getenv("FANOPS_HOOK_ROUTER") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def impact_cut(self) -> bool:
        # M4 structural-hooks: the impact-cut PRODUCER (suggest plans for router-reserved moments + render
        # operator-approved plans into stitch_draft clips). Per-format gate, DEFAULT OFF (the PRD risk-row
        # "impact-cut family disableable"). The router (hook_router) must also be on for moments to be
        # reserved; with this off the produce path is a no-op (no plans, no stitch renders) -> non-regression.
        v = (os.getenv("FANOPS_IMPACT_CUT") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def intro_tease(self) -> bool:
        # M6 structural-hooks: the intro-tease PRODUCER (an LLM-vision matcher pairs a clean clip with a
        # relevant intro asset, then a compose-prepend renders the "wait for it" tease into a stitch_draft).
        # Per-format gate, DEFAULT OFF (PRD "intro-tease family disableable"). Needs the router on (to reserve
        # clean_awaiting_strategy:intro_tease moments) AND FANOPS_RESPONDER=llm (the matcher is an agent gate);
        # with this off there is no matcher gate and no intro_tease plans/renders -> non-regression.
        v = (os.getenv("FANOPS_INTRO_TEASE") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def variant_learning(self) -> bool:
        # Creative variation v2 (closing the learning loop): with this ON, request_captions biases
        # the next caption toward the per-account hook variant that has earned a TRUSTWORTHY win
        # (>= variant_min_posts analyzed posts AND beating the runner-up by >= variant_min_gap).
        # DEFAULT OFF (opt-in), INDEPENDENT of per-account hook rendering — same off-by-default,
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
        # VALIDATION-FROZEN (Phase 2): this flag = operator INTENT; even ON, apply_variant_amplify stays
        # INERT until `learning_validated` opens — AUTO-stamped by the first real non-degraded live metric
        # (track._auto_validate_metrics_shape), or the optional early `fanops cutover metrics` probe.
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
        # NOT validation-frozen: this is a scorer swap on the SAFE caption-bias READ path (AST-locked to the
        # read/request side by test_ucb_rank_called_only_on_safe_read_or_request_side). Its trust gate is the
        # statistical one (variant_amplify_min_posts/min_gap inside the scorer) + the variant_learning master
        # flag — NOT `learning_validated`. The learning_validated freeze is reserved for the CONSEQUENTIAL
        # actuator that consumes a winner to re-mine a source (variant_amplify.py:166), never the cheap,
        # reversible caption hint. (A degraded/unconfirmed lift can still bias a caption; that is an accepted,
        # low-stakes trade — biasing a caption is reversible, re-mining a source is not.)
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
        # variant_learning and per-account hook rendering. DEFAULT OFF (opt-in), fail-open:
        # unset/empty/other -> today's behavior, no transferred prior injected.
        # VALIDATION-FROZEN (Phase 2): transferring a "proven" style measured on an unconfirmed lift
        # propagates noise across surfaces — stays inert until `learning_validated` opens (AUTO-stamped by
        # the first real non-degraded live metric via track._auto_validate_metrics_shape, or the optional
        # early `fanops cutover metrics` probe).
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
    def adjust_per_surface(self) -> bool:
        # P4(a): with this ON, classify_outcomes ranks WINNERS per (account, platform) surface so a
        # small account's best post can win on its OWN pool instead of being crowded out by a big
        # account's hits. The LOSER side stays GLOBAL regardless (D1) — per-surface logic never
        # re-scopes retirement, so a shared clip another surface won is never retired. DEFAULT OFF
        # (opt-in); unset/empty/other -> today's global ranking, byte-identical.
        v = (os.getenv("FANOPS_ADJUST_PER_SURFACE") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def p4_dim_bias(self) -> bool:
        # P4(b): with this ON, a creative DIM (first_frame_kind | clip_profile) whose higher-reach
        # value clears the per-dim P4 unlock auto-amplifies a representative source (the existing
        # adjust.amplify path), injecting the winning dim as moment-request guidance. AMPLIFY-ONLY,
        # never retires. This touches the amplify/cascade machinery (audit C1), so it is a KILL SWITCH:
        # DEFAULT OFF. VALIDATION-FROZEN (Phase 2): even ON, apply_p4_dim_bias stays INERT until
        # `fanops cutover metrics` confirms the live metrics shape (validation_gate.learning_validated).
        v = (os.getenv("FANOPS_P4_DIM_BIAS") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def timing_bias(self) -> bool:
        # Leg 3 (timing): with this ON, the reach-winning operator-local publish HOUR (once publish_hour
        # clears the per-dim P4 unlock) biases the schedule slot toward it (window-clamped to the account's
        # posting window). A schedule-slot bias, never a publish. KILL SWITCH: DEFAULT OFF. VALIDATION-
        # FROZEN (Phase 2): even ON, apply_timing_bias stays INERT until learning_validated. No hour
        # variance in the published set -> no winner -> no-op (a fixed schedule has nothing to learn).
        v = (os.getenv("FANOPS_TIMING_BIAS") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def ig_retention_proof(self) -> bool:
        # MOL-18c (learning proof, IG-tightening): with this ON, an IG row must carry a present-numeric
        # `retention` to PROVE the shape and auto-unfreeze learning (track._shape_proves_learning) — IG
        # can structurally deliver retention (Meta Graph ig_reels_avg_watch_time), so requiring it holds
        # the IG proof to the full primary set. KILL SWITCH: DEFAULT OFF — the shipped proof exempts
        # EVERYONE from retention (IG included), so requiring it is NEW behavior, opt-in only. FAIL-OPEN:
        # a platform-less/unknown row, or a platform that structurally CAN'T deliver retention (TikTok/
        # youtube), is never gated by this — it proves exactly as today. FREEZE RISK: a REELS row whose
        # duration is unknown yields no derivable retention (_retention_fraction None, post/metrics.py
        # ~:444) — with this ON such an IG post would stop proving until a retention-bearing IG row lands.
        # That is why this is default-off and reversible, not a silent tightening of the live path.
        v = (os.getenv("FANOPS_IG_RETENTION_PROOF") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def moment_hook_learning(self) -> bool:
        # P4(c): with this ON (and the FANOPS_VARIANT_LEARNING master gate on), request_moments feeds
        # the cross-surface union of gated winning hook STYLES into moment_prompt, so the vision hook
        # AUTHOR (not just captions) leans toward what has worked. STYLE cue only ("do NOT copy
        # verbatim"). DEFAULT OFF, fail-open; unset/empty/other -> today's behavior, no block injected.
        v = (os.getenv("FANOPS_MOMENT_HOOK_LEARNING") or "").strip().lower()
        return v in ("1", "true", "yes", "on")          # opt-in; unset/empty/other -> False

    @property
    def p4_min_reach_gap(self) -> float:
        # P4(b) comparative guard: the leading dim value's reach_mean must beat the runner-up's by at
        # least this many impressions before dim_bias_candidates emits it (mirrors best_hooks' min_gap).
        # DEFAULT 0.0 (the per-dim >=8-posts/>=2-values unlock is the real signal floor; the default
        # just trusts the higher-reach ranking — set a positive margin to demand a real lead for your
        # reach scale). A non-float OR NEGATIVE env -> default (a negative gap would emit on no lead at
        # all — guarded exactly like variant_ucb_c).
        try:
            v = float(os.getenv("FANOPS_P4_MIN_REACH_GAP", ""))
        except ValueError:
            return 0.0
        return v if v >= 0 else 0.0

    @property
    def gc_keep_days(self) -> int:
        # Declarative MANUAL-gc retention window (content-lifecycle Phase 3). DEFAULT 30 (today's literal —
        # unchanged when unset). CLAMPED >= 1 (the cmd_gc keep_days<1 reject precedent): a 0/negative window
        # would sweep all reusable renders. Non-int env -> default. NB: a clip whose media_url is still None
        # (cross-account is its FIRST fan-out, Phase 4) needs its .mp4 at publish — set this conservatively.
        try:
            v = int(os.getenv("FANOPS_GC_KEEP_DAYS", "30"))
        except ValueError:
            return 30
        return v if v >= 1 else 30

    @property
    def upload_max_bytes(self) -> int:
        # The Studio upload body ceiling (ING-8). DEFAULT 2048 MB (2 GiB — a long raw clip fits; an abusive
        # body is refused with 413). Configurable via FANOPS_UPLOAD_MAX_MB for a trusted localhost that ingests
        # larger masters. CLAMPED >= 1 MB (a 0/negative cap would refuse every upload). Non-int env -> default.
        try:
            mb = int(os.getenv("FANOPS_UPLOAD_MAX_MB", "2048"))
        except ValueError:
            return 2048 * 1024 * 1024
        return max(1, mb) * 1024 * 1024

    @property
    def operator_tz(self) -> str:
        """M1: the explicit operator timezone string (IANA name, e.g. 'America/New_York') used by
        the timeutil web-boundary helpers to render every scheduled time. DEFAULT 'UTC' — never
        falls through to the server's silent astimezone() default (the M1 root: a server in PST
        rendered every time in PST without labelling it, so the operator's clock was wrong). The
        operator sets this on the Go Live tab. Set via FANOPS_OPERATOR_TZ. Pure read, no I/O."""
        v = (os.getenv("FANOPS_OPERATOR_TZ") or "").strip()
        return v if v else "UTC"

    @property
    def realistic_cadence(self) -> bool:
        """M2: when ON, the per-account spread engine widens the default cadence to a 2-3h
        jittered band (PRD: 'leaning jittered 2-3h for a human feel'). DEFAULT OFF preserves the
        M4 30-min floor — byte-identical to today's behaviour. Mirrors concurrent_sources's
        explicit-on-words pattern. Set via FANOPS_REALISTIC_CADENCE."""
        v = (os.getenv("FANOPS_REALISTIC_CADENCE") or "").strip().lower()
        return v in {"1", "true", "yes", "on"}

    def account_window(self, handle: str) -> "tuple[int, int] | None":
        """M7 seam: the per-account daily posting window (open_hour, close_hour) in operator-local
        hours. Returns None when the account is unknown OR has no daily_window field — None means
        'fully open 24h' per the PRD's default-open contract. The cadence engine reads this to
        avoid laying a post at 03:00 when the account only posts 09:00–23:00. Populated by the
        operator today (a future analytics surface fills it from per-account posting-time
        insights — PRD M7). Reads accounts.json directly so it survives a reload without a
        Config rebuild; fail-open on parse error (no posting window known -> 24h open, never 500)."""
        try:
            import json
            from fanops.models import validate_account_handle
            try:
                handle = validate_account_handle(handle)
            except ValueError:
                pass
            data = json.loads(self.accounts_path.read_text())
            for a in data.get("accounts", []):
                try:
                    ah = validate_account_handle(a.get("handle") or "")
                except ValueError:
                    ah = (a.get("handle") or "").strip()
                if ah == handle:
                    win = a.get("daily_window")
                    if isinstance(win, (list, tuple)) and len(win) == 2:
                        try:
                            return (int(win[0]), int(win[1]))
                        except (ValueError, TypeError):
                            return None
                    return None
            return None
        except (OSError, ValueError):
            return None

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

    @property
    def zernio_max_upload_bytes(self) -> int:
        # Zernio rejects large TikTok uploads with 413 — preflight BEFORE the two-step upload so the
        # operator gets a fast oversize bucket (Sprint 2). DEFAULT 4 MB (live-discovered Zernio 413 ceiling).
        try:
            mb = int(os.getenv("FANOPS_ZERNIO_MAX_UPLOAD_MB", "4"))
        except ValueError:
            mb = 4
        return max(1, mb) * 1024 * 1024

    @property
    def postiz_publish_per_min(self) -> int:
        # Postiz rate-limits bursts (429). Cap publishes per integration per minute (DEFAULT 4).
        # 0 disables the throttle (explicit opt-out).
        try:
            v = int(os.getenv("FANOPS_POSTIZ_PUBLISH_PER_MIN", "4"))
        except ValueError:
            return 4
        return v if v >= 0 else 4

    @property
    def concurrent_sources(self) -> bool:
        # Parallel per-source pipeline (map-parallel / reduce-serial): with this ON, the lock-free
        # pre-warm pass warms each source's slow subprocess artifacts (whisper / ffmpeg signals /
        # ffmpeg render) in a bounded thread pool instead of one-source-at-a-time, so a single long
        # video no longer head-of-line-blocks the whole queue. The same flag fans out the responder's
        # claude -p gate loop. DEFAULT OFF (opt-in) — the byte-identical contract: off -> the EXACT
        # existing sequential path, no pool constructed. Only the explicit on-words enable it; unset,
        # empty, or anything else stays OFF. Mirrors burn_subs. (One writer rule guards correctness,
        # not the flag: workers are pure, the single main transaction is the only ledger writer.)
        v = (os.getenv("FANOPS_CONCURRENT_SOURCES") or "").strip().lower()
        return v in {"1", "true", "yes", "on"}

    @property
    def concurrent_workers(self) -> int:
        # Pool size for concurrent_sources (the source map AND the responder fan-out). DEFAULT 4 — a
        # proven safe concurrent-LLM ceiling, a rate-limit guardrail that
        # caps simultaneous claude -p / whisper / ffmpeg children, NOT a correctness device. CLAMPED
        # >= 1: a pool of 0 would never run a worker and HANG, and a hang is a deadlock-guard violation
        # (the variant_ucb_c clamp precedent). A non-int env falls back to the default rather than
        # crashing an autonomous run.
        try:
            v = int(os.getenv("FANOPS_CONCURRENT_WORKERS", "4"))
        except ValueError:
            return 4
        return v if v >= 1 else 1

    @property
    def poster_backend_raw(self) -> str:
        """The raw FANOPS_POSTER string for diagnostics (half-live hints, go-live scrape) — NOT the
        validated poster_backend (unknown values fall back to dryrun there)."""
        return (os.getenv("FANOPS_POSTER") or "").strip()

    @property
    def postiz_autostart(self) -> bool:
        # Auto-start the local Postiz docker-compose stack before publish (postiz_lifecycle). DEFAULT ON;
        # only explicit off-words disable (mirrors hashtag_trends).
        v = (os.getenv("FANOPS_POSTIZ_AUTOSTART") or "").strip().lower()
        return v not in {"0", "false", "no", "off"}

    @property
    def postiz_compose_dir(self) -> str | None:
        # Where the Postiz docker-compose stack lives (health.ensure_up). Blank -> conventional path.
        v = os.getenv("FANOPS_POSTIZ_COMPOSE_DIR")
        return v.strip() if v and v.strip() else None

    @property
    def whisper_cache_root(self) -> Path:
        # Whisper checkpoint cache root ($XDG_CACHE_HOME/whisper or ~/.cache/whisper).
        base = os.getenv("XDG_CACHE_HOME")
        root = Path(base).expanduser() if base and str(base).strip() else Path.home() / ".cache"
        return root / "whisper"

    def _per_handle_meta_token(self, handle: str) -> str | None:
        """Per-handle META_GRAPH_TOKEN__<SLUG> read — the ONLY home for dynamic Meta token env keys."""
        from fanops.meta_graph import per_account_token_env_key
        from fanops.secret_provider import resolve_secret
        key = per_account_token_env_key(handle)
        if not key: return None
        v = os.getenv(key)
        env_val = v.strip() if v and v.strip() else None
        return resolve_secret(key, env_val)

    def meta_token_for(self, handle: str | None = None) -> str | None:
        """Resolve the Graph access token for `handle` (per-handle .env key wins, else global). SECRET."""
        tok = self.meta_graph_token
        if handle:
            per = self._per_handle_meta_token(handle)
            if per: return per
        return tok

    def meta_token_set_for(self, handle: str) -> bool:
        """Whether a per-handle Graph token is set (BOOL only — never exposes the secret)."""
        return bool(self._per_handle_meta_token(handle))

    def spawn_env(self, *, path: str | None = None) -> dict:
        """Subprocess env for detached fanops children: inherits os.environ (child re-reads .env via
        Config()) with an optional PATH override (daemon kick / install helpers)."""
        env = dict(os.environ)
        if path: env["PATH"] = path
        return env
