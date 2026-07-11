"""Studio "Go Live" actions (review-first milestone 5, operator-gated half): turn FanOps from dryrun
into real publishing via Postiz ENTIRELY in the browser — connect the Postiz URL + API key, map each
account to its Postiz integration id, and flip dryrun<->live behind an explicit confirm — so a
non-technical operator never touches env vars, the CLI, or accounts.json. Kept OUT of the already-large
actions.py; imported by app.py. Reuses the live poster (post.postiz), durable config writes
(autopilot.set_env_var), readiness (doctor), and the atomic accounts writer — this module is only the
operator-facing surface over them.

THREE load-bearing invariants:
  1. DUAL-WRITE — every config change writes BOTH the .env (durable across restarts) AND os.environ
     (so THIS running Studio reflects it immediately; load_dotenv runs once at process startup via
     cli.main, but the properties read os.getenv live). Writing only .env would silently not take
     effect until a restart.
  2. go_live is the ONLY setter of FANOPS_LIVE=1 (the global live/dryrun switch — NOT a backend pick;
     the publish provider is per-channel, M3), gated on ≥1 active channel having a provider whose creds
     are present + an explicit confirm — so a stray POST can never flip the system live. go_dryrun (the
     safe direction, writes FANOPS_LIVE=0) needs no confirm.
  3. The POSTIZ_API_KEY is NEVER echoed, logged, or returned in an ActionResult — only a boolean
     "set". set_postiz_config tests the key by calling Postiz; it never hands it back."""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import NamedTuple, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from datetime import datetime

from fanops import cutover
from fanops.config import Config, _LIVE_BACKENDS
from fanops.accounts import (Accounts, write_integration, add_account as _accounts_add_account,
                             set_status as _accounts_set_status, remove_account as _accounts_remove_account,
                             set_persona as _accounts_set_persona,
                             set_backend as _accounts_set_backend, ensure_channel as _accounts_ensure_channel,
                             set_ig_user_id as _accounts_set_ig_user_id, load_accounts_safe)
from fanops.log import get_logger
from fanops import secret_provider
from fanops.autopilot import set_env_var, unset_env_var
from fanops.errors import CutoverError, PostizAuthError, ToolchainMissingError, ZernioAuthError
from fanops.models import Platform
from fanops.post import postiz, zernio
from fanops.studio.actions import ActionResult

_PLATFORM_VALUES = frozenset(p.value for p in Platform)   # the platforms FanOps can route (M3)


def _dual_write(cfg: Config, key: str, value: str) -> Optional[str]:
    """Persist KEY=value durably AND set os.environ[KEY] (this process). Non-secret keys land in .env
    (atomic set_env_var); the three operator secrets land in the OS keyring ONLY (MOL-360) — never
    plaintext .env. Returns None on success, or an error string if the durable write failed — the
    caller surfaces it as a clean ActionResult so the Go-Live tab never 500s. On failure os.environ
    is left UNTOUCHED (never reflect a change that won't persist)."""
    if secret_provider.is_secret_env_key(key):
        try:
            secret_provider.set_secret(key, value)
        except (OSError, ValueError) as exc:
            return str(exc)[:160]
        try:
            unset_env_var(cfg.root / ".env", key)          # scrub any legacy plaintext secret
        except OSError as exc:
            return f"could not scrub legacy {key} from .env: {str(exc)[:140]}"
    else:
        try:
            set_env_var(cfg.root / ".env", key, value)
        except (OSError, ValueError) as exc:
            return f"could not write {key} to .env: {str(exc)[:140]}"
    os.environ[key] = value
    return None


def _dual_unset(cfg: Config, key: str) -> Optional[str]:
    """Remove KEY durably AND from os.environ (this process). Secrets: keyring + legacy .env scrub."""
    if secret_provider.is_secret_env_key(key):
        secret_provider.delete_secret(key)
    try:
        unset_env_var(cfg.root / ".env", key)
    except OSError as exc:
        return f"could not unset {key} from .env: {str(exc)[:140]}"
    os.environ.pop(key, None)
    return None


def _dotenv_assignment(env_path: Path, key: str) -> Optional[str]:
    """Read KEY's value from a .env file (ignores comments). None when absent."""
    if not env_path.exists():
        return None
    for ln in env_path.read_text().splitlines():
        stripped = ln.lstrip()
        raw_key = ln.split("=", 1)[0].strip()
        had_export = raw_key.startswith("export ")
        bare_key = raw_key[len("export "):].strip() if had_export else raw_key
        if stripped and not stripped.startswith("#") and bare_key == key:
            return ln.split("=", 1)[1].strip() if "=" in ln else ""
    return None


