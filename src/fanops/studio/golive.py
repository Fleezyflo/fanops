"""Studio "Go Live" actions (review-first milestone 5, operator-gated half): turn FanOps from dryrun
into real publishing via Postiz ENTIRELY in the browser — connect the Postiz URL + API key, map each
account to its Postiz integration id, and flip dryrun<->live behind an explicit confirm — so a
non-technical operator never touches env vars, the CLI, or accounts.json. Kept OUT of the already-large
actions.py; imported by app.py. Reuses the live poster (post.postiz), durable config writes
(autopilot.set_env_var), readiness (doctor), and the atomic accounts writer — this module is only the
operator-facing surface over them.

THREE load-bearing invariants:
  1. DUAL-WRITE — every config change writes BOTH the .env (durable across restarts) AND os.environ
     (so THIS running Studio reflects it immediately; Config.load_dotenv ran once at startup, but the
     properties read os.getenv live). Writing only .env would silently not take effect until a restart.
  2. go_live is the ONLY setter of FANOPS_POSTER=postiz, gated on creds-present + accounts-valid + an
     explicit confirm — so a stray POST can never flip the system live. go_dryrun (the safe direction)
     needs no confirm.
  3. The POSTIZ_API_KEY is NEVER echoed, logged, or returned in an ActionResult — only a boolean
     "set". set_postiz_config tests the key by calling Postiz; it never hands it back."""
from __future__ import annotations
import os
from typing import Optional

from fanops.config import Config
from fanops.accounts import Accounts, write_integration, add_account as _accounts_add_account
from fanops.autopilot import set_env_var
from fanops.errors import PostizAuthError
from fanops.post import postiz
from fanops.studio.actions import ActionResult


def _dual_write(cfg: Config, key: str, value: str) -> Optional[str]:
    """Persist KEY=value to .env (durable) AND set os.environ[KEY] (this process) — the load-bearing
    dual-write mirrored from autopilot. One without the other is a bug: .env-only doesn't take effect
    until a restart; os.environ-only is lost on restart. Returns None on success, or an error string
    if the DURABLE write failed (disk full / read-only / a newline-bearing value rejected by
    set_env_var) — the caller surfaces it as a clean ActionResult so the Go-Live tab never 500s. On a
    durable-write failure os.environ is left UNTOUCHED (never reflect a change that won't persist)."""
    try:
        set_env_var(cfg.root / ".env", key, value)
    except (OSError, ValueError) as exc:
        return f"could not write {key} to .env: {str(exc)[:140]}"
    os.environ[key] = value
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


def add_account(cfg: Config, handle: str, platforms: list, persona: str = "") -> ActionResult:
    """Onboard a NEW account ENTIRELY in the Go-Live tab (no accounts.json hand-edit): validate a
    non-blank handle + at least one platform, then append it (status active, access postiz) so it shows
    up in the channel-mapping list immediately. Duplicate handle / unknown platform / blank input ->
    a clean one-line error, never a 500. account_id stays empty — each channel is mapped per-platform next."""
    handle = (handle or "").strip()
    platforms = [p for p in (platforms or []) if (p or "").strip()]
    if not handle:
        return ActionResult(ok=False, error="enter a handle to add an account")
    if not platforms:
        return ActionResult(ok=False, error=f"pick at least one platform for {handle}")
    try:
        _accounts_add_account(cfg, handle, platforms, persona=(persona or "").strip())
    except ValueError as exc:                            # duplicate handle / unknown platform / blank
        return ActionResult(ok=False, error=str(exc))
    except Exception as exc:
        return ActionResult(ok=False, error=f"could not add {handle}: {str(exc)[:160]}")
    return ActionResult(ok=True, detail={"added": handle, "platforms": platforms})


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


def go_live(cfg: Config, confirmed: bool = False) -> ActionResult:
    """Flip the poster to postiz (LIVE) — the ONLY setter of FANOPS_POSTER=postiz. Gated, in order:
    (1) POSTIZ_URL + POSTIZ_API_KEY present (checked DIRECTLY, not via doctor — doctor only emits the
    postiz check once the backend is already postiz, and we're still on dryrun here), (2) accounts.json
    valid with every active account mapped to an id, (3) an explicit confirm (the final human gate).
    Any failing gate refuses with the specific reason and leaves the backend on dryrun. On success the
    switch is dual-written so it takes effect immediately AND survives a restart."""
    missing = [k for k, v in (("POSTIZ_URL", cfg.postiz_url), ("POSTIZ_API_KEY", cfg.postiz_api_key)) if v is None]
    if missing:
        return ActionResult(ok=False, error="not ready — set " + " + ".join(missing)
                            + " first (Connect Postiz above).")
    try:
        problems = Accounts.load(cfg).validate()         # malformed/empty-id accounts -> clean error, not 500
    except Exception as exc:
        return ActionResult(ok=False, error=f"accounts.json: {str(exc)[:160]}")
    if problems:
        return ActionResult(ok=False, error="not ready — accounts.json: " + "; ".join(problems))
    if not confirmed:
        return ActionResult(ok=False, error="GO LIVE publishes to REAL accounts — tick the confirm box, "
                            "then click again.")
    err = _dual_write(cfg, "FANOPS_POSTER", "postiz")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"mode": "postiz", "live": True})


def go_dryrun(cfg: Config) -> ActionResult:
    """Flip back to dryrun (writes payloads, posts nothing) — the SAFE direction, always allowed, no
    confirm. Dual-written so it takes effect immediately and persists."""
    err = _dual_write(cfg, "FANOPS_POSTER", "dryrun")
    if err:
        return ActionResult(ok=False, error=err)
    return ActionResult(ok=True, detail={"mode": "dryrun", "live": False})
