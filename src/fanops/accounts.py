"""Flat active-account registry — non-secret metadata only (the Blotato account_id is a
non-secret identifier; the API key lives in .env). No lanes: every active account
participates. surfaces() yields each (handle, account_id, platform). resolve_account_id()
maps a handle to its numeric Blotato id (FIX F06: v1 passed the handle straight to Blotato)."""
from __future__ import annotations
import json
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Optional, NamedTuple
from pydantic import BaseModel, Field
from fanops.config import Config, _VALID_BACKENDS, _LIVE_BACKENDS, _BACKEND_PLATFORMS, FRAMING_NAMES
from fanops.errors import ControlFileError, reason as _reason
from fanops.models import Platform
from fanops.hashtags import TAG_LEANS                 # the valid per-account tag_lean names (persona diff)
from fanops.bands import PROFILE_NAMES                # the valid per-account clip_profile names (M2 length tier)
from fanops.controlio import load_raw_list, write_json_atomic   # shared atomic control-file IO

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
    persona_id: Optional[str] = None       # A1: link to a first-class Persona (personas.json). When set AND it
                                           # resolves, the linked persona's voice/tag_lean HYDRATE this account
                                           # in memory at load (_hydrate_from_personas), so the persona is the
                                           # source of truth and an edit takes effect on next load. Additive:
                                           # None / a dangling id -> the inline persona/tag_lean below stand
                                           # (byte-identical to today) — fail-open, never crashes a load.
    tag_lean: Optional[str] = None         # persona TAG knob: tasteful|underground|bold (None -> no lean).
                                           # Additive (empty on legacy rows). An unknown value reloads fine
                                           # and is inert (vet_hashtags treats it as no-lean) — fail-open.
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
                                           # fail-open. set_framing is the strict WRITE boundary (refuses
                                           # anything not in config.FRAMING_NAMES).
    hashtag_corpus: list[str] = Field(default_factory=list)   # B1: the per-persona curated hashtag pool, HYDRATED in
                                           # memory from the linked Persona at load (never stored on the account row —
                                           # personas.json owns it). Empty on an unlinked account -> vet_hashtags(corpus=[])
                                           # is byte-identical to today. The caption path floats these ahead of the lean.
    # Lever engine (M-levers): explicit per-characteristic direction HYDRATED from the linked Persona at load,
    # which personas.compose_persona_instruction renders into the surface `persona` the casting/hook/caption
    # payloads carry. ADDITIVE — empty on every legacy/unlinked account, so compose returns the bare persona
    # voice (byte-identical). content_focus/energy -> casting; hook_angle/hook_tone -> the on-screen hook.
    content_focus: list[str] = Field(default_factory=list)
    energy: Optional[str] = None
    hook_angle: Optional[str] = None
    hook_tone: Optional[str] = None
    brief: str = ""                        # M2 LOCK: the persona's operator-approved strategy, HYDRATED from the
                                           # linked Persona; the directive compilers append it after the voice so it
                                           # rides into the real casting/hook/caption prompts. Empty -> byte-identical.
    # M3 DIRECTIVE ENGINE: the per-dimension OVERRIDE text + the per-persona clip budget, HYDRATED from the
    # linked Persona. Empty/None -> the lever-compiled default / the global cast budget (byte-identical when unset).
    casting_directive: str = ""
    hook_directive: str = ""
    caption_directive: str = ""
    clip_count: Optional[int] = None
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
        _hydrate_from_personas(a, cfg)               # A1: linked accounts read their persona's voice/tag_lean
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

    def resolve_backend(self, handle: str, platform: Optional[Platform] = None) -> Optional[str]:
        """The per-(handle, platform) poster BACKEND override, or None when unset (the publish loop then
        uses the global FANOPS_POSTER — byte-identical to today). Unknown handle / no override -> None,
        never raises: a missing override is the NORMAL case, not an error. Mirrors resolve_account_id's
        per-platform lookup but with no fallback (the fallback is the GLOBAL backend, applied by the caller)."""
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


