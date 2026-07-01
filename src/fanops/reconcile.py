"""Reconcile stage (AUDIT H4). Resolves posts stranded in `submitting` (crash mid-publish, FIX
F11) or `needs_reconcile` (ambiguous 5xx / network timeout after the body was sent, AUDIT C1) by
polling each backend for the post's terminal status. Zernio offers a real per-post lookup
(GET /posts/{postSubmissionId} -> status in-progress|failed|published|scheduled + publicUrl); Postiz
has no per-post endpoint, so it reads the `state` field off the DATE-WINDOWED GET /public/v1/posts.
Either way the poll REQUIRES the submission id.

Consequence (the honest boundary): AUDIT H1 (Phase D) stamps EVERY crossposted post with a client
idempotency token (submission_id="fanops_..."), so a post parked after a pure network timeout is no
longer id-less — it carries a fanops_ token and IS polled. But a fanops_ token is not a real backend
postSubmissionId, so that poll 404s; the per-post try/except below CONTAINS that error (leaves the
post parked, never failed — a poll error is not evidence it failed) so the pass continues. A post with
genuinely NO submission_id at all (older data) is still SKIPPED for human reconcile (the digest
surfaces it). A real postSubmissionId from an ambiguous-5xx body overwrites the token, making that post
cleanly auto-reconcilable. We never guess a post's fate — a wrong guess either drops a live post
(untrackable) or re-queues a live one (double-publish), the exact C1/cascade hazards.

Resolution:
  status 'published'        -> PostState.published (+ public_url) so track can later measure it
  status 'failed'           -> PostState.failed (definitely not live -> safe to re-queue)
  'in-progress'/'scheduled' -> leave as-is (not yet resolved; a later pass retries)

Backend-agnostic by design (P2). The poll is dispatched per backend in _default_get_status: Zernio over
GET /posts/{id} (a bound method); Postiz over the DATE-WINDOWED GET /public/v1/posts `state` field
(PostizStatusClient). The {status, publicUrl} dict and the state machine here are identical for both — a
PUBLISHED Postiz row carries its real IG permalink in `releaseURL`, which PostizStatusClient surfaces as
publicUrl (verified against the running instance 2026-06-21, metrics.py), so reconcile stamps a published
Postiz post's public_url; `fanops resolve <id> published --url` is the manual fallback for a post genuinely
absent from its date-window page (status 'unknown' -> left parked, never guessed). A FATAL auth failure
from EITHER backend (the shared AuthError base) halts the pass; a single poll error is contained per-post
(parked, never guessed failed). dryrun never reaches here (gated upstream)."""
from __future__ import annotations
from typing import Callable, Optional
from fanops.config import Config
from fanops.errors import AuthError
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import PostState, is_real_submission_id
from fanops.text import safe_public_url
from fanops.timeutil import parse_iso, iso_z, publish_buckets
from datetime import datetime, timezone, timedelta

_STUCK_AFTER = timedelta(hours=6)   # H4: a still-parked post older than this past its schedule gets a breadcrumb
# XC-1: a `submitting` post still un-poll-resolvable this long past its schedule is a crash-stranded CLAIM
# (post/run.py marks submitting + persists BEFORE the network; a mid-network crash leaves it here, and
# publish_due never re-drives a non-`queued` post). Escalate it to needs_reconcile so the digest's reconcile
# column owns it instead of a perpetual in-flight-submit. 6h covers any real slow submit; 24h is unambiguous.
_SUBMITTING_ESCALATE_AFTER = timedelta(hours=24)
# Sprint 4: submitting with no submission_id cannot be polled — heal to queued after grace.
_SUBMITTING_HEAL_AFTER = timedelta(minutes=15)
# XC-2: a needs_reconcile post still only-poll-erroring this long past its schedule on a never-real token can
# never auto-resolve (a fanops_ token 404s forever). Stamp an explicit GIVE-UP terminal marker (verify by hand)
# rather than re-polling an id that cannot resolve. 72h = three days, well past any backend's settle window.
_RECONCILE_GIVEUP_AFTER = timedelta(hours=72)
# The sentinel prefix on error_reason that marks a needs_reconcile post as a labeled TERMINAL (gave-up): the
# poll loop skips it (no further network), and the digest still surfaces it for manual verification. Distinct
# from the transient "reconcile poll error:" / "stuck …" breadcrumbs, which do NOT stop the poll.
_GIVEUP_PREFIX = "GAVE UP:"


