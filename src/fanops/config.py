# src/fanops/config.py
"""Filesystem layout + env. Never stores a secret in code; reads .env at runtime.
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
from dotenv import load_dotenv

_log = logging.getLogger("fanops.config")

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
# poster_backend. dryrun = posts nothing; postiz = free self-hosted; rest/mcp = Blotato (being retired).
PosterBackend = Literal["dryrun", "postiz", "zernio", "rest", "mcp"]
_VALID_BACKENDS = frozenset({"dryrun", "postiz", "zernio", "rest", "mcp"})
# Live (real-posting) backends: a per-account backend override pointing at one of these is a real
# "go live for this account" and must be creds-gated + confirmed, like the global go_live (dryrun isn't).
_LIVE_BACKENDS = frozenset({"postiz", "zernio", "rest", "mcp"})

# M2 per-account FRAMING values (Account.framing): the vertical crop bias for the account's render CUT.
# "top" -> head-safe upper-third crop (reframe_filter top_bias=True), "center" -> default centred crop.
# The strict WRITE boundary (set_framing/add_account refuse anything else); resolve_top_bias maps these to
# the bool top_bias, falling back to the GLOBAL aware_reframe for None/blank/unknown (validate-or-default).
FRAMING_NAMES = frozenset({"top", "center"})

# Which PLATFORMS each live backend serves in THIS deployment. Used ONLY to bound the legacy
# FANOPS_POSTER bridge (accounts.effective_provider): a provider-less channel never falls back to a
# global that doesn't post its platform (H2 — e.g. a TikTok channel must not bridge to the IG-wired
# Postiz global, which would publish to the wrong provider/integration or burn the post). The explicit
# per-channel `backends` override ALWAYS wins first, so this only narrows the back-compat fallback;
# Blotato (rest/mcp) historically served every platform.
_BACKEND_PLATFORMS = {
    "postiz": frozenset({"instagram"}),
    "zernio": frozenset({"tiktok"}),
    "rest":   frozenset({"instagram", "tiktok", "youtube", "facebook", "twitter"}),
    "mcp":    frozenset({"instagram", "tiktok", "youtube", "facebook", "twitter"}),
}

# Per-gate model tier (llm_model_for): M1b splits the moment gate. `moments` (pass 1) chooses the
# WINDOWS; `moment_hooks` (pass 2) is the CREATIVE VISION hook AUTHOR — it SEES the picked window's
# frames and writes the on-screen retention hook (the watch-through driver). BOTH -> opus (picking
# quality unchanged + the hook is the operator's #1 ask). `captions` (hashtags only) stays MECHANICAL
# -> sonnet. FANOPS_LLM_MODEL overrides all.
_GATE_MODEL_DEFAULTS = {"moments": "opus", "moment_hooks": "opus", "captions": "sonnet"}

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
        self.accounts_lock_path = self.control / "accounts.lock"   # serializes the accounts.json read-modify-write mutators
        self.context_path = self.control / "context.md"
        self.tuning_path = self.control / "tuning.json"
        self.hashtags_path = self.control / "hashtags.json"  # M4 dynamic reach-ranked tag store; absent -> frozen pools
        self.hashtag_budget_path = self.control / "hashtag_budget.json"  # M4 Meta Graph 30/7-day search budget counter
        self.cutover_path = self.control / "cutover.json"   # live-cutover harness scratch state; NEVER the ledger
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
        return _sanitize_tuning(raw)                         # warn+drop invalid entries, keep good ones

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
    def poster_backend(self) -> PosterBackend:
        # THE poster mode. An UNKNOWN/typo'd value (e.g. FANOPS_POSTER=positz) must NOT present as live:
        # get_poster falls back to DryRunPoster for any unrecognized backend, so a typo would otherwise
        # show a LIVE banner while posting NOTHING (W4). Validate against the known set and fall back to
        # dryrun + warn — the variant_ucb_c validate-or-default posture (never crash an autonomous run
        # over a bad env). Surrounding whitespace trimmed (a .env value can carry a trailing newline).
        v = (os.getenv("FANOPS_POSTER") or "").strip()
        if not v:
            return "dryrun"
        if v not in _VALID_BACKENDS:
            _log.warning("ignoring unknown FANOPS_POSTER=%r (using dryrun); valid: %s",
                         v, ", ".join(sorted(_VALID_BACKENDS)))
            return "dryrun"
        return v

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
    def postiz_url(self) -> str | None:
        # Base URL of a self-hosted (or hosted) Postiz instance, e.g. https://postiz.example.com or
        # https://api.postiz.com. The free, non-Blotato poster backend (FANOPS_POSTER=postiz) posts
        # to {postiz_url}/public/v1/... . Trailing slash trimmed by the poster.
        v = os.getenv("POSTIZ_URL")
        return v.strip() if v and v.strip() else None

    @property
    def postiz_api_key(self) -> str | None:
        # Postiz public API key (Settings > Developers > Public API), sent as the Authorization
        # header. Distinct from BLOTATO_API_KEY — a Postiz deployment needs neither a Blotato account
        # nor key. is_live_backend is True for a postiz backend WITH this key (M2): postiz both
        # PUBLISHES and now feeds the learning loop via its post analytics (PostizMetricsClient).
        v = os.getenv("POSTIZ_API_KEY")
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
        # backend WITH this key. Distinct from POSTIZ/BLOTATO keys — they coexist (per-account routing
        # can run IG via Postiz AND TikTok via Zernio at once).
        v = os.getenv("ZERNIO_API_KEY")
        return v.strip() if v and v.strip() else None

    @property
    def meta_graph_token(self) -> str | None:
        # Meta Graph API access token (IG Business) for the M4 hashtag TREND sampling. WRITE-ONLY —
        # never logged/echoed (mirrors postiz_api_key); meta_graph sends it as the access_token param.
        # Absent -> trend sampling fails open to own-reach-only ranking. Used ONLY by `hashtags refresh`,
        # never on the publish path.
        v = os.getenv("META_GRAPH_TOKEN")
        return v.strip() if v and v.strip() else None

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
        # M4 opt-in: sample LIVE Meta Graph hashtag trends during `hashtags refresh`. DEFAULT OFF —
        # own-reach ranking needs no token; trends need a wired Meta app + the 30/7-day budget. Only the
        # explicit on-words enable it; off -> own-reach-only refresh, today's behavior. Mirrors burn_subs.
        v = (os.getenv("FANOPS_HASHTAG_TRENDS") or "").strip().lower()
        return v in {"1", "true", "yes", "on"}

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
        # (M2): a postiz deployment is live on POSTIZ_API_KEY; a Blotato (rest/mcp) deployment on
        # BLOTATO_API_KEY; dryrun (or any unrecognized backend) is never live. NB: this gates the
        # learn/reconcile passes — the Blotato status reconciler (pipeline.py) further restricts itself
        # to rest/mcp, and the speculative actuators stay frozen by learning_validated until cutover.
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
            return False                                # torn registry + no global creds -> not provably live
        return bool(accounts.live_ready_channels())

    def backend_has_creds(self, backend: str) -> bool:
        # Does THIS backend have the credential to post live? Per-account routing (Zernio slice 2) asks
        # this about a per-post backend that may differ from the global poster_backend, so the live check
        # is one reusable home keyed by backend name (not just self.poster_backend). postiz->POSTIZ_API_KEY,
        # zernio->ZERNIO_API_KEY, rest/mcp(Blotato)->BLOTATO_API_KEY; dryrun/unknown -> never live.
        if backend == "postiz": return bool(self.postiz_api_key)
        if backend == "zernio": return bool(self.zernio_api_key)
        if backend in ("rest", "mcp"): return bool(self.blotato_api_key)
        return False                                    # dryrun / anything unrecognized

    @property
    def responder_mode(self) -> str:
        return os.getenv("FANOPS_RESPONDER") or "manual"

    def llm_model_for(self, kind: str) -> str:
        # V2 M1/F1: the creative brain stays PINNED (an unpinned `claude -p` drifts with the CLI default).
        # But the tier is now PER-GATE, not one blanket "opus": the MECHANICAL gates — moment-window picks
        # and hashtags-only captions — run on `sonnet` (fast + plenty for the task; blanket-opus made a
        # 28-source run ~40min of SEQUENTIAL calls). The CREATIVE gate — `moments`, the VISION author of
        # the on-screen RETENTION hook (the watch-through driver) — stays on `opus`.
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
        # Opt-in toggle for burning the TRANSCRIPT as captions (clip._subtitles_vf). DEFAULT OFF:
        # captioning what the audio already says is redundant AND only as good as the unreliable
        # auto-transcription — fine for talking-head content, wrong for music. The on-screen
        # RETENTION HOOK (m.hook) is a SEPARATE layer that burns by default regardless of this flag;
        # this only adds the transcript on top. Only the explicit on-words "1"/"true"/"yes"/"on"
        # enable it; unset/blank/anything else stays OFF. Mirrors creative_variation's opt-in shape.
        v = (os.getenv("FANOPS_BURN_SUBS") or "").strip().lower()
        return v in {"1", "true", "yes", "on"}

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
    def creative_variation(self) -> bool:
        # Per-account creative variation: each active account gets a genuinely different caption +
        # burned-in on-screen hook per clip (+ its own length/framing cut under M2). M3d (2026-06-23):
        # DEFAULT ON — per-account differentiation is the system's purpose, not an opt-in. The OFF code
        # path is RETAINED (§7 firewall: only the default resolution flipped) so an operator can pin the
        # legacy fan-to-all single-clip behavior with FANOPS_CREATIVE_VARIATION=0. The approve-with-hook
        # moment-restore flow is an OFF-mode feature (per-surface hooks own the burn when ON) — Review
        # hides that choice while ON. Mirrors visual_start/isolate_vocals' default-ON shape.
        v = (os.getenv("FANOPS_CREATIVE_VARIATION") or "").strip().lower()
        return v not in ("0", "false", "no", "off")     # DEFAULT ON; only explicit off-words disable it

    @property
    def account_casting(self) -> bool:
        # Account-First Studio: per-account MOMENT casting (Face 3). ON -> each active account is cast up to
        # cast_pick_budget of its best persona-fit moments (bounded by the batch target); crosspost then fans
        # a cast moment ONLY to its accounts. DEFAULT OFF (opt-in, mirrors creative_variation) — a fan-out-
        # changing selection layer ships behind a kill-switch; unset/empty/other stays OFF (today's behavior).
        v = (os.getenv("FANOPS_ACCOUNT_CASTING") or "").strip().lower()
        return v not in ("0", "false", "no", "off")     # DEFAULT ON (per-account selection is the wanted path); explicit off-words disable

    @property
    def cast_pick_budget(self) -> int:
        # Per-account max moments cast per pass (Face 3, budget mode). DEFAULT 6, CLAMPED >= 1 (a 0 budget casts
        # nothing; the concurrent_workers clamp precedent). A non-int env falls back to the default, never
        # crashing a run.
        try:
            v = int(os.getenv("FANOPS_CAST_PICK_BUDGET", "6"))
        except ValueError:
            return 6
        return v if v >= 1 else 1

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
        # VALIDATION-FROZEN (Phase 2): this flag = operator INTENT; even ON, apply_variant_amplify stays
        # INERT until `fanops cutover metrics` confirms lift_score's field shape against a real row
        # (validation_gate.learning_validated) — re-mining on unvalidated lift is the over-build trap.
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
        # VALIDATION-FROZEN (Phase 2): a bandit allocating over lift_scores whose live field shape is
        # unconfirmed is theater — do NOT enable until `fanops cutover` reconciles a real metrics row.
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
        # VALIDATION-FROZEN (Phase 2): transferring a "proven" style measured on an unconfirmed lift
        # propagates noise across surfaces — do NOT enable until `fanops cutover` confirms the fields.
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
        # proven safe concurrent-opus ceiling, a rate-limit guardrail that
        # caps simultaneous claude -p / whisper / ffmpeg children, NOT a correctness device. CLAMPED
        # >= 1: a pool of 0 would never run a worker and HANG, and a hang is a deadlock-guard violation
        # (the variant_ucb_c clamp precedent). A non-int env falls back to the default rather than
        # crashing an autonomous run.
        try:
            v = int(os.getenv("FANOPS_CONCURRENT_WORKERS", "4"))
        except ValueError:
            return 4
        return v if v >= 1 else 1