def _hydrate_from_personas(accts: "Accounts", cfg: Config) -> None:
    """A1: override each LINKED account's persona voice, tag_lean, corpus, levers (content_focus/energy/hook_angle/hook_tone), cut spec (clip_profile/framing), brief, and per-dimension directives IN MEMORY from its Persona (the source of truth
    once linked), so every consumer reading a.persona / a.tag_lean sees the persona's value and an operator
    edit takes effect on the next load — with ZERO consumer rewiring. FAIL-OPEN: no personas.json, a
    dangling persona_id, or any error leaves the account's inline values exactly as today (byte-identical
    when unlinked). The personas import is lazy (personas imports accounts in migrate -> avoid a cycle)."""
    if not any(getattr(acc, "persona_id", None) for acc in accts.accounts):
        return                                       # no links -> no work, no personas.json read at all
    try:
        from fanops.personas import Personas, resolved_cut_spec
        reg = Personas.load(cfg)
    except Exception:
        return                                       # corrupt/absent personas.json -> inline values stand
    for acc in accts.accounts:
        per = reg.get(getattr(acc, "persona_id", None))
        if per is None:
            continue                                 # dangling id -> inline values stand
        if per.voice:
            acc.persona = per.voice                  # the persona owns the voice (empty voice -> keep inline)
        acc.tag_lean = per.tag_lean                  # the persona owns the lean (may be None -> clears inline)
        acc.hashtag_corpus = list(per.hashtag_corpus)   # B1: the persona owns the curated corpus (the caption path reads it)
        # Lever engine: the persona owns each lever (empty -> compose ignores it -> byte-identical). clip_profile/
        # framing override the account's own ONLY when the persona pins them (else the account/global default stands).
        acc.content_focus = list(per.content_focus)
        acc.energy = per.energy
        acc.hook_angle = per.hook_angle
        acc.hook_tone = per.hook_tone
        _prof, _fr = resolved_cut_spec(per)   # P2: pin wins; else derived from content_focus/energy; else None (global stands)
        if _prof: acc.clip_profile = _prof; acc.persona_owns_profile = True   # S2 provenance: the persona TRULY owns the length
        if _fr: acc.framing = _fr
        acc.brief = getattr(per, "brief", "") or ""   # M2: the persona owns the locked brief (empty -> compose ignores it)
        acc.casting_directive = getattr(per, "casting_directive", "") or ""   # M3: per-dimension override text (empty -> lever-compiled default)
        acc.hook_directive = getattr(per, "hook_directive", "") or ""
        acc.caption_directive = getattr(per, "caption_directive", "") or ""
        acc.clip_count = getattr(per, "clip_count", None)                     # M3: per-persona clip budget (None -> global)


