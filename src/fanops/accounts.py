"""Flat active-account registry — non-secret metadata only (the hosted-backend account_id is a
non-secret identifier; the API key lives in .env). No lanes: every active account
participates. surfaces() yields each (handle, account_id, platform). resolve_account_id()
maps a handle to its hosted-backend id (FIX F06: v1 passed the handle straight to the backend)."""
from __future__ import annotations
import json
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Optional, NamedTuple
from pydantic import BaseModel, Field
from fanops.config import Config, _LIVE_BACKENDS, _BACKEND_PLATFORMS, FRAMING_NAMES
from fanops.settings import _VALID_BACKENDS
from fanops.errors import ControlFileError, reason as _reason
from fanops.models import Platform, validate_account_handle
from fanops.bands import PROFILE_NAMES                # the valid per-account clip_profile names (M2 length tier)
from fanops.controlio import load_raw_list, write_json_atomic   # shared atomic control-file IO

class AccountStatus(str, Enum):
    planned = "planned"; warming = "warming"; active = "active"; retired = "retired"

def _canonicalize_accounts_raw(raw: dict) -> bool:
    """Rewrite legacy non-canonical account handles in the raw accounts.json dict. Returns True iff changed."""
    changed = False
    for a in raw.get("accounts", []):
        if not isinstance(a, dict): continue
        old = a.get("handle") or ""
        try:
            canon = validate_account_handle(old)
        except ValueError:
            continue
        if canon != old:
            a["handle"] = canon; changed = True
    return changed

