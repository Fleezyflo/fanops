"""Reconcile stage (AUDIT H4). Resolves posts stranded in `submitting` (crash mid-publish, FIX
F11) or `needs_reconcile` (ambiguous 5xx / network timeout after the body was sent, AUDIT C1) by
polling Blotato `GET /v2/posts/{postSubmissionId}` — the ONLY lookup the API offers (status enum
in-progress|failed|published|scheduled + publicUrl/errorMessage, VERIFIED against the live Blotato
`get_post_status` MCP tool schema 2026-06-02, AUDIT D5). It REQUIRES the submission id.

Consequence (the honest boundary): AUDIT H1 (Phase D) stamps EVERY crossposted post with a client
idempotency token (submission_id="fanops_..."), so a post parked after a pure network timeout is no
longer id-less — it carries a fanops_ token and IS polled. But a fanops_ token is not a real Blotato
postSubmissionId, so that poll 404s; the per-post try/except below CONTAINS that error (leaves the
post parked, never failed — a poll error is not evidence it failed) so the pass continues. A post with
genuinely NO submission_id at all (older data) is still SKIPPED for human reconcile (the digest
surfaces it). A real postSubmissionId from an ambiguous-5xx body (blotato_rest.py) overwrites the
token, making that post cleanly auto-reconcilable. We never guess a post's fate — a wrong guess either
drops a live post (untrackable) or re-queues a live one (double-publish), the exact C1/cascade hazards.

Resolution:
  status 'published'        -> PostState.published (+ public_url) so track can later measure it
  status 'failed'           -> PostState.failed (definitely not live -> safe to re-queue)
  'in-progress'/'scheduled' -> leave as-is (not yet resolved; a later pass retries)

Backend-agnostic by design (P2). The poll is dispatched per backend in _default_get_status: Blotato
(rest/mcp) over GET /v2/posts/{id}; Postiz over the DATE-WINDOWED GET /public/v1/posts `state` field
(PostizStatusClient). The {status, publicUrl} dict and the state machine here are identical for both —
only honesty boundary differs: Postiz exposes NO permalink on any response, so a reconciled Postiz post
keeps public_url=None (the operator sets the real social URL via `fanops resolve <id> published --url`).
A FATAL auth failure from EITHER backend (the shared AuthError base) halts the pass; a single poll error
is contained per-post (parked, never guessed failed). dryrun never reaches here (gated upstream)."""
from __future__ import annotations
from typing import Callable, Optional
from fanops.config import Config
from fanops.errors import AuthError
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import PostState
from fanops.text import safe_public_url
from fanops.timeutil import parse_iso
from datetime import datetime, timezone, timedelta

_STUCK_AFTER = timedelta(hours=6)   # H4: a still-parked post older than this past its schedule gets a breadcrumb


def _parked_age(post, now: datetime):
    """now - scheduled_time for a parked post; None if there's no/invalid schedule (-> no breadcrumb, never a
    false alarm). The post is submitted when scheduled_time <= now, so this is a sound 'stuck since' proxy."""
    if not post.scheduled_time:
        return None
    try:
        return now - parse_iso(post.scheduled_time)
    except Exception:
        return None

# States whose true outcome is unknown and pollable: a publish was (or may have been) sent.
_RECONCILABLE = (PostState.submitting, PostState.submitted, PostState.needs_reconcile)
GetStatus = Callable[[str], dict]


def _status_client_for(cfg: Config, backend: str, led: Optional[Ledger]) -> GetStatus:
    # One backend's status poller. Blotato (rest/mcp) and Zernio have a TRUE per-post status endpoint
    # (a bound get_status, no date window). Postiz has NEITHER: its only signal is the `state` field on a
    # row of the DATE-WINDOWED GET /public/v1/posts, so it wraps PostizStatusClient in a closure that reads
    # the parked post's own scheduled_time from `led` for the window (a future/old/2099 post is otherwise
    # PERMANENTLY off the default ~week page). Lazy imports keep deps off the core path.
    if backend == "postiz":
        from fanops.post.metrics import PostizStatusClient
        client = PostizStatusClient(cfg)
        def poll(sid: str) -> dict:
            post = next((p for p in led.posts.values() if p.submission_id == sid), None) if led else None
            return client.get_status(sid, publish_date=post.scheduled_time if post else None)
        return poll
    if backend == "zernio":
        from fanops.post.metrics import ZernioStatusClient
        return ZernioStatusClient(cfg).get_status
    from fanops.post.metrics import BlotatoStatusClient
    return BlotatoStatusClient(cfg).get_status