def _parked_age(post, now: datetime):
    """now - scheduled_time for a parked post; None if there's no/invalid schedule (-> no breadcrumb, never a
    false alarm). The post is submitted when scheduled_time <= now, so this is a sound 'stuck since' proxy."""
    if not post.scheduled_time:
        return None
    try:
        return now - parse_iso(post.scheduled_time)
    except Exception:
        return None


def _is_fake_token(post) -> bool:
    """True iff the post still carries its BIRTH client idempotency token (crosspost.py: `fanops_…`), i.e. a
    real backend postSubmissionId never overwrote it. Such a token 404s on every poll forever, so a
    post that ONLY ever poll-errors on it can never auto-resolve — the precondition for escalation. A post
    carrying a real id is left to its normal poll (its status WILL resolve), never escalated."""
    return bool(post.submission_id) and post.submission_id.startswith("fanops_")


def _is_giveup(post) -> bool:
    """True iff this post already carries the gave-up terminal marker. A give-up post is a LABELED terminal
    (we stopped auto-reconciling it); the poll loop skips it so it never re-polls a dead token or re-stamps an
    identical line (XC-6). The operator clears it via `fanops resolve <id> published|failed --url` by hand."""
    return bool(post.error_reason) and post.error_reason.startswith(_GIVEUP_PREFIX)


# ---- Leg 2 (Insight) identify: resolve each live IG post's Graph media_id from its permalink ----------

_MEDIA_ID_UNMATCHED = "media_id_unmatched: no live IG media permalink matched this post's public_url (re-resolving next pass)"

def _norm_permalink(url: Optional[str]) -> Optional[str]:
    """Canonical key for matching a stored public_url to a Graph media `permalink`: `host_without_www + path`,
    lowercased, no trailing slash. Both are always-https public IG permalinks, differing only in a leading
    `www.` or a trailing `/` — normalizing those makes the match exact without guessing. None on a non-https /
    malformed value (safe_public_url rejects it) so a bad URL never collides with another post's real one."""
    ok = safe_public_url(url)
    if ok is None:
        return None
    from urllib.parse import urlparse
    u = urlparse(ok)
    host = u.netloc.lower()
    if host.startswith("www."): host = host[4:]
    path = u.path.rstrip("/")
    return f"{host}{path}" if host else None

def _pick_media(cands: list[dict], post) -> Optional[dict]:
    """From ≥1 live media sharing a post's normalized permalink, pick the one whose `timestamp` is nearest the
    post's `published_at` (a re-share can duplicate a permalink; the nearest ship time is the real match, never
    first-seen). One candidate -> it. No parseable published_at or no media timestamps -> first (stable)."""
    if len(cands) == 1:
        return cands[0]
    try:
        pub = parse_iso(post.published_at) if post.published_at else None
    except Exception:
        pub = None
    if pub is None:
        return cands[0]
    def _dist(m):
        try:
            return abs((parse_iso(m.get("timestamp")) - pub).total_seconds())
        except Exception:
            return float("inf")
    return min(cands, key=_dist)