def link_persona(cfg: Config, handle: str, persona_id: str) -> str:
    """Link ONE account to a first-class Persona (set persona_id) atomically — the A2 connect control. A
    BLANK persona_id CLEARS the link (-> the account's inline persona/tag_lean stand again). Scans ALL
    rows (dup-handle safety, mirrors set_tag_lean); preserves every sibling + unknown field. Unknown
    handle -> KeyError (caller -> clean ActionResult). Does NOT validate the id exists (a dangling link
    fails open at load) — the Studio resolves the id from the live registry before calling."""
    pid = (persona_id or "").strip()
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
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
    platform = getattr(platform, "value", platform)              # accept a Platform enum or its value string
    if platform not in {pf.value for pf in Platform}:
        raise ValueError(f"unknown platform: {platform!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
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
                status: str = "active", access: str = "postiz", tag_lean: str = "",
                clip_profile: str = "", framing: str = "") -> str:
    """Onboard a BRAND-NEW account into accounts.json atomically — so the Go-Live tab adds an account
    WITHOUT the operator hand-editing JSON. Validates at this control-file boundary: a non-blank handle,
    every platform a known Platform value, and (when given) a known tag_lean (never write an account that
    won't reload or carries a bogus lean). Rejects a duplicate handle. New accounts default to status=active
    (so they appear in the mapping list at once) and access=postiz; account_id stays empty — the per-platform
    ids are set afterward via write_integration / the mapping UI. Returns the handle; raises ValueError on bad
    input."""
    handle = (handle or "").strip()
    if not handle:
        raise ValueError("handle is required")
    plats = [getattr(x, "value", x) for x in platforms]      # accept Platform enums or value strings
    valid = {pf.value for pf in Platform}
    bad = [x for x in plats if x not in valid]
    if bad:
        raise ValueError(f"unknown platform(s): {', '.join(map(str, bad))}")
    lean = (tag_lean or "").strip().lower()
    if lean and lean not in TAG_LEANS:
        raise ValueError(f"unknown tag_lean: {tag_lean!r}")
    prof = (clip_profile or "").strip().lower()
    if prof and prof not in PROFILE_NAMES:
        raise ValueError(f"unknown clip_profile: {clip_profile!r}")
    fr = (framing or "").strip().lower()
    if fr and fr not in FRAMING_NAMES:
        raise ValueError(f"unknown framing: {framing!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        if any(isinstance(a, dict) and a.get("handle") == handle for a in accounts):
            raise ValueError(f"duplicate handle {handle} (already exists)")
        accounts.append({"handle": handle, "account_id": "", "platforms": plats,
                         "status": str(status), "access": str(access),
                         "persona": persona or "", "tag_lean": lean or None,
                         "clip_profile": prof or None, "framing": fr or None, "integrations": {}})
        write_json_atomic(p, raw)
    return handle


def ensure_channel(cfg: Config, handle: str, platform: str, persona: str = "", tag_lean: str = "") -> bool:
    """Idempotently ensure a (handle, platform) channel EXISTS in accounts.json atomically — the adopt-side
    primitive (M4 discover→adopt): a discovered remote channel becomes an onboardable account+platform in
    ONE write. If the handle is absent → append a NEW account (status active, [platform], empty account_id —
    born inert; the id is mapped next via write_integration). If the handle EXISTS but lacks this platform →
    append the platform (preserving every other field, sibling, and integration). If it already has the
    platform → no-op. persona/tag_lean SEED a NEW account only — they are IGNORED when the handle already
    exists (an existing account keeps its own persona/lean; change those via set_persona/set_tag_lean), so
    adopting a second platform never clobbers an account's persona. Scans ALL rows (no break) so a hand-edited
    duplicate handle gains the platform on EVERY copy — consistent with write_integration/set_backend, which
    adopt calls next. Validates handle non-blank + platform a known Platform.value + tag_lean (when given) at
    the control-file boundary. Returns True iff it changed accounts.json. Unlike add_account it NEVER raises
    on a duplicate handle (idempotent by design); it raises ValueError only on bad input."""
    handle = (handle or "").strip()
    if not handle:
        raise ValueError("handle is required")
    platform = getattr(platform, "value", platform)              # accept a Platform enum or its value string
    if platform not in {pf.value for pf in Platform}:
        raise ValueError(f"unknown platform: {platform!r}")
    lean = (tag_lean or "").strip().lower()
    if lean and lean not in TAG_LEANS:
        raise ValueError(f"unknown tag_lean: {tag_lean!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
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
                             "persona": (persona or "").strip(), "tag_lean": lean or None, "integrations": {}})
            changed = True
        if changed:
            write_json_atomic(p, raw)
        return changed