def set_postiz_config(cfg: Config, url: str, key: str = "") -> ActionResult:
    """Connect Postiz: durably set POSTIZ_URL (+ POSTIZ_API_KEY when a non-blank key is given), then
    test the credentials against the live instance. The key is write-only — tested, never returned or
    logged (the result exposes only a key_set bool). A blank key leaves any existing key untouched, so
    the operator can update just the URL. Rejects a non-http(s) URL up front with NO partial write."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ActionResult(ok=False, error=f"Postiz URL must start with http:// or https:// — got {url!r}")
    err = _dual_write(cfg, "POSTIZ_URL", url)
    if err:
        return ActionResult(ok=False, error=err)
    key = (key or "").strip()
    if key:
        err = _dual_write(cfg, "POSTIZ_API_KEY", key)    # write-only: stored, never echoed back
        if err:
            return ActionResult(ok=False, error=err)
    try:
        reachable = postiz.postiz_check_auth(cfg)
    except PostizAuthError:
        # Discard the exception text on the key-handling surface — emit a FIXED message so a future
        # PostizAuthError that ever embedded the key value could not leak through str(exc) (ecc:python-review).
        # W9: the key WAS dual-written above, so tell the operator it's saved (re-enter to correct) rather
        # than imply nothing happened. Still no key echo.
        return ActionResult(ok=False, error="Postiz auth failed — check POSTIZ_API_KEY (the test request was "
                            "rejected; credentials saved — re-enter to correct).")
    if not reachable:
        return ActionResult(ok=False, error=f"Saved POSTIZ_URL but could not reach Postiz at {url} — "
                            "check the URL points at your running Postiz instance.")
    return ActionResult(ok=True, detail={"url": url, "key_set": cfg.postiz_api_key is not None, "auth": "ok"})


def refresh_integrations(cfg: Config) -> ActionResult:
    """Fetch the operator's connected Postiz channels so the mapping UI can offer them as a picklist
    (no hand-pasted integration ids). Auth failure -> FATAL + POSTIZ_API_KEY (named so the operator
    knows what to fix); any other failure -> a clean one-line error, never a 500."""
    try:
        integrations = postiz.postiz_list_integrations(cfg)
    except PostizAuthError:
        # Fixed message (no str(exc)) — the key must never reach an ActionResult.error (ecc:python-review).
        return ActionResult(ok=False, error="FATAL auth failure — check POSTIZ_API_KEY.")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not list Postiz integrations: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"integrations": integrations})


# ── Zernio (slice 4): a SECOND scheduler backend, key-only (hosted) — connect + per-account routing ────
def set_zernio_config(cfg: Config, key: str) -> ActionResult:
    """Connect Zernio: durably set ZERNIO_API_KEY, then test it against the live API. Zernio is HOSTED
    (no URL to set, unlike Postiz). The key is write-only — tested, NEVER returned or logged (the result
    exposes no key). A blank key is rejected with NO write (there is nothing else to configure)."""
    key = (key or "").strip()
    if not key:
        return ActionResult(ok=False, error="enter your Zernio API key (Settings > API Keys).")
    err = _dual_write(cfg, "ZERNIO_API_KEY", key)        # write-only: stored, never echoed back
    if err:
        return ActionResult(ok=False, error=err)
    try:
        reachable = zernio.zernio_check_auth(cfg)
    except ZernioAuthError:
        # Fixed message (no str(exc)) so the key can never leak through the exception text. The key WAS
        # dual-written, so tell the operator it's saved (re-enter to correct), still no echo.
        return ActionResult(ok=False, error="Zernio auth failed — check your ZERNIO_API_KEY (the test "
                            "request was rejected; key saved — re-enter to correct).")
    if not reachable:
        return ActionResult(ok=False, error="Saved ZERNIO_API_KEY but could not reach Zernio — try again.")
    return ActionResult(ok=True, detail={"key_set": cfg.zernio_api_key is not None, "auth": "ok"})


def refresh_zernio_accounts(cfg: Config) -> ActionResult:
    """Fetch the operator's connected Zernio accounts so the routing UI can show which TikTok _ids exist
    (no hand-pasting). Auth failure -> FATAL + ZERNIO_API_KEY; any other failure -> a clean one-line error."""
    try:
        accounts = zernio.zernio_list_accounts(cfg)
    except ZernioAuthError:
        return ActionResult(ok=False, error="FATAL auth failure — check ZERNIO_API_KEY.")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not list Zernio accounts: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"accounts": accounts})


def set_account_backend(cfg: Config, handle: str, platform: str, backend: str, confirmed: bool = False) -> ActionResult:
    """Route ONE (handle, platform) channel to a poster BACKEND from the Go-Live tab. Setting it to a LIVE
    backend (postiz/zernio/rest/mcp) is a per-account 'go live' — GATED, mirroring go_live: (1) that
    backend's creds must be present, (2) an explicit confirm. A blank / 'default' backend CLEARS the
    override (back to the global FANOPS_POSTER) and needs neither. Unknown handle / platform / backend ->
    a clean one-line error, never a 500."""
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=False, error="no account selected")
    bk = (backend or "").strip().lower()
    is_live = bk in _LIVE_BACKENDS
    if is_live:
        if not cfg.backend_has_creds(bk):                # name the right key per backend
            need = {"zernio": "ZERNIO_API_KEY", "postiz": "POSTIZ_API_KEY"}.get(bk, "the backend's API key")
            return ActionResult(ok=False, error=f"not ready — connect {bk} first (set {need}).")
        if not confirmed:
            return ActionResult(ok=False, error=f"routing {handle}'s {platform} to {bk} publishes to the "
                                "REAL account — tick the confirm box, then click again.")
        # H3: a LIVE route must target a real per-platform integration id — without one the publish is
        # mis-targeted or burnt. The shared legacy account_id is NOT a valid per-channel provider id.
        acct = next((a for a in Accounts.load(cfg).accounts if a.handle == handle), None)
        if not (acct and acct.integrations.get(platform)):
            return ActionResult(ok=False, error=f"{handle} has no {platform} integration id — map the "
                                f"{platform} channel (Discover → Adopt) first, then route it to {bk}.")
    try:
        _accounts_set_backend(cfg, handle, platform, bk)
    except KeyError:
        return ActionResult(ok=False, error=f"no such account: {handle}")
    except ValueError as exc:                            # unknown platform / backend
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not route {handle}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"handle": handle, "platform": platform, "backend": bk or "default"})


def add_account(cfg: Config, handle: str, platforms: list, persona: str = "") -> ActionResult:
    """Onboard a NEW account ENTIRELY in the Go-Live tab (no accounts.json hand-edit): validate a
    non-blank handle + at least one platform, then append it (status active, access postiz) so it shows
    up in the channel-mapping list immediately. Duplicate handle / unknown platform / blank input -> a clean
    one-line error, never a 500. account_id stays empty — each channel is mapped per-platform next. (M3:
    tag_lean retired — link the account to a persona; its curated corpus is the hashtag differentiator.)"""
    handle = (handle or "").strip()
    platforms = [p for p in (platforms or []) if (p or "").strip()]
    if not handle:
        return ActionResult(ok=False, error="enter a handle to add an account")
    if not platforms:
        return ActionResult(ok=False, error=f"pick at least one platform for {handle}")
    try:
        handle = _accounts_add_account(cfg, handle, platforms, persona=(persona or "").strip())
    except ValueError as exc:                            # duplicate handle / unknown platform / blank
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not add {handle}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"added": handle, "platforms": platforms})


def set_per_account_hooks(cfg: Config, on: bool) -> ActionResult:
    """Toggle per-account on-screen hooks (FANOPS_CREATIVE_VARIATION) from the Go-Live tab — the gate that
    burns each account's OWN persona-flavored on-screen hook (M3d: default ON; turning it OFF restores the
    legacy shared moment hook on every surface). Dual-written so it takes effect immediately AND persists.
    Works in dryrun OR live (it changes how clips render per account, not whether they publish). A
    durable-write failure -> clean error."""
    err = _dual_write(cfg, "FANOPS_CREATIVE_VARIATION", "1" if on else "0")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"per_account_hooks": bool(on)})


def set_account_casting(cfg: Config, on: bool) -> ActionResult:
    """Toggle per-account moment casting (FANOPS_ACCOUNT_CASTING) from the Go-Live tab — ON casts each account
    its OWN LLM-selected moments (default OFF = every moment fans to all accounts). Dual-written so it takes
    effect immediately AND persists. Works in dryrun OR live (it changes which posts are BORN, not whether they
    publish). No secret -> no key-leak surface. A durable-write failure -> clean error. Structural twin of
    set_per_account_hooks; OFF is a true kill-switch (crosspost ignores persisted selections when the flag is off)."""
    err = _dual_write(cfg, "FANOPS_ACCOUNT_CASTING", "1" if on else "0")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"account_casting": bool(on)})


def set_ai_responder(cfg: Config, on: bool) -> ActionResult:
    """THE single, explicit AI switch (FANOPS_RESPONDER=llm|manual) from the Go-Live tab — the ONLY intended
    way to turn the LLM responder on/off. ON means the pipeline answers its own moment/caption/hook gates by
    invoking `claude` (on every run/kick/daemon-tick); OFF (manual) means gates stay pending for a human. This
    is the NO-haphazard-claude contract made operator-visible: claude fires because THIS toggle is on, never
    because the binary happens to be on PATH. Dual-written so it takes effect immediately AND persists across
    restarts/daemon-ticks. Works in dryrun OR live (orthogonal to publishing). Durable-write failure -> clean error."""
    err = _dual_write(cfg, "FANOPS_RESPONDER", "llm" if on else "manual")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"responder": "llm" if on else "manual"})


def install_daemon(cfg: Config, interval: str = "10m") -> ActionResult:
    """Install + load the launchd pipeline driver (hands-off processing) from the Go-Live tab — no CLI. The
    daemon is SCHEDULING only; it inherits the ambient AI switch (set_ai_responder), so installing it never
    turns the LLM on by itself. Off-darwin / launchctl-absent / bad interval -> clean ActionResult (never a
    trace). Reports whether the resolved responder means recurring `claude` so the operator sees the cost."""
    from fanops import daemon
    try:
        secs = daemon.parse_interval(interval)
        res = daemon.install(cfg, interval=secs, responder="inherit")
    except (RuntimeError, ToolchainMissingError, ValueError) as exc:
        return ActionResult(ok=False, error=f"daemon install failed: {str(exc)[:160]}")
    return ActionResult(ok=res.get("loaded", False), detail={"daemon_installed": True, "interval": secs,
                        "loaded": res.get("loaded", False), "responder": res.get("responder"),
                        "discloses_llm": res.get("discloses_llm", False)})


def uninstall_daemon(cfg: Config) -> ActionResult:
    """Unload + remove the launchd pipeline driver from the Go-Live tab — no CLI. Off-darwin / launchctl-absent
    -> clean ActionResult. Confirms the real outcome (daemon.stop reports stopped only if the label is gone)."""
    from fanops import daemon
    try:
        res = daemon.stop(cfg, remove=True)
    except (RuntimeError, ToolchainMissingError) as exc:
        return ActionResult(ok=False, error=f"daemon uninstall failed: {str(exc)[:160]}")
    return ActionResult(ok=res.get("stopped", False), detail={"daemon_removed": True, "stopped": res.get("stopped", False)})


def set_clip_profile(cfg: Config, profile: str) -> ActionResult:
    """Set FANOPS_CLIP_PROFILE (clip-length band) from the Go-Live tab. Length tiers: 'short' (8-15s),
    'medium' (16-26s), 'long' (28-45s); legacy content-type bands 'talk' (12-22s) / 'song' (18-35s) stay
    valid (M2 additive — no remap). Persisted VERBATIM (no normalize -> no learning-cohort split). Validates
    the value (unknown -> clean error, never silently mis-set); dual-written."""
    profile = (profile or "").strip().lower()
    _ALLOWED = ("short", "medium", "long", "talk", "song")
    if profile not in _ALLOWED:
        return ActionResult(ok=False, error=f"clip profile must be one of {_ALLOWED} (got {profile!r})")
    err = _dual_write(cfg, "FANOPS_CLIP_PROFILE", profile)
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"clip_profile": profile})


# ── Advanced learning levers (Phase 6) ──────────────────────────────────────────────────────────────
# Four default-OFF INTENT flags for the A/B learning loop, surfaced from env-only into the Go-Live tab.
# These set operator intent; the apply paths stay learning_validated-frozen (a flag ON does NOT unfreeze
# learning — that gate is auto-stamped from real non-degraded live metrics). Each dual-writes (.env + os.
# environ) so it takes effect without a restart; a durable-write failure -> clean error (never a 500).
def set_variant_learning(cfg: Config, on: bool) -> ActionResult:
    """Toggle the A/B hook-learning loop master switch (FANOPS_VARIANT_LEARNING). OFF (default) = no variant
    leader is ever selected; ON = the loop MAY act once learning_validated unfreezes it. Intent only."""
    err = _dual_write(cfg, "FANOPS_VARIANT_LEARNING", "1" if on else "0")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"variant_learning": bool(on)})


def set_variant_amplify(cfg: Config, on: bool) -> ActionResult:
    """Toggle variant-driven AMPLIFY (FANOPS_VARIANT_AMPLIFY) — a SUSTAINED proven winner auto-amplifies its
    source moment-guidance. Amplify-only (never retire), streak-gated, validation-frozen. Default OFF."""
    err = _dual_write(cfg, "FANOPS_VARIANT_AMPLIFY", "1" if on else "0")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"variant_amplify": bool(on)})


def set_variant_ucb(cfg: Config, on: bool) -> ActionResult:
    """Toggle UCB1 variant ranking (FANOPS_VARIANT_UCB) — replace the raw-mean leader pick with a deterministic
    UCB1 explore/exploit rank (amplify floor unchanged). Default OFF."""
    err = _dual_write(cfg, "FANOPS_VARIANT_UCB", "1" if on else "0")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"variant_ucb": bool(on)})


def set_variant_transfer(cfg: Config, on: bool) -> ActionResult:
    """Toggle cross-account hook TRANSFER (FANOPS_VARIANT_TRANSFER) — seed a cold account's variant pool with
    hooks proven on donor accounts. Default OFF."""
    err = _dual_write(cfg, "FANOPS_VARIANT_TRANSFER", "1" if on else "0")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"variant_transfer": bool(on)})


def map_account(cfg: Config, handle: str, platform: str, integration_id: str) -> ActionResult:
    """Map ONE (handle, platform) channel to its Postiz integration id, persisted atomically to
    accounts.json (the key non-technical win — replaces hand-editing JSON). A handle's Instagram and
    TikTok are different integrations, so the mapping is per-platform. Unknown handle / blank id ->
    clean error."""
    handle = (handle or "").strip()
    platform = (platform or "").strip()
    integration_id = (integration_id or "").strip()
    if not handle:
        return ActionResult(ok=False, error="no account selected")
    if not platform:
        return ActionResult(ok=False, error=f"no platform selected for {handle}")
    if not integration_id:
        return ActionResult(ok=False, error=f"pick a Postiz integration for {handle} {platform} (none selected)")
    try:
        write_integration(cfg, handle, platform, integration_id)
    except KeyError:
        return ActionResult(ok=False, error=f"no such account: {handle}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not map {handle} {platform}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"handle": handle, "platform": platform, "account_id": integration_id})


def set_meta_creds(cfg: Config, handle: str, ig_user_id: str, token: str = "") -> ActionResult:
    """Set ONE handle's per-account Meta Graph credentials (the audit's per-handle-creds gap): its IG
    Business user id + its Graph access token, so the insights / live-linking reads use the RIGHT handle's
    creds for that account instead of the single global META_IG_USER_ID. The id is NON-SECRET (persisted to
    accounts.json via set_ig_user_id, like a Postiz integration id); the TOKEN is a SECRET, dual-written to a
    PER-HANDLE .env key (META_GRAPH_TOKEN__<SLUG>) + os.environ — write-only, NEVER echoed/logged/returned
    (mirrors set_postiz_config's key discipline). A blank token leaves any existing per-handle token untouched
    (so the operator can update just the id). The id write happens FIRST (validates the handle exists); only
    then is the token written, so a bad handle never leaks a token into .env. Unknown handle / blank handle ->
    clean error. Fail-open: a durable-write failure surfaces as a clean ActionResult (the tab never 500s)."""
    from fanops.meta_graph import per_account_token_env_key
    handle = (handle or "").strip()
    ig_user_id = (ig_user_id or "").strip()
    token = (token or "").strip()
    if not handle:
        return ActionResult(ok=False, error="no account selected")
    if not ig_user_id and not token:
        return ActionResult(ok=False, error=f"nothing to set for {handle} — enter an IG user id and/or an access token")
    try:
        _accounts_set_ig_user_id(cfg, handle, ig_user_id)        # non-secret id -> accounts.json (validates the handle)
    except KeyError:
        return ActionResult(ok=False, error=f"no such account: {handle}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not set IG user id for {handle}: {str(exc)[:160]}")
    if token:                                                    # write-only secret -> keyring + os.environ
        key = per_account_token_env_key(handle)
        if not key:
            return ActionResult(ok=False, error=f"{handle} has no env-safe name for a per-account token — use the global META_GRAPH_TOKEN")
        err = _dual_write(cfg, key, token)                       # stored, never echoed back
        if err:
            return ActionResult(ok=False, error=err)
    # detail carries NO token — only the id (non-secret) + a token_set bool
    return ActionResult(ok=True, detail={"handle": handle, "ig_user_id": ig_user_id, "token_set": bool(token)})


# ── M4: discover → adopt — one-click onboarding from the connected providers ───────────────────────────
# Instead of hand-typing handles and pasting ids, the operator clicks Discover: FanOps lists every channel
# its connected providers (Postiz + Zernio) already hold, proposes a handle for each, and the operator ticks
# the ones to adopt. Adopt creates the account, maps the channel's id, and (confirm+creds gated) routes it to
# its provider. Matching is DETERMINISTIC (exact normalized handle or an existing integration id) — FanOps
# never silently merges two accounts on a guess; an unmatched channel is proposed NEW with an editable handle.

class DiscoveredChannel(NamedTuple):
    """One channel a connected provider already holds, proposed for adoption. `suggested_handle` is the
    deterministic normalized handle (editable by the operator); `match` is an existing account handle when
    this channel maps to one (by that exact handle OR by an already-mapped id), else None (a NEW account);
    `already_mapped` is True when an existing account already carries THIS provider id for THIS platform."""
    provider: str
    id: str
    name: str
    platform: str
    suggested_handle: str
    match: Optional[str]
    already_mapped: bool


def _norm_handle(name: str) -> str:
    """The deterministic handle proposed for a discovered channel: the name lowercased with every
    non-alphanumeric stripped (so 'Mark Makmouly' -> 'markmakmouly', matching an existing 'markmakmouly').
    A name that normalizes to empty (all punctuation/emoji) falls back to 'channel' — never blank —
    and the operator edits it before adopting."""
    from fanops.models import validate_account_handle
    body = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    h = body or "channel"
    try:
        return validate_account_handle(h)
    except ValueError:
        return h


def _match_channel(accts: Accounts, cid: str, platform: str, suggested: str) -> tuple[Optional[str], bool]:
    """DETERMINISTIC match of a discovered (id, platform) channel to an existing account (handle, already_mapped).
    By exact id FIRST (an account whose integrations[platform] or shared account_id already equals this id ->
    that handle, already_mapped=True), else by exact normalized handle (suggested == an existing account's
    normalized handle -> that handle, already_mapped=False), else (None, False) -> a NEW account. No fuzzy
    matching: FanOps never merges two accounts on a guess."""
    for a in accts.accounts:
        if a.integrations.get(platform) == cid:        # H6: platform-aware id match ONLY — the bare shared
            return a.handle, True                       # account_id (platform-agnostic) matched cross-platform,
                                                        # hiding a genuinely-new different-platform channel from adopt
    for a in accts.accounts:
        if _norm_handle(a.handle) == suggested:
            return a.handle, False
    return None, False


def discover_channels(cfg: Config) -> ActionResult:
    """List every channel the CONNECTED providers (Postiz + Zernio) already hold, each proposed for one-click
    adoption (handle + provider + id + deterministic match). FAIL-SOFT PER PROVIDER: a provider with no key is
    skipped with a note; a provider whose list call fails is noted but never aborts the other. Refused (ok=False)
    only when NEITHER provider is connected (nothing to discover). The operator confirms every row in adopt —
    discover never writes. Never 500s (a torn accounts.json degrades to no matches via load_accounts_safe)."""
    accts, _ = load_accounts_safe(cfg)                   # read-only; degrade rather than 500 on a torn registry
    channels: list[DiscoveredChannel] = []
    notes: list[str] = []
    providers = ((cfg.postiz_api_key, "postiz", postiz.postiz_list_integrations, PostizAuthError, "POSTIZ_API_KEY"),
                 (cfg.zernio_api_key, "zernio", zernio.zernio_list_accounts, ZernioAuthError, "ZERNIO_API_KEY"))
    connected = 0
    for key, name, lister, auth_exc, key_name in providers:
        if not key:
            notes.append(f"{name}: not connected (skipped)")
            continue
        connected += 1
        try:
            remote = lister(cfg)
        except auth_exc:
            notes.append(f"{name}: auth failed — check {key_name}")   # fixed text; the key is never echoed
            continue
        except Exception as exc:
            notes.append(f"{name}: could not list channels ({str(exc)[:120]})")
            continue
        for r in remote:
            if r.platform not in _PLATFORM_VALUES:               # M3: a platform FanOps can't model isn't
                notes.append(f"{name}: skipped {r.name} — unsupported platform {r.platform!r}")
                continue                                          # routable -> classify into notes, never adoptable
            suggested = _norm_handle(r.name)
            match, mapped = _match_channel(accts, r.id, r.platform, suggested)
            channels.append(DiscoveredChannel(provider=name, id=r.id, name=r.name, platform=r.platform,
                                              suggested_handle=suggested, match=match, already_mapped=mapped))
    if connected == 0:
        return ActionResult(ok=False, error="connect Postiz or Zernio first to discover channels.")
    return ActionResult(ok=True, detail={"channels": channels, "notes": notes})


def adopt_channels(cfg: Config, selections: list, confirmed: bool = False) -> ActionResult:
    """Adopt the operator-selected discovered channels: per row, ensure the account+platform exists, map the
    channel's id, and (CONFIRM + creds gated) route it to its provider. PER-ROW ISOLATED: a bad row is recorded
    and skipped, never aborting the batch. Account creation + id mapping ALWAYS happen (the channel is onboarded,
    born inert); the CONFIRM gates ONLY the provider routing — without it the channel is mapped but unrouted (it
    won't publish until routed), so a stray POST can never make a channel live. Returns counts + per-row outcomes;
    always a clean result (never 500/raises), so the htmx panel can render it.

    Each selection is a dict: {provider, id, platform, handle, persona?}. (M3: tag_lean retired.)"""
    adopted = routed = 0
    rows: list[dict] = []
    for sel in (selections or []):
        handle = (sel.get("handle") or "").strip()
        platform = (sel.get("platform") or "").strip()
        provider = (sel.get("provider") or "").strip().lower()
        cid = (sel.get("id") or "").strip()
        if not (handle and platform and provider and cid):
            rows.append({"handle": handle, "platform": platform, "ok": False, "error": "incomplete selection"})
            continue
        try:
            _accounts_ensure_channel(cfg, handle, platform, persona=(sel.get("persona") or "").strip())
            write_integration(cfg, handle, platform, cid)
            adopted += 1
            row = {"handle": handle, "platform": platform, "provider": provider, "ok": True, "routed": False}
            if provider in _LIVE_BACKENDS and confirmed and cfg.backend_has_creds(provider):
                _accounts_set_backend(cfg, handle, platform, provider)   # route this channel to its provider (live-capable)
                row["routed"] = True; routed += 1
            else:                                                        # M2: name WHY a mapped channel didn't route
                row["routing_skipped"] = ("not live-capable" if provider not in _LIVE_BACKENDS
                                          else "confirm not ticked" if not confirmed
                                          else f"{provider} not connected")
            rows.append(row)
        except (ValueError, KeyError) as exc:                # unknown platform/handle/lean — clean per-row error
            rows.append({"handle": handle, "platform": platform, "ok": False, "error": str(exc)})
        except Exception as exc:
            rows.append({"handle": handle, "platform": platform, "ok": False, "error": str(exc)[:160]})
    return ActionResult(ok=True, detail={"adopted": adopted, "routed": routed, "rows": rows})


def remove_account(cfg: Config, handle: str) -> ActionResult:
    """Remove an account ENTIRELY from the Go-Live tab (no JSON hand-edit) — clears a placeholder like
    @TBD-1 the UI couldn't delete before. Unknown handle / blank -> clean error, never a 500."""
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=False, error="no account selected")
    try:
        _accounts_remove_account(cfg, handle)
    except KeyError:
        return ActionResult(ok=False, error=f"no such account: {handle}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not remove {handle}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"removed": handle})


def demote_account(cfg: Config, handle: str) -> ActionResult:
    """Demote an account to `planned` from the Go-Live tab — it leaves active() + the publishing fan-out
    but keeps its row/history (the gentle alternative to remove for an account with live posts). Unknown
    handle / blank -> clean error."""
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=False, error="no account selected")
    try:
        _accounts_set_status(cfg, handle, "planned")
    except KeyError:
        return ActionResult(ok=False, error=f"no such account: {handle}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not demote {handle}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"demoted": handle})


def promote_account(cfg: Config, handle: str) -> ActionResult:
    """Promote a `planned`/demoted account back to `active` from the Go-Live tab — the inverse of demote, so a
    demote is no longer a silent one-way door. Unknown handle / blank -> clean error, never a 500."""
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=False, error="no account selected")
    try:
        _accounts_set_status(cfg, handle, "active")
    except KeyError:
        return ActionResult(ok=False, error=f"no such account: {handle}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not promote {handle}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"promoted": handle})


def set_persona(cfg: Config, handle: str, persona: str) -> ActionResult:
    """Set or clear ONE account's persona from the Go-Live tab (the persona was add-time-only before; editing it
    meant hand-surgery on accounts.json). A blank persona clears it. Unknown handle / blank handle -> clean error."""
    handle = (handle or "").strip()
    if not handle:
        return ActionResult(ok=False, error="no account selected")
    try:
        _accounts_set_persona(cfg, handle, persona)
    except KeyError:
        return ActionResult(ok=False, error=f"no such account: {handle}")
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not set persona for {handle}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"handle": handle})