def resolve_media_ids(led: Ledger, cfg: Config, *, get=None) -> Ledger:
    """Stamp each published/analyzed IG post's Graph `media_id` by matching its permalink against the live
    /{ig_user}/media list. Runs INSIDE the automatic pull path (pull_metrics) so the unattended daemon
    resolves new posts itself — the sole-source insights read keys on media_id, so an unresolved post is
    invisible to it. Idempotent (a post already carrying a media_id is skipped) and fail-open (an empty media
    list resolves nobody, never crashes). An unmatched post is breadcrumbed, NEVER given a fabricated id."""
    from fanops.models import Platform
    from fanops import meta_graph
    log = get_logger(cfg)
    targets = [p for p in led.posts.values()
               if p.platform is Platform.instagram and p.media_id is None
               and p.state in (PostState.published, PostState.analyzed)
               and _norm_permalink(p.public_url) is not None]
    if not targets:
        return led                                               # nothing to resolve -> no network call
    media = meta_graph.list_user_media(cfg, get=get)
    if not media:
        return led                                               # couldn't enumerate (no creds / transport) ->
        #                                                          stay re-resolvable, DON'T false-breadcrumb "unmatched"
    by_key: dict[str, list[dict]] = {}
    for m in media:
        k = _norm_permalink(m.get("permalink"))
        if k: by_key.setdefault(k, []).append(m)
    for p in targets:
        cands = by_key.get(_norm_permalink(p.public_url))
        if cands:
            picked = _pick_media(cands, p)
            mid = picked.get("id")
            # stamp the media's REAL product_type alongside media_id (same live record) so the insights
            # request is derived from what the media IS — a feed video is never asked for a reels-only metric.
            led.posts[p.id] = p.model_copy(update={"media_id": mid,
                                                   "product_type": picked.get("media_product_type")})
            log("reconcile", p.id, "media_id_resolved", media_id=mid,
                product_type=picked.get("media_product_type"))
        else:
            # we DID enumerate live media and this permalink wasn't among them: honest miss -> keep
            # re-resolvable (media_id stays None) + breadcrumb; never guess an id.
            led.posts[p.id] = p.model_copy(update={"error_reason": _MEDIA_ID_UNMATCHED})
            log("reconcile", p.id, "media_id_unmatched")
    return led

# States whose true outcome is unknown and pollable: a publish was (or may have been) sent.
_RECONCILABLE = (PostState.submitting, PostState.submitted, PostState.needs_reconcile)
GetStatus = Callable[[str], dict]
_LIVE_STATUS_BACKENDS = frozenset({"postiz", "zernio"})


def _status_client_for(cfg: Config, backend: str, led: Optional[Ledger]) -> GetStatus:
    # One backend's status poller. Zernio has a TRUE per-post status endpoint (a bound get_status, no
    # date window). Postiz has NONE: its only signal is the `state` field on a row of the DATE-WINDOWED
    # GET /public/v1/posts, so it wraps PostizStatusClient in a closure that reads the parked post's own
    # scheduled_time from `led` for the window (a future/old/2099 post is otherwise PERMANENTLY off the
    # default ~week page). An unknown backend FAILS CLOSED + legibly (a stale FANOPS_POSTER already
    # degrades to dryrun at cfg, W4). Lazy imports keep deps off the core path.
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
    raise ValueError(f"unknown backend {backend!r}: no status client (expected postiz/zernio)")


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


def _poll_backend_for_sid(cfg: Config, routing: dict[str, str], sid: str) -> str:
    """Resolve which status client owns this submission — never dryrun (fails closed)."""
    b = routing.get(sid)
    if b in _LIVE_STATUS_BACKENDS:
        return b
    g = cfg.poster_backend
    if g in _LIVE_STATUS_BACKENDS:
        return g
    raise RuntimeError("reconcile: no live status backend (global dryrun / channel has no provider)")