class Account(BaseModel):
    handle: str
    account_id: str = ""                   # shared/legacy id (a Postiz integration id, or legacy numeric);
                                           # the FALLBACK when a platform has no per-platform id below
    platforms: list[Platform] = Field(default_factory=list)
    status: AccountStatus = AccountStatus.planned
    access: str = "postiz"                 # METHOD, never a credential
    persona: Optional[str] = None
    persona_id: Optional[str] = None       # A1: link to a first-class Persona (personas.json). When set AND it
                                           # resolves, the linked persona's voice/corpus/levers HYDRATE this
                                           # account in memory at load (_hydrate_from_personas), so the persona
                                           # is the source of truth and an edit takes effect on next load.
                                           # Additive: None / a dangling id -> the inline persona below stands
                                           # (byte-identical to today) — fail-open, never crashes a load.
    clip_profile: Optional[str] = None     # M2 per-account LENGTH tier: short|medium|long (or legacy talk|song).
                                           # None -> Config.resolve_clip_profile falls back to the GLOBAL
                                           # FANOPS_CLIP_PROFILE (byte-identical to today). Additive (empty on
                                           # legacy rows); an unknown value reloads fine and band_for defaults
                                           # it to TALK downstream — fail-open. set_clip_profile is the strict
                                           # WRITE boundary (refuses anything not in bands.PROFILE_NAMES).
    framing: Optional[str] = None          # M2 per-account vertical CROP bias: top|center. None -> Config.
                                           # resolve_top_bias falls back to the GLOBAL aware_reframe (byte-
                                           # identical to today). Additive (empty on legacy rows); an unknown
                                           # value reloads fine and resolve_top_bias ignores it (-> global) —
                                           # fail-open. add_account is the strict WRITE boundary (refuses
                                           # anything not in config.FRAMING_NAMES).
    hashtag_corpus: list[str] = Field(default_factory=list)   # B1: the per-persona curated hashtag pool, HYDRATED in
                                           # memory from the linked Persona at load (never stored on the account row —
                                           # personas.json owns it). Empty on an unlinked account -> vet_hashtags(corpus=[])
                                           # is byte-identical to today. The caption path floats these ahead of the frozen rank
                                           # (M3: the curated corpus is the SOLE per-account hashtag differentiator).
    # Lever engine (M-levers): explicit per-characteristic direction HYDRATED from the linked Persona at load,
    # which personas.compose_persona_instruction renders into the surface `persona` the casting/hook/caption
    # payloads carry. ADDITIVE — empty on every legacy/unlinked account, so compose returns the bare persona
    # voice (byte-identical). content_focus/selection_scope -> casting; hook_angle -> the on-screen hook.
    content_focus: list[str] = Field(default_factory=list)
    selection_scope: Optional[str] = None
    hook_angle: Optional[str] = None
    # M3e: the 3 per-dimension OVERRIDE carriers (casting/hook/caption_directive) were RETIRED with the Persona
    # overrides — the structured levers always compile the directives now; the voice carries freeform register.
    # Provenance (S2): True only when the LINKED persona actually supplied clip_profile (resolved_cut_spec
    # returned a profile at hydration). HYDRATION-ONLY — never written back to accounts.json (set_* mutate the
    # raw dict). Lets the Studio attribute a length to the persona vs the account's own pin truthfully; default
    # False -> attribution falls to the account pin / global (byte-identical when unlinked or persona-cut-silent).
    persona_owns_profile: bool = False
    # Per-platform poster ids keyed by Platform.value (e.g. {"instagram": "ig_1", "tiktok": "tk_9"}).
    # A handle's Instagram and TikTok are DIFFERENT Postiz integrations, so each (handle, platform) must
    # resolve to its OWN id. ADDITIVE: empty on a legacy account, which then resolves via account_id —
    # no migration. A platform absent here falls back to account_id (so a partly-mapped account works).
    integrations: dict[str, str] = Field(default_factory=dict)
    # Per-platform poster BACKEND override keyed by Platform.value (e.g. {"tiktok": "zernio"}). ADDITIVE,
    # empty on every legacy account -> resolve_backend returns None -> the publish loop falls back to the
    # GLOBAL FANOPS_POSTER (byte-identical to today). An override lets IG publish via Postiz while TikTok
    # publishes via Zernio in the SAME run. integrations[platform] still holds the id; this names WHICH
    # backend that id belongs to.
    backends: dict[str, str] = Field(default_factory=dict)
    # Per-account Meta Graph IG Business user id (the audit's per-handle-creds gap): META_IG_USER_ID was a
    # SINGLE GLOBAL credential, so every Graph read (list_user_media / insights / hashtag reads) saw ONE
    # handle regardless of which account a post belonged to. This is a NON-SECRET identifier (exactly like
    # account_id / integrations ids — the IG business id, not a token), so it lives here in accounts.json.
    # ADDITIVE: None on a legacy account -> meta_graph.resolve_meta_creds falls back to the GLOBAL
    # META_IG_USER_ID (byte-identical to a single-account setup). The per-account ACCESS TOKEN is a SECRET
    # and does NOT live here — it rides a per-handle .env key (dual-written like POSTIZ_API_KEY). set_ig_user_id
    # is the strict WRITE boundary.
    ig_user_id: Optional[str] = None

class Surface(NamedTuple):
    account: str
    account_id: str
    platform: Platform