def go_live(cfg: Config, confirmed: bool = False, *, now: "datetime | None" = None) -> ActionResult:
    """Flip the GLOBAL switch to LIVE (FANOPS_LIVE=1) — the operator's yes/no, NOT a backend pick: each
    channel publishes via its own provider (M3). The ONLY setter of FANOPS_LIVE=1. Gated, in order:
    (1) accounts.json valid (malformed/empty-id accounts -> clean error, not 500), (2) ≥1 ACTIVE channel
    has a provider whose creds are present (explicit accounts.json provider, else the legacy FANOPS_POSTER
    bridge) — flipping live with zero publishable channels would post nothing, so it's refused with a
    fix-it message, (3) M6 past-due backlog gate: NO queued post may have a scheduled_time <= `now` —
    otherwise the daemon's first live tick would machine-gun the backlog (PRD risk pinned), (4) an
    explicit confirm (the final human gate). Any failing gate leaves the system on dryrun, ATOMICALLY
    (.env + os.environ both unchanged). On success the switch is dual-written so it takes effect
    immediately AND survives a restart. Does NOT write FANOPS_POSTER — the per-channel provider is the
    source of truth. `now` is INJECTED (default: utc now) so tests can drive the past-due gate
    deterministically; mirrors actions._now / approve_posts."""
    try:
        accounts = Accounts.load(cfg)
        problems = accounts.validate()                   # malformed/empty-id accounts -> clean error, not 500
    except Exception as exc:
        return ActionResult(ok=False, error=f"accounts.json: {str(exc)[:160]}")
    if problems:
        return ActionResult(ok=False, error="not ready — accounts.json: " + "; ".join(problems))
    ready = accounts.live_ready_channels()
    if not ready:
        return ActionResult(ok=False, error="not ready — no active channel has a provider with creds. Connect "
                            "a provider (Postiz/Zernio) and route at least one channel to it, then try again.")
    # M6: PAST-DUE BACKLOG GATE. The daemon's publish_due iterates only PostState.queued posts whose
    # scheduled_time <= now. If we flip live with even ONE such post, the very next daemon tick fires
    # it — and a backlog fires every one in immediate succession (the PRD risk 'machine-gun publish').
    # REFUSE the flip until the operator respreads. Makes the bad path unconstructable: the daemon
    # cannot read FANOPS_LIVE=1 and find a past-due queued post at the same time.
    from datetime import datetime as _dt, timezone as _tz
    from fanops.ledger import Ledger as _Ledger
    from fanops.models import PostState as _PS
    from fanops.timeutil import is_due_or_past
    _now = now if now is not None else _dt.now(_tz.utc)
    try:
        _led = _Ledger.load(cfg)
    except Exception as _exc:
        # A torn ledger is the doctor's problem, not the live gate's — but it MUST be visible (R2
        # root: never silent fail-open). Log and refuse so the operator sees both signals.
        get_logger(cfg)("go_live", "-", "past_due_gate_load_failed", err=str(_exc)[:160])
        return ActionResult(ok=False, error=f"not ready — ledger unreadable: {str(_exc)[:160]}. Run `fanops doctor` first.")
    _past_due = sum(1 for _p in _led.posts.values()
                    if _p.state is _PS.queued and is_due_or_past(_p.scheduled_time, _now))
    if _past_due:
        return ActionResult(ok=False, error=(
            f"not ready — {_past_due} queued post(s) are past-due. Respread the bucket first "
            f"(Schedule → Reschedule all) so the live daemon doesn't machine-gun the backlog, "
            f"then try again."))
    if not confirmed:
        return ActionResult(ok=False, error="GO LIVE publishes to REAL accounts — tick the confirm box, "
                            "then click again.")
    err = _dual_write(cfg, "FANOPS_LIVE", "1")
    if err:
        return ActionResult(ok=False, error=err)
    # ROOT decouple (NO haphazard claude): going LIVE is the PUBLISH switch — orthogonal to the AI switch.
    # It must NOT force FANOPS_RESPONDER=llm (that silently spawned `claude` on every tick after a go-live).
    # The AI responder is enabled EXPLICITLY and separately (Go-Live → AI Responder / set_ai_responder).
    # M3c: scrape stale FANOPS_POSTER=dryrun — .env.example seeds it; pre-M3b go_dryrun wrote it;
    # M3b go_live never updated it. Studio dual-writes os.environ immediately; the resident daemon
    # loop reloads .env each tick (load_dotenv override=True). One-shot CLI/Studio restarts also
    # reload at process entry (cli.main). Operators saw LIVE=1 + POSTER=dryrun and thought the flip
    # reverted. Per-channel backends are the publish truth; an explicit dryrun global is misleading.
    _poster_disk = (_dotenv_assignment(cfg.root / ".env", "FANOPS_POSTER") or "").strip().lower()
    _poster_live = cfg.poster_backend_raw.lower()
    if _poster_disk == "dryrun" or _poster_live == "dryrun":
        if (err := _dual_unset(cfg, "FANOPS_POSTER")):
            return ActionResult(ok=False, error=err)
    # M1: a live-ready channel that resolves ONLY via the legacy FANOPS_POSTER bridge (no explicit
    # `backends`) goes dark the instant FANOPS_POSTER is unset — name them so the operator can pin the
    # provider explicitly (route the channel in the Go-Live tab; that persists `backends[platform]`).
    bridge_only = sorted({h for (h, p, _prov) in ready if accounts.resolve_backend(h, Platform(p)) is None})
    detail = {"live": True, "mode": "live", "ready": len(ready)}
    # D12: go_live NEVER writes FANOPS_POSTER — per-channel accounts.json routing is the source of truth.
    # The operator who reads .env sees FANOPS_POSTER=dryrun (or absent) after a successful flip and panics
    # that the flip reverted. Say so on the success surface: FANOPS_POSTER is a legacy bridge, not the
    # switch. (The docstring already explains it; the OPERATOR doesn't read docstrings — the UI does.)
    detail["routing_source"] = ("your channels publish via per-channel accounts.json routing — FANOPS_POSTER "
                                "is a legacy bridge only, NOT the live switch, so it's intentionally left as-is.")
    if bridge_only:
        detail["bridge_only_warning"] = ("publishes only via the legacy FANOPS_POSTER bridge (goes dark if "
                                         "it's unset) — pin a provider for: " + ", ".join(bridge_only))
    return ActionResult(ok=True, detail=detail)