def _reconcilable_routing(cfg: Config, led: Optional[Ledger]) -> dict[str, str]:
    # submission_id -> RESOLVED backend (accounts.json `backends` override -> else the global FANOPS_POSTER)
    # for every reconcilable post that HAS a submission id. Empty when led is None. Accounts load is guarded:
    # a corrupt accounts.json must NOT crash the reconcile read (publish surfaces it loudly) — degrade to
    # the global backend for every post + log.
    if led is None:
        return {}
    from fanops.accounts import load_accounts_safe
    accounts, err = load_accounts_safe(cfg)
    if err: get_logger(cfg)("backend_route", "accounts", "load_failed_global_fallback", err=err)
    # H1: per-channel provider (effective_provider), NOT `resolve_backend or global` — so a live channel's
    # status polls hit ITS provider (zernio/postiz) even when FANOPS_POSTER is unset. A post whose channel
    # has no provider is SKIPPED (never dryrun-routed -> never silently stranded against the wrong client).
    return {p.submission_id: prov
            for p in led.posts.values() if p.state in _RECONCILABLE and p.submission_id
            and (prov := accounts.effective_provider(p.account, p.platform))}


def _default_get_status(cfg: Config, led: Optional[Ledger] = None) -> GetStatus:
    # Per-post backend routing (zernio). When the reconcilable posts in `led` resolve to MORE THAN ONE
    # backend (an account override + the global), route each submission to its own backend's status client
    # — so IG-via-Postiz and TikTok-via-Zernio reconcile in ONE pass. With one backend (or no led) this is
    # the UNCHANGED single-backend dispatch (Blotato/Zernio bound method, Postiz date-window closure), so
    # the blotato bound-method check + the 30+ existing reconcile tests are byte-identical.
    routing = _reconcilable_routing(cfg, led)
    backends = set(routing.values())
    if len(backends) <= 1:
        return _status_client_for(cfg, next(iter(backends)) if backends else cfg.poster_backend, led)
    pollers = {b: _status_client_for(cfg, b, led) for b in backends}
    def poll(sid: str) -> dict:
        backend = routing.get(sid, cfg.poster_backend)
        return (pollers.get(backend) or _status_client_for(cfg, backend, led))(sid)
    return poll


def reconcile_due(cfg: Config) -> dict[str, int]:
    """Reconcile stranded posts with the per-post status POLLS (network) OUTSIDE the ledger flock —
    only the apply runs inside a tight transaction (mirrors cmd_reconcile; M1, same fix as publish #89).
    Pre-poll each reconcilable post's status against a lock-free snapshot, then hand the CACHED results
    to reconcile_posts inside ONE Ledger.transaction (it re-checks each post's CURRENT state under the
    lock, so a post that changed between poll and apply is handled correctly). A single poll error is
    CAPTURED and re-raised inside the apply so reconcile_posts' per-post containment (park, set
    error_reason, never guess failed) is preserved byte-for-byte; a FATAL AuthError halts the pass.
    Empty/not-stranded -> no transaction. Caller gates on backend/key. Returns the resolved counts.
    `_default_get_status` may raise (no key) — the caller decides whether that's 'skip clean'."""
    snapshot = Ledger.load(cfg)
    poll = _default_get_status(cfg, snapshot)            # built FIRST so the not-configured raise (no key
                                                         # -> caller skips clean) is independent of whether
                                                         # anything is stranded; cheap (no network on build)
    reconcilable = [p for p in snapshot.posts.values() if p.state in _RECONCILABLE and p.submission_id]
    if not reconcilable:
        return {"needs_reconcile": len(snapshot.posts_in_state(PostState.needs_reconcile)),
                "published": len(snapshot.posts_in_state(PostState.published))}
    results: dict[str, object] = {}                      # sid -> info dict OR captured Exception
    for p in reconcilable:
        try:
            results[p.submission_id] = poll(p.submission_id) or {}   # network, NO lock held
        except AuthError:
            raise                                        # bad key (Blotato OR Postiz): every poll fails -> halt
        except Exception as exc:
            results[p.submission_id] = exc               # capture; re-raised in apply -> parked (never guessed failed)
    def cached(sid: str) -> dict:
        r = results.get(sid, {})
        if isinstance(r, Exception): raise r             # reconcile_posts' per-post except parks it + logs
        return r
    with Ledger.transaction(cfg) as led:
        led = reconcile_posts(led, cfg, get_status=cached)
        return {"needs_reconcile": len(led.posts_in_state(PostState.needs_reconcile)),
                "published": len(led.posts_in_state(PostState.published))}