class Accounts:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.accounts: list[Account] = []
        # MOL-79: per-row parse failures collected at load (index + reason). accounts.json is
        # hand-edited by the operator, so ONE stray null / trailing-comma / missing-field row must
        # degrade to "that row skipped" (mirrors the sibling Personas.load leniency) rather than
        # crash the whole registry across the daemon + every Studio page. NOT silent: validate()
        # promotes these to problems so the doctor/health surface names the bad row.
        self.skipped_rows: list[str] = []

    @classmethod
    def load(cls, cfg: Config) -> "Accounts":
        a = cls(cfg)
        p = cfg.accounts_path
        if p.exists():
            text = p.read_text()                       # an I/O error here is a real problem, not "invalid"
            try:
                raw = json.loads(text)                 # a corrupt file (bad JSON) still fails loud
            except Exception as e:
                # Hand-edit typo (the documented "paste account_id, set status:active" step).
                # Clear one-liner instead of a raw traceback.
                raise ControlFileError(f"{p.name} invalid: {_reason(e)}") from e
            # MOL-79: a WRONG TOP-LEVEL SHAPE (a bare list, a string, a null — not the documented
            # {"accounts": [...]} envelope) is NOT a per-row typo; there are no rows to be lenient
            # about. Fail loud like a corrupt file so the operator fixes the envelope, not so raw.get
            # leaks an AttributeError. Per-row leniency (below) only applies inside a valid envelope.
            if not isinstance(raw, dict):
                raise ControlFileError(f"{p.name} invalid: top-level must be an object with an 'accounts' list, got {type(raw).__name__}")
            _canonicalize_accounts_raw(raw)          # M20: in-memory only; mutators write disk canonical handles
            # MOL-79: per-ROW leniency. Build each Account under its own guard so ONE bad row (a
            # null, a trailing-comma artifact, a dict missing a required field) is skipped + recorded
            # while every other account still loads — the whole pipeline/Studio no longer goes down
            # over a single hand-edit typo. Skips surface via validate() -> doctor (never silent).
            for i, x in enumerate(raw.get("accounts", [])):
                try:
                    a.accounts.append(Account(**x))
                except Exception as e:
                    a.skipped_rows.append(f"row {i}: {_reason(e)}")
        _hydrate_from_personas(a, cfg)               # A1: linked accounts read their persona's voice/corpus/levers
        return a

    def active(self) -> list[Account]:
        return [a for a in self.accounts if a.status is AccountStatus.active]

    def resolve_account_id(self, handle: str, platform: Optional[Platform] = None) -> str:
        """The poster id for a handle, per-platform when `platform` is given. Prefers the platform's own
        integrations[platform] id, else the shared account_id fallback (back-compat). A known handle whose
        chosen id is empty fails loud rather than returning "" — an empty id must never reach the poster
        (FIX F06). `platform=None` keeps the legacy handle-only behavior (returns account_id)."""
        try:
            handle = validate_account_handle(handle)
        except ValueError as e:
            raise KeyError(handle) from e
        for a in self.accounts:
            if a.handle == handle:
                chosen = (a.integrations.get(platform.value) if platform else None) or a.account_id
                if not chosen:
                    where = platform.value if platform else "any platform"
                    raise KeyError(f"{handle} has no account_id for {where} (status={a.status.value})")
                return chosen
        raise KeyError(handle)

    def resolve_backend(self, handle: str, platform: Optional[Platform] = None) -> Optional[str]:
        """The per-(handle, platform) poster BACKEND override, or None when unset (the publish loop then
        uses the global FANOPS_POSTER — byte-identical to today). Unknown handle / no override -> None,
        never raises: a missing override is the NORMAL case, not an error. Mirrors resolve_account_id's
        per-platform lookup but with no fallback (the fallback is the GLOBAL backend, applied by the caller)."""
        try:
            handle = validate_account_handle(handle)
        except ValueError:
            return None
        for a in self.accounts:
            if a.handle == handle:
                return a.backends.get(platform.value) if platform else None
        return None

    def effective_provider(self, handle: str, platform: Optional[Platform] = None) -> Optional[str]:
        """The provider that publishes THIS (handle, platform) channel — the publish source of truth (M3):
        the explicit per-channel provider in accounts.json (`backends`), else a BACK-COMPAT bridge to the
        legacy global FANOPS_POSTER (read-only) so the running deployment never goes dark. None when there
        is no explicit provider AND no LIVE legacy global to bridge from -> the publish layer SKIPS the post
        (never silently global-defaults, never fails). The bridge fires ONLY while FANOPS_POSTER names a live
        backend; a NEW deployment (go_live writes FANOPS_LIVE, not FANOPS_POSTER) has none, so an explicit
        provider is REQUIRED there. Retires with the legacy var."""
        explicit = self.resolve_backend(handle, platform)
        if explicit:
            return explicit
        g = self.cfg.poster_backend
        # H2: the legacy bridge is platform-AWARE — only fall back to the global when it actually SERVES
        # this platform (a provider-less TikTok channel must not bridge to an IG-wired Postiz global). A
        # platform-less (legacy handle-only) lookup keeps the old behavior — can't check, so bridge if live.
        if g in _LIVE_BACKENDS and (platform is None or platform.value in _BACKEND_PLATFORMS.get(g, frozenset())):
            return self.cfg.poster_backend                       # legacy bridge: keep the running channels live
        return None

    def live_ready_channels(self) -> list[tuple[str, str, str]]:
        """Active (handle, platform, provider) channels that would ACTUALLY publish once the system is live —
        each one's effective provider (M3) resolves AND that provider's creds are present. This is the
        readiness primitive go_live gates on (flipping live with zero publishable channels would post
        nothing) and the status banner derives its mode label from. Excludes: inactive accounts, channels
        with no provider (no explicit + no live legacy bridge), and providers whose API key is absent.
        Pure reads (no I/O); never raises — a torn registry surfaces upstream via load_accounts_safe."""
        out = []
        for a in self.active():
            for p in a.platforms:
                prov = self.effective_provider(a.handle, p)
                if prov and self.cfg.backend_has_creds(prov):
                    out.append((a.handle, p.value, prov))
        return out

    def validate(self) -> list[str]:
        """Config problems to surface before a run. Per-platform: each active account's every platform
        must resolve to an id (its integrations[platform] OR the shared account_id) — so a multi-platform
        handle with one channel unmapped is flagged by name, while a legacy single-account_id account
        still passes via the fallback. R2/D5/D15: also rejects the drift state where ONE side of the
        per-platform routing pair is set without the other — integrations[p] set + backends[p] unset
        (or vice versa) silently fell back to the legacy FANOPS_POSTER=dryrun bridge on a 'live' config
        (the cisumwolfhom incident). The structural rule closes the bad path at go_live time."""
        problems = []
        # MOL-79: a per-row parse skip at load is a config-integrity problem, surfaced HERE so the
        # doctor/health screen names the malformed row (validate() is what doctor renders). Without
        # this the skipped account would vanish silently — worse than the loud crash it replaced.
        for s in self.skipped_rows:
            problems.append(f"accounts.json {s} — malformed, skipped (fix the row in the Studio Go-Live tab or accounts.json)")
        for a in self.active():
            if not a.platforms:
                problems.append(f"active account {a.handle} has no platforms")
            for p in a.platforms:
                if not (a.integrations.get(p.value) or a.account_id):
                    problems.append(f"active account {a.handle} has no account_id for {p.value}")
                # R2/D5/D15: per-platform routing pair must be atomically set or atomically unset.
                # Either side present without the other is the drift state — the legacy bridge
                # would silently route the channel to dryrun. Refuse it.
                has_integ = bool(a.integrations.get(p.value))
                has_backend = bool(a.backends.get(p.value))
                if has_integ and not has_backend:
                    problems.append(f"{a.handle}/{p.value}: integration id set without a backend — "
                                    f"set backends.{p.value} or clear integrations.{p.value} "
                                    f"(R2/D5: would silently route to the legacy FANOPS_POSTER bridge)")
                elif has_backend and not has_integ:
                    problems.append(f"{a.handle}/{p.value}: backend set without an integration id — "
                                    f"set integrations.{p.value} or clear backends.{p.value} "
                                    f"(R2/D4: backend would have no id to publish through)")
        seen = set()
        for a in self.accounts:
            if a.handle in seen:
                problems.append(f"duplicate handle {a.handle} (handles must be unique)")
            seen.add(a.handle)
        if self.cfg.account_casting and any((a.persona_id or a.persona or getattr(a, "tag_lean", None)) for a in self.active()):
            from fanops.bands import band_for
            g_band = band_for(self.cfg.clip_profile); g_frame = self.cfg.aware_reframe
            for a in self.active():
                if not ((a.persona_id or "").strip() or (a.persona or "").strip()):
                    problems.append(f"{a.handle}: no persona linked — per-account hooks/cuts need a persona")
                    continue
                prof = self.cfg.resolve_clip_profile(a); tb = self.cfg.resolve_top_bias(a)
                if band_for(prof) == g_band and tb == g_frame:
                    problems.append(f"{a.handle}: cut spec matches global ({prof}) — will shared-cut without a length/framing diff")
        return problems

    def surfaces(self) -> list[Surface]:
        # Each (handle, platform) carries its OWN poster id: the platform's integrations id, else the
        # shared account_id fallback — so a multi-platform handle posts each platform to its own channel.
        return [Surface(a.handle, a.integrations.get(p.value) or a.account_id, p)
                for a in self.active() for p in a.platforms]