def _default_get_status(cfg: Config, led: Optional[Ledger] = None) -> GetStatus:
    # Per-post backend routing (zernio). When the reconcilable posts in `led` resolve to MORE THAN ONE
    # backend (an account override + the global), route each submission to its own backend's status client
    # — so IG-via-Postiz and TikTok-via-Zernio reconcile in ONE pass. With one backend (or no led) this is
    # the single-backend dispatch (Zernio bound method, Postiz date-window closure), so the Zernio
    # bound-method check + the existing reconcile tests are byte-identical.
    routing = _reconcilable_routing(cfg, led)
    backends = set(routing.values())
    if not backends:
        g = cfg.poster_backend
        if g in _LIVE_STATUS_BACKENDS:
            backends = {g}
        else:
            def poll(sid: str) -> dict:
                raise RuntimeError("reconcile: no live status backend (global dryrun / channel has no provider)")
            return poll
    if len(backends) <= 1:
        return _status_client_for(cfg, next(iter(backends)), led)
    pollers = {b: _status_client_for(cfg, b, led) for b in backends}
    def poll(sid: str) -> dict:
        backend = _poll_backend_for_sid(cfg, routing, sid)
        return (pollers.get(backend) or _status_client_for(cfg, backend, led))(sid)
    return poll


def heal_stranded_submitting(cfg: Config, *, now: Optional[datetime] = None) -> int:
    """Crash-stranded `submitting` posts with no submission_id -> `queued` after a grace window.
    Nothing was pollable; publish_due never re-drives submitting. Returns count healed."""
    now = now or datetime.now(timezone.utc)
    healed = 0
    with Ledger.transaction(cfg) as led:
        for p in list(led.posts.values()):
            if p.state is not PostState.submitting:
                continue
            if (p.submission_id or "").strip():
                continue
            age = _parked_age(p, now)
            if age is None or age < _SUBMITTING_HEAL_AFTER:
                continue
            led.posts[p.id] = p.model_copy(update={"state": PostState.queued, "error_reason": None})
            healed += 1
            get_logger(cfg)("reconcile", p.id, "healed: submitting->queued", reason="no_submission_id")
    return healed


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
    healed = heal_stranded_submitting(cfg)
    routing = _reconcilable_routing(cfg, snapshot)
    log = get_logger(cfg)
    reconcilable = []
    for p in snapshot.posts.values():
        if p.state not in _RECONCILABLE or not p.submission_id:
            continue
        try:
            _poll_backend_for_sid(cfg, routing, p.submission_id)
        except RuntimeError:
            log("reconcile", p.id, "skipped: no live provider")
            continue
        reconcilable.append(p)
    if not reconcilable:
        return {"needs_reconcile": len(snapshot.posts_in_state(PostState.needs_reconcile)),
                "published": len(snapshot.posts_in_state(PostState.published)),
                "healed_submitting": healed}
    poll = _default_get_status(cfg, snapshot)            # only built when work exists; never dryrun (fails closed)
    from fanops.postiz_lifecycle import ensure_up        # reconcilable>0: bring the local Postiz stack up to poll
    ensure_up(cfg)
    results: dict[str, object] = {}                      # sid -> info dict OR captured Exception
    for p in reconcilable:
        try:
            results[p.submission_id] = poll(p.submission_id) or {}   # network, NO lock held
        except AuthError:
            raise                                        # bad key (Postiz OR Zernio): every poll fails -> halt
        except Exception as exc:
            results[p.submission_id] = exc               # capture; re-raised in apply -> parked (never guessed failed)
    def cached(sid: str) -> dict:
        r = results.get(sid, {})
        if isinstance(r, Exception): raise r             # reconcile_posts' per-post except parks it + logs
        return r
    with Ledger.transaction(cfg) as led:
        led = reconcile_posts(led, cfg, get_status=cached)
        return {"needs_reconcile": len(led.posts_in_state(PostState.needs_reconcile)),
                "published": len(led.posts_in_state(PostState.published)),
                "healed_submitting": healed}