def reconcile_posts(led: Ledger, cfg: Config, *, get_status: Optional[GetStatus] = None,
                    now: Optional[datetime] = None) -> Ledger:
    poll = get_status or _default_get_status(cfg, led)
    now = now or datetime.now(timezone.utc)               # clock injected by tests; real callers default to UTC now
    log = get_logger(cfg)
    for post in [p for p in led.posts.values() if p.state in _RECONCILABLE]:
        if not post.submission_id:
            log("reconcile", post.id, "skipped: no submission_id")
            continue                       # no id -> cannot poll (API needs it) -> human reconcile
        # Per-post resilience (mirrors publish_due, run.py:70-76): one post's poll error must NOT
        # abort the whole pass. AUDIT H1 made this load-bearing — D1 stamps EVERY post with a CLIENT
        # idempotency token (submission_id = "fanops_..."), so a post parked after a pure network
        # timeout carries a fanops_ token that is NOT a real Blotato postSubmissionId. Polling it
        # 404s -> BlotatoStatusClient.get_status raises RuntimeError. Uncaught, that raise escapes
        # reconcile_posts and strands every genuinely-published post LATER in iteration order
        # (order-dependent availability bug). Contain it to THIS post instead.
        try:
            info = poll(post.submission_id) or {}
        except AuthError:
            raise                          # bad key/401 (Blotato OR Postiz): EVERY poll will fail ->
                                           # halt, don't grind. Widened from BlotatoAuthError to the
                                           # shared AuthError base (P2) so a Postiz 401 also halts.
                                           # the ledger recording a bogus error on every parked post
        except Exception as exc:
            # A single poll failure (e.g. a 404 on a not-yet-real fanops_ token) is NOT evidence the
            # post failed — it MAY be live. Honor the prime directive: never guess a post's fate.
            # Leave it parked (state untouched, NOT failed) and surface the reason for the digest;
            # a later pass retries. Then move on so the next post still gets reconciled.
            post.error_reason = f"reconcile poll error: {str(exc)[:200]}"
            log("reconcile", post.id, "poll-error", err=str(exc)[:200])   # detail rides the log stream, not only the ledger
            continue
        status = (info.get("status") or "").lower()
        if status == "published":
            post.state = PostState.published
            post.public_url = safe_public_url(info.get("publicUrl")) or post.public_url   # M2: https-only or keep existing
            post.error_reason = None                      # a transient poll-error reason must not survive a successful publish
            log("reconcile", post.id, "published")
        elif status == "failed":
            post.state = PostState.failed
            post.error_reason = f"reconciled: poster reports failed ({info.get('errorMessage', 'no detail')})"
            log("reconcile", post.id, "failed")
        else:
            # in-progress / scheduled / unknown -> leave parked (never guess the fate); a later pass retries.
            # H4: a post stuck here across many passes would otherwise be SILENT (no digest section, no
            # error_reason). Once it's parked well past its schedule, stamp an age breadcrumb so it surfaces
            # in the digest's error column — WITHOUT changing state (its fate is genuinely unknown, not failed).
            age = _parked_age(post, now)
            if age is not None and age > _STUCK_AFTER:
                hrs = int(age.total_seconds() // 3600)
                post.error_reason = (f"stuck {status or 'unknown'} ~{hrs}h past schedule — check the channel "
                                     "(publish may have silently failed)")
            log("reconcile", post.id, f"left: {status or 'unknown'}")
    return led