def _persona_for_account(acc: Account, reg) -> "object | None":
    """Resolve the Persona record for `acc`: explicit persona_id first, else an exact inline-voice match to a
    first-class Persona (so brief-seeded inline strings hydrate cut/levers WITHOUT a persisted link). Pure read."""
    pid = (acc.persona_id or "").strip()
    if pid:
        per = reg.get(pid)
        if per is not None:
            return per
    voice = (acc.persona or "").strip()
    if voice:
        return next((p for p in reg.all() if (getattr(p, "voice", None) or "").strip() == voice), None)
    return None


def _hydrate_from_personas(accts: "Accounts", cfg: Config) -> None:
    """A1: override each LINKED account's persona voice, corpus, levers (content_focus/selection_scope/hook_angle), cut spec (clip_profile/framing), and per-dimension directives IN MEMORY from its Persona (the source of truth
    once linked), so every consumer reading a.persona sees the persona's value and an operator edit takes
    effect on the next load — with ZERO consumer rewiring. FAIL-OPEN: no personas.json, a dangling persona_id,
    or any error leaves the account's inline values exactly as today (byte-identical when unlinked). The
    personas import is lazy (personas imports accounts in migrate -> avoid a cycle). Voice-match: an unlinked
    account whose inline persona equals a Persona.voice still hydrates (derived cut spec + levers) in memory."""
    try:
        from fanops.personas import Personas, resolved_cut_spec
        reg = Personas.load(cfg)
    except Exception:
        return                                       # corrupt/absent personas.json -> inline values stand
    for acc in accts.accounts:
        per = _persona_for_account(acc, reg)
        if per is None:
            continue                                 # no link + no voice match -> inline values stand
        if per.voice:
            acc.persona = per.voice                  # the persona owns the voice (empty voice -> keep inline)
        acc.hashtag_corpus = list(per.hashtag_corpus)   # B1: the persona owns the curated corpus (the caption path reads it; M3 — the sole hashtag differentiator)
        # Lever engine: the persona owns each lever (empty -> compose ignores it -> byte-identical). clip_profile/
        # framing override the account's own ONLY when the persona pins them (else the account/global default stands).
        acc.content_focus = list(per.content_focus)
        acc.selection_scope = per.selection_scope
        acc.hook_angle = per.hook_angle
        _prof, _fr = resolved_cut_spec(per)   # P2: derived from content_focus; else None (global stands)
        if _prof: acc.clip_profile = _prof; acc.persona_owns_profile = True   # S2 provenance: the persona TRULY owns the length
        if _fr: acc.framing = _fr
        # M3e: the per-dimension directive OVERRIDES were retired — nothing to hydrate; the structured levers
        # (content_focus/selection_scope/hook_angle) above always compile the directives, the voice carries the register.