def reconcile_posts(led: Ledger, cfg: Config, *, get_status: Optional[GetStatus] = None,
                    now: Optional[datetime] = None) -> Ledger:
    poll = get_status or _default_get_status(cfg, led)
    now = now or datetime.now(timezone.utc)               # clock injected by tests; real callers default to UTC now
    log = get_logger(cfg)
    for post in [p for p in led.posts.values() if p.state in _RECONCILABLE]:
        if _is_giveup(post):
            continue                       # XC-2/XC-6: a labeled-terminal post is no longer polled or re-logged
        if not post.submission_id:
            log("reconcile", post.id, "skipped: no submission_id")
            continue                       # no id -> cannot poll (API needs it) -> human reconcile
        # Per-post resilience (mirrors publish_due, run.py:70-76): one post's poll error must NOT
        # abort the whole pass. AUDIT H1 made this load-bearing — D1 stamps EVERY post with a CLIENT
        # idempotency token (submission_id = "fanops_..."), so a post parked after a pure network
        # timeout carries a fanops_ token that is NOT a real backend postSubmissionId. Polling it
        # 404s -> the status client's get_status raises RuntimeError. Uncaught, that raise escapes
        # reconcile_posts and strands every genuinely-published post LATER in iteration order
        # (order-dependent availability bug). Contain it to THIS post instead.
        try:
            info = poll(post.submission_id) or {}
        except AuthError:
            raise                          # bad key/401 (Postiz OR Zernio): EVERY poll will fail ->
                                           # halt, don't grind. Type-matched on the shared AuthError
                                           # base (P2) so any backend's 401 halts.
                                           # the ledger recording a bogus error on every parked post
        except Exception as exc:
            # A single poll failure (e.g. a 404 on a not-yet-real fanops_ token) is NOT evidence the
            # post failed — it MAY be live. Honor the prime directive: never guess a post's fate.
            # Leave it parked (state untouched, NOT failed) and surface the reason for the digest;
            # a later pass retries. Then move on so the next post still gets reconciled. Immutable
            # update (model_copy + dict reassignment), mirroring the ledger's own set_*_state pattern.
            led.posts[post.id] = post.model_copy(update={"error_reason": f"reconcile poll error: {str(exc)[:200]}"})
            log("reconcile", post.id, "poll-error", err=str(exc)[:200])   # detail rides the log stream, not only the ledger
            continue
        status = (info.get("status") or "").lower()
        if status == "published":
            # CULM-3: capture the REAL backend id the poll surfaced (Postiz / Zernio postSubmissionId),
            # preferring it over the birth fanops_ token; never overwrite an already-real id with None.
            real = next((info[k] for k in ("postSubmissionId", "id", "submissionId")
                         if is_real_submission_id(info.get(k))), None)
            new_sub = real or (post.submission_id if is_real_submission_id(post.submission_id) else None)
            # R1: a 'published' reconcile that captures NO valid URL (M2 safe_public_url rejected
            # the malformed one) AND the post had no prior URL is the SAME ghost-row class as
            # _publish_one's submitted-no-url case. Park in needs_reconcile so the next poll can
            # back-fill a real https permalink — never promote to published with public_url=None
            # (the Pydantic R1 invariant would refuse the save below; fail-closed BEFORE construction).
            captured_url = safe_public_url(info.get("publicUrl")) or post.public_url
            if not (captured_url or "").strip():
                led.posts[post.id] = post.model_copy(update={
                    "state": PostState.needs_reconcile,
                    "error_reason": ("publish_missing_url_at_reconcile: backend reports published but no valid "
                                     "https url captured (M2 safe_public_url rejected it); re-polling next pass"),
                })
                log("reconcile", post.id, "published_no_url_parked")
                continue
            upd = {"state": PostState.published,
                   "public_url": captured_url,
                   "error_reason": None}                  # a transient poll-error reason must not survive a successful publish
            if not (post.published_at or "").strip():
                upd["published_at"] = iso_z(now)         # mirror _publish_one: reconcile-only promote must carry a ship stamp
            # Leg 3 (timing): bucket the ship stamp into operator-local (hour, weekday) — mirror _publish_one
            # so a reconcile-recovered publish is rankable by timing_bias too. Uses the published_at we
            # just set (else the pre-existing one). Fail-safe (None,None) leaves the dim unranked.
            _ph, _pd = publish_buckets(upd.get("published_at") or post.published_at, cfg)
            upd["publish_hour"], upd["publish_dow"] = _ph, _pd
            if new_sub: upd["submission_id"] = new_sub
            led.posts[post.id] = post.model_copy(update=upd)
            if new_sub is None:                           # published but still no real id -> attribution can't bind
                log("reconcile", post.id, "published_no_real_id")   # first-class: a logged outcome, not silence
            try:                                          # CULM-Q3: archive includes reconcile-recovered posts
                from fanops.post.run import _archive_published   # lazy: reconcile must not import the publish stage eagerly
                _archive_published(cfg, led.posts[post.id])
            except Exception as exc:
                log("reconcile", post.id, "archive_error", err=str(exc)[:120])   # fail-open: never block a recovered publish
            log("reconcile", post.id, "published")
        elif status == "failed":
            led.posts[post.id] = post.model_copy(update={
                "state": PostState.failed,
                "error_reason": f"reconciled: poster reports failed ({info.get('errorMessage', 'no detail')})"})
            log("reconcile", post.id, "failed")
        else:
            # in-progress / scheduled / unknown -> not auto-resolved this pass; never guess the fate.
            age = _parked_age(post, now)
            # XC-1: a crash-stranded `submitting` on a never-real token, past the escalation deadline, hands off
            # to the reconcile-column owner (needs_reconcile). Checked BEFORE the dedup guard below so it fires
            # even when an earlier pass already stamped a `stuck …` breadcrumb. State change is logged once
            # (the post stops being submitting, so it never re-takes this branch). Never -> a re-queueable state.
            if age is not None and _is_fake_token(post) and post.state is PostState.submitting \
                    and age > _SUBMITTING_ESCALATE_AFTER:
                led.posts[post.id] = post.model_copy(update={
                    "state": PostState.needs_reconcile,
                    "error_reason": (f"escalated submitting->needs_reconcile after "
                                     f"{int(age.total_seconds() // 3600)}h (crash-stranded submit, token "
                                     "never resolved) — verify on the channel before any resubmit")})
                log("reconcile", post.id, "escalated: submitting->needs_reconcile")
                continue
            # XC-2: a needs_reconcile post on a never-real token past the long bound -> labeled GIVE-UP terminal.
            # Logged once (the loop-head _is_giveup skip means this post is never re-reached on the next pass).
            if age is not None and _is_fake_token(post) and post.state is PostState.needs_reconcile \
                    and age > _RECONCILE_GIVEUP_AFTER:
                led.posts[post.id] = post.model_copy(update={"error_reason": (
                    f"{_GIVEUP_PREFIX} unresolved {int(age.total_seconds() // 3600)}h past schedule on a "
                    "never-real token — gave up auto-reconcile; verify on the channel manually")})
                log("reconcile", post.id, "gave-up: needs_reconcile terminal")
                continue
            # XC-6: a post that already carries a surfaced reason (a prior `stuck …` breadcrumb or a contained
            # `reconcile poll error:`) is NOT re-stamped or re-logged — that per-pass repeat was the alert
            # fatigue. It stays parked, silently, until it resolves / escalates / is given up.
            if post.error_reason:
                continue
            # First un-reasoned visit: stamp the stuck breadcrumb if it's now well past schedule, then log the
            # visit ONCE. A scheduleless post (age None) is never breadcrumbed (deadline unmeasurable) but the
            # visit is still audit-logged so a monitor sees it was reconciled.
            if age is not None and age > _STUCK_AFTER:
                hrs = int(age.total_seconds() // 3600)
                led.posts[post.id] = post.model_copy(update={"error_reason": (
                    f"stuck {status or 'unknown'} ~{hrs}h past schedule — check the channel "
                    "(publish may have silently failed)")})
            log("reconcile", post.id, f"left: {status or 'unknown'}")
    return led