def set_status(cfg: Config, handle: str, status: str) -> str:
    """Change ONE account's status atomically (the Go-Live DEMOTE control — e.g. an active placeholder ->
    planned, so it leaves active() and the publishing fan-out without losing its row). Validates status at
    the control-file boundary (must be an AccountStatus value — never write a status that won't reload);
    preserves every sibling, unknown field, and the account's own other fields. Unknown handle -> KeyError."""
    status = getattr(status, "value", status)                    # accept an AccountStatus enum or its value
    if status not in {s.value for s in AccountStatus}:
        raise ValueError(f"unknown status: {status!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        found = False
        for a in accounts:                                       # scan ALL rows (no break): a hand-edited file with
            if isinstance(a, dict) and a.get("handle") == handle:  # duplicate handles must not leave a 2nd copy active —
                a["status"] = str(status); found = True          # mirrors remove_account dropping every match
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def set_tag_lean(cfg: Config, handle: str, lean: str) -> str:
    """Set or clear ONE account's tag_lean atomically (the Go-Live persona-differentiation control). A blank
    lean CLEARS it (-> None). Validates a non-blank lean at the control-file boundary (must be a known
    TAG_LEANS value — never write a lean that vet_hashtags would ignore as a typo); preserves every sibling,
    unknown field, and the account's own other fields; scans ALL rows (dup-handle safety, mirrors set_status).
    Unknown handle -> KeyError."""
    lean = (lean or "").strip().lower()
    if lean and lean not in TAG_LEANS:
        raise ValueError(f"unknown tag_lean: {lean!r}")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        found = False
        for a in accounts:
            if isinstance(a, dict) and a.get("handle") == handle:
                a["tag_lean"] = lean or None; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def set_clip_profile(cfg: Config, handle: str, profile: str) -> str:
    """Set or clear ONE account's clip_profile atomically (the M2 per-account LENGTH control). A blank
    profile CLEARS it (-> None -> resolve_clip_profile falls back to the global FANOPS_CLIP_PROFILE).
    Validates a non-blank profile at the control-file boundary (must be a known bands.PROFILE_NAMES value
    — never write a profile that only band_for's default would catch); preserves every sibling, unknown
    field, and the account's own other fields; scans ALL rows (dup-handle safety, mirrors set_tag_lean).
    Unknown handle -> KeyError."""
    profile = (profile or "").strip().lower()
    if profile and profile not in PROFILE_NAMES:
        raise ValueError(f"unknown clip_profile: {profile!r} (valid: {', '.join(sorted(PROFILE_NAMES))})")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        found = False
        for a in accounts:
            if isinstance(a, dict) and a.get("handle") == handle:
                a["clip_profile"] = profile or None; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def set_framing(cfg: Config, handle: str, framing: str) -> str:
    """Set or clear ONE account's framing atomically (the M2 per-account vertical-CROP control). A blank
    framing CLEARS it (-> None -> resolve_top_bias falls back to the global FANOPS_AWARE_REFRAME). Validates
    a non-blank framing at the control-file boundary (must be a known config.FRAMING_NAMES value — never write
    a framing that resolve_top_bias would ignore as a typo); preserves every sibling, unknown field, and the
    account's own other fields; scans ALL rows (dup-handle safety, mirrors set_clip_profile). Unknown handle
    -> KeyError."""
    framing = (framing or "").strip().lower()
    if framing and framing not in FRAMING_NAMES:
        raise ValueError(f"unknown framing: {framing!r} (valid: {', '.join(sorted(FRAMING_NAMES))})")
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        found = False
        for a in accounts:
            if isinstance(a, dict) and a.get("handle") == handle:
                a["framing"] = framing or None; found = True
        if not found:
            raise KeyError(handle)
        write_json_atomic(p, raw)
    return handle


def set_persona(cfg: Config, handle: str, persona: str) -> str:
    """Set or clear ONE account's persona atomically (the Go-Live persona editor). A blank persona CLEARS it
    (-> ""). Preserves every sibling, unknown field, and the account's other fields; scans ALL rows (dup-handle
    safety, mirrors set_tag_lean/set_status). Unknown handle -> KeyError."""
    persona = (persona or "").strip()
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
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
    p = cfg.accounts_path
    with _accounts_txn(cfg):                                      # serialize: load INSIDE the lock (no lost update)
        raw, accounts = _load_raw_accounts(p)
        kept = [a for a in accounts if not (isinstance(a, dict) and a.get("handle") == handle)]
        if len(kept) == len(accounts):
            raise KeyError(handle)
        raw["accounts"] = kept
        write_json_atomic(p, raw)
    return handle