def link_persona(cfg: Config, handle: str, persona_id: str) -> str:
    """Link ONE account to a first-class Persona (set persona_id) atomically — the A2 connect control. A
    BLANK persona_id CLEARS the link (-> the account's inline persona stands again). Scans ALL
    rows (dup-handle safety, mirrors set_status); preserves every sibling + unknown field. Unknown
    handle -> KeyError (caller -> clean ActionResult). Does NOT validate the id exists (a dangling link
    fails open at load) — the Studio resolves the id from the live registry before calling."""
    handle = validate_account_handle(handle)
    pid = (persona_id or "").strip()
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        found = False
        for a in accounts:
            if isinstance(a, dict) and a.get("handle") == handle:
                a["persona_id"] = pid or None; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def load_accounts_safe(cfg: Config) -> tuple["Accounts", Optional[str]]:
    """Accounts.load that NEVER raises — for the READ paths (metrics/reconcile per-post backend routing)
    that must degrade to the global backend on a corrupt accounts.json rather than crash. Publish already
    surfaces the corruption loudly (run.py Accounts.load raises ControlFileError), so a read path failing
    hard would be a redundant new crash. Returns (accounts, error_or_None); on failure an EMPTY registry
    (every handle then resolves to no override -> the global backend) plus the truncated error to log."""
    try:
        return Accounts.load(cfg), None
    except Exception as e:
        return Accounts(cfg), str(e)[:160]