def go_dryrun(cfg: Config) -> ActionResult:
    """Flip the GLOBAL switch back to dryrun (FANOPS_LIVE=0 — writes payloads, posts nothing) — the SAFE
    direction, always allowed, no confirm. Dual-written so it takes effect immediately and persists. Does
    NOT touch FANOPS_POSTER or any per-channel provider — only the global live/dryrun state."""
    err = _dual_write(cfg, "FANOPS_LIVE", "0")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"live": False, "mode": "dryrun"})


def validate_learning(cfg: Config, *, integration_id: Optional[str] = None, confirmed: bool = False) -> ActionResult:
    """Run the Postiz live-cutover (M3) from the browser to UNFREEZE the learning loop — posts ONE real
    throwaway probe to the OPERATOR-SELECTED integration, reconciles its real analytics labels, and
    writes metrics_confirmed (which learning_validated reads). Gated, in order: live-postiz + key →
    integration_id must be one the operator mapped (never auto-pick a real channel) → explicit confirm.
    NEVER 500s; the POSTIZ_API_KEY is never echoed (fixed-string auth errors). A missing metrics row is
    surfaced as 'retry later' (Postiz analytics lag), not a failure."""
    if not cfg.is_live:                                   # M3: live-vs-dryrun is the switch, not a backend pick
        return ActionResult(ok=False, error="GO LIVE first — Validate runs the Postiz cutover (a real throwaway post).")
    if not cfg.postiz_api_key:                            # Validate is Postiz-specific (lists/posts to a Postiz channel)
        return ActionResult(ok=False, error="connect Postiz first — Validate probes a Postiz channel (set POSTIZ_API_KEY).")
    integration_id = (integration_id or "").strip()
    try:
        known = {i.id for i in postiz.postiz_list_integrations(cfg)}
    except PostizAuthError:
        return ActionResult(ok=False, error="FATAL auth failure — check POSTIZ_API_KEY.")   # fixed string, never str(exc)
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not list Postiz integrations: {str(exc)[:160]}")
    if not integration_id or integration_id not in known:
        return ActionResult(ok=False, error="pick the throwaway channel to validate against (one of your mapped Postiz integrations).")
    if not confirmed:
        return ActionResult(ok=False, error="Validate posts ONE real throwaway post — tick the confirm box, then click again.")
    try:
        if not cutover.cutover_auth(cfg).get("ok"):                          # cutover_auth -> postiz_auth wraps postiz_check_auth, which returns False on a non-401 failure (unreachable/5xx) -> ok=False here
            return ActionResult(ok=False, error="Postiz auth probe failed — check POSTIZ_URL and POSTIZ_API_KEY (instance reachable?).")
        posted = cutover.cutover_post(cfg, integration_id, confirmed=True)   # the operator-SELECTED integration, never auto-picked
        sid = posted["submission_id"]
        metrics = cutover.cutover_metrics(cfg, sid)
        lift = cutover.cutover_lift(cfg, sid)
    except PostizAuthError:
        return ActionResult(ok=False, error="Postiz auth failed during validation — check POSTIZ_API_KEY.")   # fixed string
    except CutoverError as exc:
        return ActionResult(ok=False, error=str(exc))                        # cutover messages carry no key (ids/fixed text)
    except Exception as exc:
        return ActionResult(ok=False, error=f"validation failed: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"validated": True, "reconciliation": metrics.get("reconciliation"),
                                         "lift_score": lift.get("lift_score")})
