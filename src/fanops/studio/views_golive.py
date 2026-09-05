"""Go-Live / onboarding read-models for the Studio: per-channel account rows, readiness matrix,
onboarding cards, and the full GoLiveStatus tab projection. Pure (no HTTP/Flask). Lazy-imports
_publish_mode_label and daemon_health from the views facade to avoid circular imports."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from fanops.accounts import Accounts
from fanops.config import Config


@dataclass
class GoLiveChannel:
    platform: str
    integration_id: str        # effective current id: the per-platform integrations[platform], else the
                               # shared account_id fallback, else "" (unmapped). NEVER a secret.
    backend: str = ""          # Zernio slice 4: the per-(handle, platform) backend OVERRIDE (e.g. "zernio");
                               # "" == no override -> the global FANOPS_POSTER. Surfaced so the operator sees
                               # WHICH scheduler each channel publishes through (IG postiz, TikTok zernio).


@dataclass
class ChannelReadiness:
    handle: str
    platform: str
    backend: str          # effective_provider resolved, "" if none
    mapped: bool
    creds: bool           # backend_has_creds(effective_provider) — publish creds NOT Meta
    persona: bool         # persona_id or inline persona (account-level)
    window: bool          # True always today (default-open window)
    ready: bool           # MUST agree with go_live
    first_blocker: str    # earliest failing check as action phrase; "" when ready


@dataclass
class GoLiveAccount:
    handle: str
    persona: Optional[str]
    channels: list[GoLiveChannel]    # one per platform this handle posts to
    persona_id: Optional[str] = None # S8: the linked first-class Persona record id (Account.persona_id) — None
                                     # when the account uses only inline text. Drives the linked/no-persona badge.


@dataclass
class GoLiveStatus:
    mode: str
    is_live: bool
    postiz_url: Optional[str]
    key_set: bool              # BOOL only — the POSTIZ_API_KEY value is NEVER carried in this read-model
    accounts: list[GoLiveAccount]
    checks: list[dict]
    notes: list[str]
    zernio_key_set: bool = False       # Zernio slice 4: BOOL only — ZERNIO_API_KEY present (connect-block state)
    learning_validated: bool = False   # M3: cutover.json metrics_confirmed — the loop is unfrozen on this backend
    account_casting: bool = False      # per-account moment casting ON (FANOPS_ACCOUNT_CASTING) — distinct moment sets per account
    clip_profile: str = "talk"         # clip-length band (FANOPS_CLIP_PROFILE): talk 12-22s / song 18-35s
    responder_mode: str = "llm"        # FANOPS_RESPONDER resolves to 'llm' (validate-or-refuse); gates answered by the LLM
    llm_transport: str = "claude"      # FANOPS_LLM_TRANSPORT: claude | cursor (which CLI shells for gates)
    llm_cli_binary: str = "claude"     # resolved binary name for operator copy (claude | cursor-agent)
    daemon: Optional[dict] = None      # launchd pipeline-driver health (verdict/loaded/interval/responder), None off-darwin
    paused: bool = False               # 00_control/paused — the operator brake on the unattended pump
    demoted: list = field(default_factory=list)   # Phase 3: planned/demoted accounts (promotable) — golive_accounts lists only active()
    # Phase 6: A/B learning-loop INTENT flags (default OFF). For the variant_* three, ON sets intent only —
    # their apply paths stay learning_validated-frozen (that gate auto-stamps on real metrics). The two
    # learn_* flags below carry NO such freeze: each is the whole gate on its learn-pass actuator.
    variant_learning: bool = False     # FANOPS_VARIANT_LEARNING — the loop master switch
    variant_amplify: bool = False      # FANOPS_VARIANT_AMPLIFY — a sustained winner auto-amplifies its source
    learn_amplify: bool = False        # FANOPS_LEARN_AMPLIFY — learn-pass winner mints new moments/clips/posts
    learn_retire: bool = False         # FANOPS_LEARN_RETIRE — learn-pass loser suppresses its clip/moment/unshipped posts
    variant_ucb: bool = False          # FANOPS_VARIANT_UCB — deterministic UCB1 explore/exploit rank
    variant_transfer: bool = False     # FANOPS_VARIANT_TRANSFER — seed a cold account from proven donors
    setup_state: str = "NOT_CONFIGURED"   # MOL-302: derived setup position (never persisted)
    setup_next: str = ""               # next operator action for the current setup_state
    half_live: bool = False            # D15/MOL-297: LIVE flag set but nothing routes live — warn, never solid-green LIVE
    half_live_hint: str = ""           # operator-facing explanation (names the ignored FANOPS_POSTER value)
    channels: list[ChannelReadiness] = field(default_factory=list)  # S04: per-(handle×platform) readiness matrix
    next_blocker: str = ""             # earliest first_blocker across channels; connect-first when none
    account_cards: list["AccountOnboardingCard"] = field(default_factory=list)  # U12: account-centric onboarding
                                       # cards — a display-only RE-PROJECTION of `channels` grouped by handle
                                       # (+ demoted). NEVER recomputes readiness; the template renders these.


@dataclass
class AccountOnboardingCard:
    """U12: one card per handle (active or demoted) for account-centric onboarding — a pure DISPLAY projection
    of channel_readiness re-grouped by account, so onboarding one account never means hopping stations. It
    NEVER recomputes readiness: `channels` is the subset of channel_readiness for this handle, `next_blocker`
    is that handle's WORST channel first_blocker (same _blocker_priority order the fleet next_blocker uses),
    `next_anchor` deep-links the CTA to the existing form that clears it."""
    account: GoLiveAccount            # active or demoted (the header + persona chip source)
    channels: list[ChannelReadiness]  # subset of channel_readiness for this handle ([] for a demoted account)
    next_blocker: str                 # worst-channel first_blocker (fleet order), "" when the account is ready
    next_anchor: str                  # in-page fragment id the single CTA deep-links to ("" when ready)


def golive_accounts(cfg: Config) -> list[GoLiveAccount]:
    """The active accounts as a per-channel read-model, SHARED by golive_status + home_status so the two
    surfaces never drift on what "connected" means. One GoLiveChannel per platform; integration_id is the
    effective per-platform id (integrations[platform] -> account_id fallback -> "" unmapped). Fail-open: a
    malformed accounts.json logs accounts_error and degrades to [] (the surface never 500s). NO secret."""
    try:
        return [GoLiveAccount(
            handle=a.handle, persona=a.persona,
            persona_id=getattr(a, "persona_id", None),     # S8: the linked first-class Persona (badge), additive
            channels=[GoLiveChannel(platform=p.value,
                                    integration_id=a.integrations.get(p.value) or a.account_id or "",
                                    backend=a.backends.get(p.value) or "")
                      for p in a.platforms])
            for a in Accounts.load(cfg).active()]
    except Exception as exc:
        from fanops.log import get_logger             # ECC fix #5: a disk/parse error was invisible
        get_logger(cfg)("golive", "-", "accounts_error", err=str(exc)[:160])
        return []                                     # malformed accounts.json — doctor's readiness check names it


def golive_demoted_accounts(cfg: Config) -> list:
    """Phase 3: the PLANNED (demoted / never-activated) accounts as a read-model so Go-Live can render them with
    a Promote button — golive_accounts lists only active(), so a demote was a silent one-way door. Fail-open -> []
    on a malformed accounts.json (mirrors golive_accounts)."""
    try:
        return [GoLiveAccount(
            handle=a.handle, persona=a.persona,
            persona_id=getattr(a, "persona_id", None),     # S8: the linked first-class Persona (badge), additive
            channels=[GoLiveChannel(platform=p.value,
                                    integration_id=a.integrations.get(p.value) or a.account_id or "",
                                    backend=a.backends.get(p.value) or "")
                      for p in a.platforms])
            for a in Accounts.load(cfg).accounts if a.status.value == "planned"]
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("golive", "-", "accounts_error", err=str(exc)[:160])
        return []


def _blocker_priority(msg: str) -> int:
    if msg == "connect Postiz or Zernio first": return 0
    if msg == "map an integration id": return 1
    if msg == "route to a scheduler backend": return 2
    if msg.startswith("connect ") and "first (set " in msg: return 3
    if msg == "link a persona": return 4
    return 99


def _channel_first_blocker(cfg: Config, *, mapped: bool, has_integ: bool, has_backend: bool,
                           backend: str, persona_ok: bool) -> str:
    if not cfg.postiz_api_key and not cfg.zernio_api_key:
        return "connect Postiz or Zernio first"
    if not mapped:
        return "map an integration id"
    if has_integ and not has_backend:
        return "route to a scheduler backend"
    if has_backend and not has_integ:
        return "map an integration id"
    if backend:
        from fanops.config import _LIVE_BACKENDS
        if backend in _LIVE_BACKENDS and not cfg.backend_has_creds(backend):
            need = {"zernio": "ZERNIO_API_KEY", "postiz": "POSTIZ_API_KEY"}.get(backend, "the backend's API key")
            return f"connect {backend} first (set {need})"
    if cfg.account_casting and not persona_ok:
        return "link a persona"
    return ""


def channel_readiness(cfg: Config) -> list[ChannelReadiness]:
    """S04: per-(handle×platform) readiness for the Go-Live matrix — mirrors go_live gates without mutating."""
    from fanops.models import Platform
    try:
        accounts = Accounts.load(cfg)
        live_ready = set(accounts.live_ready_channels())
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("golive", "-", "channel_readiness_error", err=str(exc)[:160])
        return []
    out: list[ChannelReadiness] = []
    for a in accounts.active():
        persona_ok = bool((a.persona_id or "").strip() or (a.persona or "").strip())
        for p in a.platforms:
            pv = p.value
            mapped = bool(a.integrations.get(pv) or a.account_id)
            has_integ = bool(a.integrations.get(pv))
            has_backend = bool(a.backends.get(pv))
            backend = accounts.effective_provider(a.handle, Platform(p)) or ""
            creds = bool(backend and cfg.backend_has_creds(backend))
            r2 = (has_integ and not has_backend) or (has_backend and not has_integ)
            in_live = (a.handle, pv, backend) in live_ready if backend else False
            ready = in_live and not r2 and (not cfg.account_casting or persona_ok)
            blocker = _channel_first_blocker(cfg, mapped=mapped, has_integ=has_integ, has_backend=has_backend,
                                             backend=backend, persona_ok=persona_ok)
            if blocker:
                ready = False
            out.append(ChannelReadiness(handle=a.handle, platform=pv, backend=backend, mapped=mapped, creds=creds,
                                        persona=persona_ok, window=True, ready=ready, first_blocker=blocker))
    return out


def _next_blocker(channels: list[ChannelReadiness]) -> str:
    if not channels:
        return "connect Postiz or Zernio first"
    blockers = [c.first_blocker for c in channels if c.first_blocker]
    if not blockers:
        return ""
    return min(blockers, key=_blocker_priority)


def _card_worst_blocker(channels: list[ChannelReadiness]) -> str:
    """The WORST (highest-priority) first_blocker across ONE handle's channels — the card's single CTA. Uses the
    SAME _blocker_priority ordering the fleet _next_blocker uses (never a new order). "" when every channel is
    ready. An empty ladder (a demoted account has no channel_readiness rows) has no publish blocker -> ""."""
    blockers = [c.first_blocker for c in channels if c.first_blocker]
    return min(blockers, key=_blocker_priority) if blockers else ""


def _card_next_anchor(handle: str, blocker: str, channels: list[ChannelReadiness]) -> str:
    """Map a card's worst blocker to the in-page fragment id of the EXISTING form that clears it (NO new route).
    Matched on the exact strings _channel_first_blocker returns; the map/backend anchors point at the FIRST
    channel of this handle whose own first_blocker is that phrase (the deep-linked form id in the card body)."""
    if not blocker:
        return ""
    if blocker == "connect Postiz or Zernio first" or (blocker.startswith("connect ") and "first (set " in blocker):
        return "#golive-connect"
    if blocker == "map an integration id":
        ch = next((c for c in channels if c.first_blocker == blocker), None)
        return f"#map-{handle}-{ch.platform}" if ch else "#golive-connect"
    if blocker == "route to a scheduler backend":
        ch = next((c for c in channels if c.first_blocker == blocker), None)
        return f"#backend-{handle}-{ch.platform}" if ch else "#golive-connect"
    if blocker == "link a persona":
        return f"#persona-{handle}"
    return "#golive-connect"


def onboarding_account_cards(cfg: Config) -> list[AccountOnboardingCard]:
    """U12: the account-centric onboarding read-model — one AccountOnboardingCard per handle (active first,
    then demoted). A pure DISPLAY projection: it RE-GROUPS channel_readiness(cfg) by handle (never a second
    readiness computation), picks that handle's worst-channel blocker as the single CTA (fleet order), maps it
    to the existing form's anchor, and attaches the display-only insights rows. Demoted accounts have no
    channel_readiness rows (only active() is projected) -> empty ladder + a promote CTA in the template."""
    chans = channel_readiness(cfg)
    by_handle: dict[str, list[ChannelReadiness]] = {}
    for c in chans:
        by_handle.setdefault(c.handle, []).append(c)
    cards: list[AccountOnboardingCard] = []
    for acct in golive_accounts(cfg):                     # active accounts, in accounts.json order
        ladder = by_handle.get(acct.handle, [])
        blocker = _card_worst_blocker(ladder)
        cards.append(AccountOnboardingCard(
            account=acct, channels=ladder, next_blocker=blocker,
            next_anchor=_card_next_anchor(acct.handle, blocker, ladder)))
    for acct in golive_demoted_accounts(cfg):             # demoted (planned) accounts — promotable, empty ladder
        cards.append(AccountOnboardingCard(
            account=acct, channels=[], next_blocker="", next_anchor=""))
    return cards


def golive_status(cfg: Config) -> GoLiveStatus:
    """Lock-free read-model for the Go-Live tab: the publish mode (dryrun/live), whether Postiz is
    configured (postiz_url is shown — it is NON-secret; key_set is a BOOL only, the key itself is never
    exposed), the ACTIVE accounts to map, and doctor readiness via health_model projectors (MOL-965 WP2).

    Accounts are listed PER-CHANNEL: each active handle carries one GoLiveChannel per platform, because a
    handle's Instagram and TikTok are DIFFERENT Postiz integrations (M1). Each channel's integration_id is
    the effective current id — the per-platform integrations[platform], else the shared account_id
    fallback, else "" (unmapped). Tolerates a malformed accounts.json (falls back to an empty list) so the
    tab never 500s. Readiness checks/notes/half-live come from build_health_report → project_golive_readiness
    (no second doctor assembly, no parallel half-live compute)."""
    from fanops.health_model import build_health_report, project_golive_readiness
    from fanops.studio import views as _views
    accts = golive_accounts(cfg)                      # shared helper (single source of truth for the accounts read-model)
    try:
        ready = project_golive_readiness(build_health_report(cfg))
    except Exception as exc:                          # invariant: the Go-Live tab must never 500 (ecc:python-review)
        from fanops.log import get_logger             # ECC fix #5: log why readiness is unavailable
        get_logger(cfg)("golive", "-", "doctor_error", err=str(exc)[:160])
        # Never fail-open to calm LIVE-looking half_live=False (MOL-965 WP2-fix).
        ready = {"checks": [], "notes": ["readiness check unavailable"],
                 "half_live": True,
                 "half_live_hint": "readiness unavailable — not confirmed LIVE"}
    from fanops.validation_gate import learning_validated
    from fanops.doctor import setup_state, setup_next_action
    from fanops.pipeline_run import paused as _paused
    chans = channel_readiness(cfg)
    return GoLiveStatus(
        mode=_views._publish_mode_label(cfg),               # provider-aware (M3); 'dryrun' when not live
        is_live=cfg.is_live,
        half_live=ready["half_live"], half_live_hint=ready["half_live_hint"],
        postiz_url=cfg.postiz_url,                    # non-secret; shown so the operator can confirm config
        key_set=cfg.postiz_api_key is not None,       # BOOL only — the API key value is NEVER exposed
        zernio_key_set=cfg.zernio_api_key is not None,  # Zernio slice 4: BOOL only (connect-block state)
        accounts=accts,
        channels=chans, next_blocker=_next_blocker(chans),
        account_cards=onboarding_account_cards(cfg),   # U12: account-centric cards (display re-projection of channels)
        checks=ready["checks"],
        notes=ready["notes"],
        learning_validated=learning_validated(cfg),    # M3: shows whether the loop is unfrozen (cutover done)
        account_casting=cfg.account_casting,           # per-account moment casting toggle state (persona diff)
        clip_profile=cfg.clip_profile,                 # clip-length band (talk/song)
        responder_mode=cfg.responder_mode,             # always 'llm' now (validate-or-refuse); gates answered by the LLM
        llm_transport=cfg.llm_transport, llm_cli_binary=cfg.llm_cli_binary,
        daemon=_views.daemon_health(cfg),                     # launchd driver health for the Go-Live daemon control (None off-darwin)
        paused=_paused(cfg),                           # 00_control/paused — operator brake on the unattended pump
        demoted=golive_demoted_accounts(cfg),          # Phase 3: promotable planned accounts
        variant_learning=cfg.variant_learning,         # Phase 6: A/B learning-loop intent flags (default OFF)
        variant_amplify=cfg.variant_amplify, variant_ucb=cfg.variant_ucb, variant_transfer=cfg.variant_transfer,
        learn_amplify=cfg.learn_amplify,               # the learn-pass amplify intent flag (unattended minter)
        learn_retire=cfg.learn_retire,                 # the learn-pass retire intent flag (unattended destroyer)
        setup_state=setup_state(cfg), setup_next=setup_next_action(cfg))