def _load_raw_accounts(p: Path) -> tuple[dict, list]:
    """Read accounts.json as the RAW parsed dict (absent file -> empty registry) and return (raw, the
    accounts list). Mutating the raw dict — not Account.model_dump() — is how every writer preserves
    unknown/future fields and sibling accounts exactly. A non-list 'accounts' is a corrupt file."""
    return load_raw_list(p, "accounts")


@contextmanager
def _accounts_txn(cfg: Config):
    """Serialize a mutator's READ-modify-write under cfg.accounts_lock_path so two concurrent Studio/
    daemon writers can't lost-update (the _load_raw_accounts MUST run INSIDE this lock — reading outside
    it is the lost-update window). Reuses the proven fcntl flock helper; the import is LAZY so there is no
    module-load cycle (ledger never imports accounts — verified one-way)."""
    from fanops.ledger import _file_lock
    with _file_lock(cfg.accounts_lock_path):
        yield


def write_integration(cfg: Config, handle: str, platform: str, integration_id: str | int) -> str:
    """Map ONE (handle, platform) channel to its own poster id: set integrations[platform] = id in
    accounts.json atomically — the per-platform Go-Live mapping that replaces hand-editing JSON, so a
    handle's Instagram and TikTok point at their DIFFERENT Postiz integrations. Creates the integrations
    sub-dict if absent; preserves every sibling account, unknown field, and other platform's id. The id
    is coerced to str. Unknown handle -> KeyError (caller -> clean ActionResult); unknown platform ->
    ValueError (defense-in-depth at the boundary: never write a key that can't match a Platform.value)."""
    handle = validate_account_handle(handle)
    platform = getattr(platform, "value", platform)              # accept a Platform enum or its value string
    if platform not in {pf.value for pf in Platform}:
        raise ValueError(f"unknown platform: {platform!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        found = False
        for a in accounts:                                       # scan ALL rows (no break): mirror set_status/
            if isinstance(a, dict) and a.get("handle") == handle:  # remove_account so a hand-edited duplicate handle
                integ = a.get("integrations")                     # maps consistently across EVERY copy, not just the
                if not isinstance(integ, dict): integ = {}        # first (handles SHOULD be unique — add_account
                integ[str(platform)] = str(integration_id)        # rejects dupes — but a hand-edit must not diverge)
                a["integrations"] = integ; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def set_backend(cfg: Config, handle: str, platform: str, backend: str) -> str:
    """Set or CLEAR ONE (handle, platform) channel's poster BACKEND override in accounts.json atomically —
    the per-account routing knob (e.g. route @tk's tiktok to "zernio" while the global stays "postiz").
    A blank or "default" backend CLEARS the override (-> falls back to the global FANOPS_POSTER). Validates
    at the control-file boundary: platform a known Platform.value (ValueError), backend a known poster
    backend (ValueError) — never write a key/value that won't reload. Scans ALL rows (dup-handle safety,
    mirrors write_integration); preserves every sibling, unknown field, and the account's other fields.
    Unknown handle -> KeyError (caller -> clean ActionResult). The id itself stays in integrations[platform]."""
    handle = validate_account_handle(handle)
    platform = getattr(platform, "value", platform)              # accept a Platform enum or its value string
    if platform not in {pf.value for pf in Platform}:
        raise ValueError(f"unknown platform: {platform!r}")
    backend = (backend or "").strip().lower()
    clearing = backend in ("", "default")
    if not clearing and backend not in _VALID_BACKENDS:
        raise ValueError(f"unknown backend: {backend!r} (valid: {', '.join(sorted(_VALID_BACKENDS))})")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        found = False
        for a in accounts:                                       # scan ALL rows (dup-handle safety)
            if isinstance(a, dict) and a.get("handle") == handle:
                # M4: refuse routing a platform the account doesn't carry (a config error -> never silently
                # written). Clearing an override is always allowed (it may be removing a stale one).
                if not clearing and platform not in (a.get("platforms") or []):
                    raise ValueError(f"{handle} does not carry {platform!r} — add the platform first")
                bk = a.get("backends")
                if not isinstance(bk, dict): bk = {}
                if clearing: bk.pop(str(platform), None)
                else: bk[str(platform)] = backend
                a["backends"] = bk; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def add_account(cfg: Config, handle: str, platforms: list, persona: str = "",
                status: str = "active", access: str = "postiz",
                clip_profile: str = "", framing: str = "") -> str:
    """Onboard a BRAND-NEW account into accounts.json atomically — so the Go-Live tab adds an account
    WITHOUT the operator hand-editing JSON. Validates at this control-file boundary: a non-blank handle and
    every platform a known Platform value (never write an account that won't reload). Rejects a duplicate
    handle. New accounts default to status=active (so they appear in the mapping list at once) and
    access=postiz; account_id stays empty — the per-platform ids are set afterward via write_integration /
    the mapping UI. Returns the handle; raises ValueError on bad input. (M3: tag_lean retired — a linked
    persona's curated hashtag_corpus is the per-account hashtag differentiator.)"""
    handle = validate_account_handle(handle)
    plats = [getattr(x, "value", x) for x in platforms]      # accept Platform enums or value strings
    valid = {pf.value for pf in Platform}
    bad = [x for x in plats if x not in valid]
    if bad:
        raise ValueError(f"unknown platform(s): {', '.join(map(str, bad))}")
    prof = (clip_profile or "").strip().lower()
    if prof and prof not in PROFILE_NAMES:
        raise ValueError(f"unknown clip_profile: {clip_profile!r}")
    fr = (framing or "").strip().lower()
    if fr and fr not in FRAMING_NAMES:
        raise ValueError(f"unknown framing: {framing!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        if any(isinstance(a, dict) and a.get("handle") == handle for a in accounts):
            raise ValueError(f"duplicate handle {handle} (already exists)")
        accounts.append({"handle": handle, "account_id": "", "platforms": plats,
                         "status": str(status), "access": str(access),
                         "persona": persona or "",
                         "clip_profile": prof or None, "framing": fr or None, "integrations": {}})
        write_json_atomic(p, raw)
    return handle


def ensure_channel(cfg: Config, handle: str, platform: str, persona: str = "") -> bool:
    """Idempotently ensure a (handle, platform) channel EXISTS in accounts.json atomically — the adopt-side
    primitive (M4 discover→adopt): a discovered remote channel becomes an onboardable account+platform in
    ONE write. If the handle is absent → append a NEW account (status active, [platform], empty account_id —
    born inert; the id is mapped next via write_integration). If the handle EXISTS but lacks this platform →
    append the platform (preserving every other field, sibling, and integration). If it already has the
    platform → no-op. persona SEEDS a NEW account only — IGNORED when the handle already exists (an existing
    account keeps its own persona; change it via set_persona), so adopting a second platform never clobbers an
    account's persona. Scans ALL rows (no break) so a hand-edited duplicate handle gains the platform on EVERY
    copy — consistent with write_integration/set_backend, which adopt calls next. Validates handle non-blank +
    platform a known Platform.value at the control-file boundary. Returns True iff it changed accounts.json.
    Unlike add_account it NEVER raises on a duplicate handle (idempotent by design); raises ValueError only on
    bad input. (M3: tag_lean retired.)"""
    handle = validate_account_handle(handle)
    platform = getattr(platform, "value", platform)              # accept a Platform enum or its value string
    if platform not in {pf.value for pf in Platform}:
        raise ValueError(f"unknown platform: {platform!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        found = changed = False
        for a in accounts:                                       # scan ALL rows (dup-handle safety; mirrors write_integration)
            if isinstance(a, dict) and a.get("handle") == handle:
                found = True
                plats = a.get("platforms")
                if not isinstance(plats, list): plats = []
                if platform not in plats:
                    plats.append(platform); a["platforms"] = plats; changed = True
        if not found:
            accounts.append({"handle": handle, "account_id": "", "platforms": [platform],
                             "status": "active", "access": "postiz",
                             "persona": (persona or "").strip(), "integrations": {}})
            changed = True
        if changed:
            write_json_atomic(p, raw)
        return changed


def set_status(cfg: Config, handle: str, status: str) -> str:
    """Change ONE account's status atomically (the Go-Live DEMOTE control — e.g. an active placeholder ->
    planned, so it leaves active() and the publishing fan-out without losing its row). Validates status at
    the control-file boundary (must be an AccountStatus value — never write a status that won't reload);
    preserves every sibling, unknown field, and the account's own other fields. Unknown handle -> KeyError."""
    handle = validate_account_handle(handle)
    status = getattr(status, "value", status)                    # accept an AccountStatus enum or its value
    if status not in {s.value for s in AccountStatus}:
        raise ValueError(f"unknown status: {status!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        found = False
        for a in accounts:                                       # scan ALL rows (no break): a hand-edited file with
            if isinstance(a, dict) and a.get("handle") == handle:  # duplicate handles must not leave a 2nd copy active —
                a["status"] = str(status); found = True          # mirrors remove_account dropping every match
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def set_clip_profile(cfg: Config, handle: str, profile: str) -> str:
    """Set or clear ONE account's clip_profile atomically (the M2 per-account LENGTH control). A blank
    profile CLEARS it (-> None -> resolve_clip_profile falls back to the global FANOPS_CLIP_PROFILE).
    Validates a non-blank profile at the control-file boundary (must be a known bands.PROFILE_NAMES value
    — never write a profile that only band_for's default would catch); preserves every sibling, unknown
    field, and the account's own other fields; scans ALL rows (dup-handle safety, mirrors set_status).
    Unknown handle -> KeyError."""
    handle = validate_account_handle(handle)
    profile = (profile or "").strip().lower()
    if profile and profile not in PROFILE_NAMES:
        raise ValueError(f"unknown clip_profile: {profile!r} (valid: {', '.join(sorted(PROFILE_NAMES))})")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        found = False
        for a in accounts:
            if isinstance(a, dict) and a.get("handle") == handle:
                a["clip_profile"] = profile or None; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def set_ig_user_id(cfg: Config, handle: str, ig_user_id: str) -> str:
    """Set or clear ONE account's per-account Meta IG Business user id atomically (the Go-Live per-account
    Meta credential control). A blank id CLEARS it (-> None -> meta_graph.resolve_meta_creds falls back to
    the GLOBAL META_IG_USER_ID). The id is a NON-SECRET identifier (like account_id / integrations ids) so
    it belongs in accounts.json; the per-account ACCESS TOKEN is a SECRET written separately to a per-handle
    .env key (never here). Preserves every sibling, unknown field, and the account's own other fields; scans
    ALL rows (dup-handle safety, mirrors set_persona). Unknown handle -> KeyError (caller -> clean
    ActionResult)."""
    handle = validate_account_handle(handle)
    ig_user_id = (ig_user_id or "").strip()
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        found = False
        for a in accounts:
            if isinstance(a, dict) and a.get("handle") == handle:
                a["ig_user_id"] = ig_user_id or None; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def set_persona(cfg: Config, handle: str, persona: str) -> str:
    """Set or clear ONE account's persona atomically (the Go-Live persona editor). A blank persona CLEARS it
    (-> ""). Preserves every sibling, unknown field, and the account's other fields; scans ALL rows (dup-handle
    safety, mirrors set_status). Unknown handle -> KeyError."""
    handle = validate_account_handle(handle)
    persona = (persona or "").strip()
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        found = False
        for a in accounts:
            if isinstance(a, dict) and a.get("handle") == handle:
                a["persona"] = persona; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def remove_account(cfg: Config, handle: str) -> str:
    """Remove ONE account from accounts.json atomically (the Go-Live REMOVE control — clears a placeholder
    like @TBD-1 that the UI couldn't delete before, only hand-editing JSON could). Drops only the matching
    dict; preserves every sibling + unknown field; an empty registry stays valid. Unknown handle -> KeyError
    (caller -> clean ActionResult)."""
    handle = validate_account_handle(handle)
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        _canonicalize_accounts_raw(raw)
        kept = [a for a in accounts if not (isinstance(a, dict) and a.get("handle") == handle)]
        if len(kept) == len(accounts):
            raise KeyError(handle)
        raw["accounts"] = kept
        write_json_atomic(p, raw)
    return handle
