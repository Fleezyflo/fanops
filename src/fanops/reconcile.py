"""Reconcile stage (AUDIT H4). Resolves posts stranded in `submitting` (crash mid-publish, FIX F11)
or `needs_reconcile` (ambiguous 5xx / network timeout after the body was sent, AUDIT C1). Two
backends, two READ shapes. Zernio has a true per-post lookup (GET /posts/{postSubmissionId} ->
status in-progress|failed|published|scheduled + publicUrl) and is polled one post at a time. Postiz
has no per-post endpoint at all, so it is MIRRORED: ONE bulk read of GET /public/v1/posts over the
widest window (PostizStatusClient.list_all) is projected onto every Postiz-backed post carrying a
REAL submission id — the pending ones AND the ones already resting published/analyzed. Either shape
REQUIRES the submission id.

The mirror is STATELESS: Postiz's row is the single truth, and a pass over unchanged rows writes
ZERO ledger bytes. The row's `state` token is kept VERBATIM on `Post.postiz_state` (observability
only, MOL-784) and written only when the token CHANGED. What an observation may DO is bounded:

  PUBLISHED, first observation on a pending post -> promote (public_url <- releaseURL, media_id <-
                                                    releaseId, published_at, publish buckets),
                                                    behind the unchanged IG/TikTok liveness gates
  ERROR, on a pending post                       -> the failed branch (incl. the candidate hold)
  QUEUE / absent / anything else                 -> nothing but the postiz_state mirror
  ANY later change on an ALREADY-published post  -> nothing but the postiz_state mirror

No mirrored observation EVER moves a post into a re-queueable state — `failed` is re-queueable, so a
mirror able to write it is a double-post vector; that call belongs to the operator. A post the
backend stops resolving is LATE, not failed, and lateness is DERIVED from the row and the schedule
at read time by the digest — never stamped into `error_reason`, because a stamp is a decision and
this module makes none.

Consequence (the honest boundary): AUDIT H1 (Phase D) stamps EVERY crossposted post with a client
idempotency token (submission_id="fanops_..."), which is not a real backend id, so such a post can
never appear in a Postiz window. It is never mirrored and never carries a postiz_state, but it IS
still visited, so the (state, age) escalation can move a crash-stranded `submitting` claim into
`needs_reconcile`. A post with genuinely NO submission_id at all (older data) is SKIPPED for human
reconcile (the digest surfaces it). A real backend id from an ambiguous-5xx body overwrites the
token, making that post cleanly auto-reconcilable. We never guess a post's fate — a wrong guess
either drops a live post (untrackable) or re-queues a live one (double-publish), the exact
C1/cascade hazards.

A FATAL auth failure from EITHER backend (the shared AuthError base) halts the pass. A TRANSPORT
failure is a log line and nothing else: a failed bulk fetch mirrors nobody this pass, and a failed
Zernio poll leaves its post byte-identical. `fanops resolve <id> published --url` stays the manual
route for a post the backend never surfaces. dryrun never reaches here (gated upstream)."""
from __future__ import annotations
from typing import Callable, Optional
from fanops.config import Config
from fanops.errors import AuthError
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import ErrorKind, PostState, is_real_submission_id, ImportedMedia
from fanops.text import safe_public_url
from fanops.timeutil import parse_iso, iso_z, publish_buckets
from datetime import datetime, timezone, timedelta

# XC-1: a `submitting` post still un-resolvable this long past its schedule is a crash-stranded CLAIM
# (post/run.py marks submitting + persists BEFORE the network; a mid-network crash leaves it here, and
# publish_due never re-drives a non-`queued` post). Escalate it to needs_reconcile so the digest's reconcile
# column owns it instead of a perpetual in-flight-submit. 6h covers any real slow submit; 24h is unambiguous.
# This is the ONE remaining age-driven write, and it is a state MOVE between two non-re-queueable states —
# it decides nothing about whether the post is live. There is no longer any age at which the system declares
# a post lost: waiting is not failing, and a post the backend has not resolved is LATE (a fact the digest
# derives from the row and the schedule on every read), never terminal.
_SUBMITTING_ESCALATE_AFTER = timedelta(hours=24)
# Sprint 4: submitting with no submission_id cannot be polled — park needs_reconcile after grace (H02).
_SUBMITTING_HEAL_AFTER = timedelta(minutes=15)


def _parked_age(post, now: datetime):
    """now - scheduled_time for a parked post; None if there's no/invalid schedule (-> no breadcrumb, never a
    false alarm). The post is submitted when scheduled_time <= now, so this is a sound 'stuck since' proxy.
    Narrow catch: parse_iso/fromisoformat raises ValueError; bad types raise TypeError/AttributeError."""
    if not post.scheduled_time:
        return None
    try:
        return now - parse_iso(post.scheduled_time)
    except (ValueError, TypeError, AttributeError):
        return None


def _capture_poll_exc(results: dict, sid: str, exc: BaseException) -> None:
    """Stash a poll failure for in-lock re-raise; reconcile_posts' per-post except logs it (no double-log here)."""
    results[sid] = exc


def _apply_age_terminal(post, now) -> dict | None:
    """RC-2 (S04): the un-strand escalation, as a PURE FUNCTION OF (state, age).

    It consults NEITHER this pass's observation, NOR the token's provenance (fake vs real), NOR
    error_reason — the three incidental conditions the old ladder gated on, each of which could veto
    the move on its own: a raising poll `continue`d before ever reaching it; `_is_fake_token`
    excluded every real backend id; a stale `error_reason` suppressed the visit from pass one.
    reconcile is the SOLE reader of `submitting` (publish_due iterates `queued` only), so if it
    cannot move a post out of the in-flight-submit lane, nothing can.

    Returns {"update": <model_copy update>, "log": <event>} to apply+log, or None when the post is
    not past the deadline. The predicate is the WHOLE of it — (state, age), nothing else:

      submitting + age > _SUBMITTING_ESCALATE_AFTER (24h) -> needs_reconcile (still observed, the
                                                             digest's reconcile column owns it,
                                                             never re-queueable)

    That is the ONLY rung, deliberately. The give-up rung this function used to carry declared a
    post lost purely because it was old — a verdict about a backend it had not heard from, written
    into `error_reason` where three substring parsers read it. Postiz's row now answers the
    question, so no age needs to guess at it."""
    age = _parked_age(post, now)
    if age is None:
        return None                                       # no measurable deadline -> never a false escalation
    hrs = int(age.total_seconds() // 3600)
    if post.state is PostState.submitting and age > _SUBMITTING_ESCALATE_AFTER:
        return {"update": {"state": PostState.needs_reconcile,
                           "error_reason": (f"escalated submitting->needs_reconcile after {hrs}h "
                                            "(submit unresolved past deadline) — verify on the channel "
                                            "before any resubmit")},
                "log": "escalated: submitting->needs_reconcile"}
    return None


# ---- REST-gate quarantine sentinel (publish-verify at the transition) ---------------------------------

# Prefix on error_reason marking a TikTok post the REST gate refused to let rest in a terminal-positive
# state (published/analyzed) because its LIVENESS is NOT confirmed — no live-verifiable url (oEmbed author
# != reported username) / no real submission_id. IG does NOT use this prefix: IG liveness is confirmed by
# Postiz (status==published + a real releaseURL) or by resolve_ig_media / confirm_post_live; media_id arrives
# at promotion from Postiz releaseId and post_type is declared at mint. It is now the ONLY prefix reconcile
# writes to error_reason — the give-up terminal and the "stuck …" breadcrumb that once shared the field are
# gone (the mirror answers what they guessed at). A TikTok post carrying it is QUARANTINED to needs_reconcile:
# reconcile_posts refuses to re-promote it while still unconfirmed, and the digest surfaces it.
_UNVERIFIED_PREFIX = "unverified:"
# (The former _UNVERIFIED_IG_MEDIA quarantine reason and the ig_media_id_unresolved enrichment note were
# REMOVED: IG liveness is not gated on feed enumeration, and the authored-post feed-match leg is gone.)
_UNVERIFIED_TIKTOK = (_UNVERIFIED_PREFIX + " TikTok post not live-verified — needs a real backend "
                      "submission_id AND a public_url proven live for this handle (oEmbed author==handle). "
                      "Backend reported published but the URL/id could not be confirmed; parked, not rested.")
# MOL-117 — the CREDENTIALED-account IG identity park. A quarantine (the _UNVERIFIED_PREFIX sentinel), so
# reconcile_posts keeps re-polling it (needs_reconcile ∈ _RECONCILABLE) and re-confirms next pass — a
# post that later resolves on the platform recovers, one that never does stays visibly parked. Reached
# ONLY for an account with its OWN ig_user_id: the Graph gave a DEFINITIVE verdict (object absent, or
# resolved to a DIFFERENT owner than the intended handle) — never on a transport hiccup (that fails OPEN).
_UNVERIFIED_IG = (_UNVERIFIED_PREFIX + " IG post not platform-confirmed live — this account carries its "
                  "own ig_user_id, so liveness is gated on the Meta Graph: the captured media object must "
                  "resolve AND its owner username must match the intended account handle. The Graph gave a "
                  "definitive verdict that it does NOT (object absent or owned by a different handle); parked, "
                  "not rested on Postiz's self-report. Re-polled next pass — recovers if the object resolves.")


def _tiktok_url_confirmed(cfg: Config, post, url: Optional[str], sub: Optional[str],
                          reported_username: Optional[str], *, get=None) -> bool:
    """REST-gate for a TikTok post: it may only rest published when its identity is CONFIRMED, symmetric with
    IG's matched media_id. Two necessary conditions, BOTH required: (1) a real (non fanops_) submission_id AND
    a non-empty safe_public_url — the T4 baseline; (2) the url passes the live TikTok oEmbed verifier: the live
    video's oEmbed author == the username ZERNIO REPORTS THIS POST WENT TO (`reported_username`, surfaced by
    ZernioStatusClient.get_status from the status body it already fetched — NO second network call). A TikTok
    video's real author is the TikTok username on the Zernio integration (our internal @hrmny-blog publishes to
    tiktok.com/@wahed_bared), so comparing to `post.account` (the internal handle) FALSE-REJECTED genuinely-live
    posts — this now compares to Zernio's authoritative reported username instead. FAIL CLOSED at every step —
    any missing/failing piece (bad url, fake token, MISSING reported username, oEmbed mismatch, an unimportable/
    erroring verifier) returns False and the post stays parked. The oEmbed HTTP getter is injectable (`get`) so
    tests never touch the network; the verifier is imported lazily to keep reconcile import-light."""
    ok = safe_public_url(url)
    if not (ok and is_real_submission_id(sub)):
        return False                                         # baseline: no verifiable url / no real id -> not confirmed
    if not (reported_username or "").strip():
        return False                                         # no authoritative Zernio username -> fail closed (never rest on an unproven shape)
    try:
        from fanops.post.metrics import verify_tiktok_permalink   # live oEmbed author == Zernio-reported username
        return bool(verify_tiktok_permalink(cfg, ok, reported_username, get=get))
    except Exception as exc:
        get_logger(cfg)("reconcile", post.id, "tiktok_verify_error", err=str(exc)[:120])
        return False                                         # an unimportable/erroring verifier is NOT proof it is live


# MOL-117 gate verdicts: REST (platform-confirmed / uncredentialed Postiz-rest), PARK (definitive
# identity failure on a credentialed account), FAIL_OPEN (transport hiccup during confirm -> retry next tick).
_GATE_REST, _GATE_PARK, _GATE_FAILOPEN = "rest", "park", "fail_open"


def _ig_rest_verdict(cfg: Config, post, media_id, credentialed_handles, confirm, graph_get) -> str:
    """MOL-117 — the CONDITIONAL IG rest-gate. `post.account` is the intended IG handle; `media_id` is the
    Postiz releaseId (the IG object id) just captured this pass; `credentialed_handles` is
    meta_graph.credentialed_ig_handles(cfg) (handles with their OWN ig_user_id). `confirm` is the MOL-113
    confirm_post_live seam (injectable for tests); `graph_get` is the Graph HTTP getter (injectable).
      • UNCREDENTIALED account (handle NOT in `credentialed_handles`): _GATE_REST — UNCHANGED Postiz-rest
        path. A borrowed/global credential can't enumerate this object without false-negativing it (the #317
        6-stuck-posts regression), so liveness stands on the Postiz-confirmed releaseURL. confirm is NEVER
        called here.
      • CREDENTIALED account: FAIL-CLOSED platform identity gate. Ask the platform (confirm_post_live over
        the captured media_id, scoped to this handle's creds) and REST only when it resolves AND its owner
        username == the intended handle. A DEFINITIVE non-confirmation (object absent) or an owner MISMATCH
        -> _GATE_PARK (never rest on Postiz's word). A TRANSPORT failure during the confirm (the injected
        probe saw the getter raise) -> _GATE_FAILOPEN: the confirmed=False is a network hiccup, NOT a verdict,
        so don't strand the post — retry next tick. Mirrors TikTok's posture: fail-closed on a real identity
        mismatch, fail-open on a network hiccup."""
    handle = (post.account or "").strip()
    if handle.lstrip("@").lower() not in {h.lstrip("@").lower() for h in credentialed_handles}:
        return _GATE_REST                                    # uncredentialed -> Postiz-rest UNCHANGED (#317 guard)
    # credentialed: platform-confirm over the captured media_id, transport-probed so a raising getter is
    # distinguishable from a definitive absence (confirm/_graph_get both collapse to confirmed=False).
    probe = {"transport_failed": False}
    def _probed_get(url, params=None, timeout=None):
        g = graph_get or _requests_get()
        try:
            return g(url, params=params, timeout=timeout)
        except Exception:
            probe["transport_failed"] = True                 # record the transport error, then let it propagate
            raise                                            # into _graph_get, which fail-softs it to None
    probe_id = (media_id or post.media_id or "").strip() if isinstance(media_id or post.media_id, str) else (media_id or post.media_id)
    if not probe_id:
        return _GATE_FAILOPEN
    cand = post.model_copy(update={"media_id": probe_id})    # the resolve INPUT is the just-captured releaseId
    try:
        res = confirm(cfg, cand, get=_probed_get)
    except Exception as exc:
        get_logger(cfg)("reconcile", post.id, "ig_confirm_seam_error", err=str(exc)[:120])
        return _GATE_FAILOPEN                                # an erroring seam is NOT a verdict -> retry next tick
    if res.get("confirmed") and _owner_matches(res.get("owner"), handle):
        return _GATE_REST                                    # platform-confirmed AND owned by the intended handle
    if probe["transport_failed"]:
        return _GATE_FAILOPEN                                # confirmed=False rode a transport hiccup -> fail OPEN
    return _GATE_PARK                                        # DEFINITIVE: object absent or owner mismatch -> fail CLOSED


def _owner_matches(owner, handle) -> bool:
    """The Graph-reported owner username == the intended IG handle, compared case-insensitively and
    '@'-insensitively (the handle is canonicalized to a leading '@'; the Graph username carries none)."""
    return bool(owner) and owner.strip().lstrip("@").lower() == handle.lstrip("@").lower()


def _requests_get():
    import requests
    return requests.get


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

def _capture_publish_fields(info: dict, post) -> tuple[str | None, str | None, str | None, str | None]:
    """Shared published-row capture: (captured_url, reported_username, new_sub, release_id)."""
    real = next((info[k] for k in ("postSubmissionId", "id", "submissionId")
                 if is_real_submission_id(info.get(k))), None)
    new_sub = real or (post.submission_id if is_real_submission_id(post.submission_id) else None)
    captured_url = safe_public_url(info.get("publicUrl")) or post.public_url
    reported_username = info.get("tiktokUsername")
    _rid = info.get("releaseId")
    _rid = _rid.strip() if isinstance(_rid, str) and _rid.strip() else None
    return captured_url, reported_username, new_sub, _rid


def _enrich_poll_liveness(cfg: Config, post, info: dict, *, cred_ig, confirm, graph_get) -> None:
    """M04: pre-compute liveness verdicts during the lock-free poll (network allowed). Mutates `info`
    with a `liveness` dict the apply path reads without further network I/O. Enrichment order mirrors
    apply: TikTok analytics fallback BEFORE oEmbed/IG confirm."""
    from fanops.models import Platform as _Plat
    captured_url, reported_username, new_sub, _rid = _capture_publish_fields(info, post)
    if not (captured_url or "").strip() and post.platform is _Plat.tiktok:
        try:
            from fanops.post.metrics import zernio_analytics_url_and_username
            _u, _un = zernio_analytics_url_and_username(cfg, post.submission_id, post.account_id)
            captured_url = _u or captured_url
            reported_username = reported_username or _un
        except Exception as exc:
            get_logger(cfg)("reconcile", post.id, "tiktok_analytics_fallback_error", err=str(exc)[:120])
    liv: dict = {"captured_url": captured_url, "reported_username": reported_username,
                 "new_sub": new_sub, "release_id": _rid}
    if not (captured_url or "").strip():
        liv["published_no_url"] = True
        info["liveness"] = liv
        return
    liv["published_no_url"] = False
    if post.platform is _Plat.tiktok:
        liv["tiktok_ok"] = _tiktok_url_confirmed(cfg, post, captured_url, new_sub, reported_username)
    elif post.platform is _Plat.instagram:
        liv["ig_verdict"] = _ig_rest_verdict(cfg, post, _rid, cred_ig, confirm, graph_get)
    info["liveness"] = liv


# ---- ledger-rebuild M2 (Instagram is the source of truth): import live-only media ------------------
# project_imported_media: a live media matched to NO ledger post is IMPORTED as an ImportedMedia record
# ("viewed there, not authored here"). Authored posts are skipped by permalink; their media_id/post_type
# are minted at publish time, not by feed match.

def project_imported_media(led: Ledger, cfg: Config, *, get=None) -> Ledger:
    """Iterate the live /{ig_user}/media inventory; a media whose permalink matches an EXISTING ledger post
    (any post carrying that public_url — "authored here") is SKIPPED; every OTHER live media is UPSERTED as
    an ImportedMedia (keyed by its Graph media_id). IDEMPOTENT — a re-run over the same media OVERWRITES the
    identity fields (latest live snapshot wins) but PRESERVES any metrics/metrics_series the insights read
    (M3) already filled (the /media list carries no insights). SINGLE-HANDLE scope: META_IG_USER_ID is one
    credential, so this enumerates ONE handle's media and stamps that credentialed handle on every record
    (the Live library / wipe-preview scope label). FAIL-OPEN (no creds / empty list / transport failure ->
    imports nobody, never crashes). fan-accounts-repost-freely: an ImportedMedia MIRRORS live — it never
    blocks reposting; there is no supersede/dedupe here."""
    from fanops import meta_graph
    from datetime import datetime, timezone
    log = get_logger(cfg)
    # Per-account creds (the per-handle-creds gap): enumerate EVERY per-account-credentialed IG handle's
    # media, each with its own creds — no longer capped at the single global handle. Empty handle set ->
    # [None] -> the single global enumeration (byte-identical). Each pair carries WHICH handle it came from
    # so the imported record is stamped with its true handle, not the one global scope label.
    handles = meta_graph.credentialed_ig_handles(cfg) or [None]
    scoped = meta_graph.enumerate_scoped_media(cfg, handles, get=get)
    if not scoped:
        return led                                               # no creds / empty / transport -> import nobody (fail-open)
    now_z = iso_z(datetime.now(timezone.utc))                    # audit birth stamp for a first-time import
    # the set of live permalinks we ALREADY author (a ledger post points at them) — shadowed, never imported.
    authored: set[str] = set()
    for p in led.posts.values():
        k = _norm_permalink(p.public_url)
        if k: authored.add(k)
    imported = 0
    for src_handle, m in scoped:
        # the scope label: the real handle this media was enumerated under, or the global ig id for the
        # None (global-creds) enumeration — preserves the single-handle scope label byte-for-byte.
        handle = src_handle if src_handle is not None else cfg.meta_ig_user_id
        mid = m.get("id")
        if not mid:
            continue                                             # a media with no id is un-keyable -> skip (defensive)
        if _norm_permalink(m.get("permalink")) in authored:
            continue                                             # authored here -> the Post is the record, not an import
        prior = led.imported_media.get(mid)
        # UPSERT: refresh identity fields from the live snapshot; PRESERVE prior metrics/metrics_series + the
        # original imported_at (a re-pull must not erase what the insights read filled, nor reset the audit birth).
        led.add_imported_media(ImportedMedia(
            media_id=mid,
            permalink=m.get("permalink"),
            product_type=m.get("media_product_type"),
            timestamp=m.get("timestamp"),
            caption=m.get("caption"),
            account=handle,
            metrics=(prior.metrics if prior else {}),
            metrics_series=(prior.metrics_series if prior else []),
            error_reason=(prior.error_reason if prior else None),
            imported_at=(prior.imported_at if prior and prior.imported_at else now_z)))
        imported += 1
    log("reconcile", "-", "imported_media_projected", imported=imported, live=len(scoped), handles=len(handles))
    return led

# States whose true outcome is unknown and pollable: a publish was (or may have been) sent.
_RECONCILABLE = (PostState.submitting, PostState.submitted, PostState.needs_reconcile)
# States a post RESTS in once its publication is settled. The Postiz mirror keeps observing them for life —
# not to re-decide them (nothing here may move a resting post; see the module header) but because the row is
# the only place a later platform-side change is visible at all, and a mirror that stops looking the moment a
# post succeeds can only ever report the moment of success.
_MIRROR_RESTING = (PostState.published, PostState.analyzed)
# The Post.postiz_state sentinel (MOL-784 vocabulary) for "the mirrored window held NO row for this post's
# submission id". Written ONLY for a post whose id is a real backend id — an id that COULD have matched.
_MIRROR_ABSENT = "absent"
# The one RAW Postiz token (same MOL-784 vocabulary, kept verbatim off the row) that means the backend
# considers the row DONE. Compared case-folded because the mirror deliberately never normalises what it
# stores. It lives HERE, beside the sentinel, because it is the SAME vocabulary and a second literal in a
# second module is the copied-number defect class — `pending_lateness` below and the digest's mirror-drift
# section (digest._postiz_drift) both read it from here.
_MIRROR_PUBLISHED = "PUBLISHED"
# INVARIANT (report 11 §5, I-7) — `Post.reconcile_candidate_id` is NEVER a poll key here, and this set is
# exactly why. A candidate is an UNPROVEN pointer a backend handed back on a duplicate signal (a Zernio 409's
# details.existingPostId): it names a record the BACKEND holds, which is not evidence that OUR post is that
# record. `needs_reconcile` is deliberately IN _RECONCILABLE, so if a candidate were ever parked in
# `submission_id` (or joined the poll keys below), this module would poll it, find it live — OF COURSE it is
# live, that is WHY the backend rejected us as a duplicate — and promote OUR row to `published` carrying
# ANOTHER post's permalink. Silent misattribution, indistinguishable from a real publish.
# Therefore: poll keys are `submission_id` ONLY (_reconcilable_routing / reconcile_posts below); a candidate
# is operator-facing evidence, cleared only when an explicit identity decision resolves the record. Pinned by
# the never-polls / never-promotes negative controls in tests/test_zernio_idempotency.py.
GetStatus = Callable[[str], dict]
_LIVE_STATUS_BACKENDS = frozenset({"postiz", "zernio"})
# Backends with a TRUE per-post status GET. Postiz is mirror-only (`list_all`); it is still a live
# routing backend above, but `_status_client_for` / `_default_get_status` never construct it.
_POLL_STATUS_BACKENDS = frozenset({"zernio"})


def _status_client_for(cfg: Config, backend: str, led: Optional[Ledger]) -> GetStatus:
    # One backend's per-post status poller. Only Zernio has a TRUE per-post status endpoint (a bound
    # get_status, no date window). Postiz is read via the bulk mirror (`PostizStatusClient.list_all`)
    # and has no per-post poller — do not construct it here. An unknown backend FAILS CLOSED + legibly
    # (a stale FANOPS_POSTER already degrades to dryrun at cfg, W4). Lazy imports keep deps off the
    # core path. `led` is unused (signature kept so call sites stay uniform with the mixed dispatcher).
    if backend == "zernio":
        from fanops.post.metrics import ZernioStatusClient
        return ZernioStatusClient(cfg).get_status
    raise ValueError(f"unknown backend {backend!r}: no status client (expected zernio)")


def _reconcilable_routing(cfg: Config, led: Optional[Ledger], *,
                          states: tuple = _RECONCILABLE) -> dict[str, str]:
    # submission_id -> RESOLVED backend (accounts.json `backends` override -> else the global FANOPS_POSTER)
    # for every post in `states` that HAS a submission id. `states` defaults to the pollable set; the mirror
    # widens it to the resting states too, because a resting post still has a backend that owns its row.
    # Empty when led is None. Accounts load is guarded: a corrupt accounts.json must NOT crash the reconcile
    # read (publish surfaces it loudly) — degrade to the global backend for every post + log.
    if led is None:
        return {}
    from fanops.accounts import load_accounts_safe
    accounts, err = load_accounts_safe(cfg)
    if err: get_logger(cfg)("backend_route", "accounts", "load_failed_global_fallback", err=err)
    # H1: per-channel provider (effective_provider), NOT `resolve_backend or global` — so a live channel's
    # status reads hit ITS provider (zernio/postiz) even when FANOPS_POSTER is unset. A post whose channel
    # has no provider is SKIPPED (never dryrun-routed -> never silently stranded against the wrong client).
    return {p.submission_id: prov
            for p in led.posts.values() if p.state in states and p.submission_id
            and (prov := accounts.effective_provider(p.account, p.platform))}


def _mirror_info(row: Optional[dict]) -> dict:
    """Project ONE Postiz window row (PostizStatusClient._fetch_posts' shape) into the observation dict
    reconcile_posts consumes. `row is None` means the window — the WIDEST one the API accepts, with no
    pagination to be on the wrong side of (metrics.list_all) — held no row for this submission id; that
    absence is a real observation, recorded as the `absent` sentinel and NOTHING else. A row's raw `state`
    token rides through VERBATIM for the postiz_state mirror; a blank token is no observation at all
    (recording it would overwrite a true prior value with an empty one), so it is simply omitted."""
    if row is None:
        return {"status": "unknown", "postiz_state": _MIRROR_ABSENT}
    out: dict = {"status": row["status"]}
    raw = (row.get("state") or "").strip()
    if raw:
        out["postiz_state"] = raw
    if row["status"] == "published":
        out["publicUrl"] = row["releaseURL"] or None       # the real IG permalink (only on PUBLISHED rows)
        rid = row["releaseId"]
        if isinstance(rid, str) and rid.strip():
            out["releaseId"] = rid.strip()                 # IG Graph media id -> persisted on Post.media_id
    return out


def _mirror_update(post, info: dict) -> dict:
    """The postiz_state half of a post's update — `{}` when the observed token is UNCHANGED. This is the
    zero-byte property: a pass over a corpus whose rows did not move must not rewrite a single ledger row,
    so an identical observation is not a write. Never clears a prior value: no observation (a non-Postiz
    post, a client token that no row can carry, a fetch that failed) leaves the last one standing."""
    obs = info.get("postiz_state")
    return {} if not obs or obs == post.postiz_state else {"postiz_state": obs}


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
    # Per-post status poller for Zernio. Postiz posts are mirrored via `list_all` and never enter this
    # seam — filter them out of the backends set so a mixed corpus does NOT eagerly construct
    # PostizStatusClient (whose `__init__` raises PostizAuthError when the key is missing). With one
    # pollable backend (or no led) this is the single-backend Zernio bound-method dispatch.
    routing = _reconcilable_routing(cfg, led)
    backends = {b for b in routing.values() if b in _POLL_STATUS_BACKENDS}
    if not backends:
        g = cfg.poster_backend
        if g in _POLL_STATUS_BACKENDS:
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
    """Crash-stranded `submitting` posts with no submission_id -> `needs_reconcile` after a grace window.
    Nothing was pollable; publish_due never re-drives submitting. Returns count healed."""
    now = now or datetime.now(timezone.utc)
    healed = 0
    reason = ("healed: submitting->needs_reconcile (no submission_id — ambiguous live; reconcile by hand)")
    with Ledger.transaction(cfg) as led:
        for p in list(led.posts.values()):
            if p.state is not PostState.submitting:
                continue
            if (p.submission_id or "").strip():
                continue
            age = _parked_age(p, now)
            if age is None or age < _SUBMITTING_HEAL_AFTER:
                continue
            led.set_post_state(p.id, PostState.needs_reconcile, error_reason=reason)
            healed += 1
            get_logger(cfg)("reconcile", p.id, "healed: submitting->needs_reconcile", reason="no_submission_id")
    return healed


def _reconcile_reads(cfg: Config, snapshot: Ledger, log) -> tuple[list, list, list]:
    """Split the reconcile surface by RESOLVED BACKEND — the read SHAPES are not interchangeable, so the
    split is the first thing the pass decides. Returns (mirrored, token_only, polled):

      mirrored   — Postiz-backed, a REAL submission id, pending OR resting. One bulk window answers all
                   of them; a resting post is here so its row keeps being observed after it succeeds.
      token_only — Postiz-backed, pending, carrying only a `fanops_` client token. NO Postiz row can
                   ever carry that id, so there is nothing to mirror and nothing to poll — but the post
                   is still VISITED, because the (state, age) escalation is what un-strands it.
      polled     — Zernio-backed and pending: the per-post GET /posts/{id}, unchanged. Zernio is NOT
                   mirrored, so a Zernio-backed resting post is out of the surface entirely and is
                   never written an `absent` it was never asked about.

    A post whose channel resolves to no live provider is skipped; that is logged for a pending post
    (it is work not done) and silent for a resting one (there is nothing it was owed)."""
    routing = _reconcilable_routing(cfg, snapshot, states=_RECONCILABLE + _MIRROR_RESTING)
    mirrored, token_only, polled = [], [], []
    for p in snapshot.posts.values():
        resting = p.state in _MIRROR_RESTING
        if not (resting or p.state in _RECONCILABLE) or not p.submission_id:
            continue
        try:
            backend = _poll_backend_for_sid(cfg, routing, p.submission_id)
        except RuntimeError:
            if not resting:
                log("reconcile", p.id, "skipped: no live provider")
            continue
        if backend != "postiz":
            if not resting:
                polled.append(p)                         # Zernio: per-post GET; resting posts are not read
        elif is_real_submission_id(p.submission_id):
            mirrored.append(p)
        elif not resting:
            token_only.append(p)                         # a client token names no row -> age ladder only
    return mirrored, token_only, polled


def reconcile_due(cfg: Config) -> dict[str, int]:
    """One reconcile pass: every network READ runs OUTSIDE the ledger flock, and only the apply runs
    inside a tight transaction (mirrors cmd_reconcile; M1, same fix as publish #89).

    Postiz is read ONCE — `PostizStatusClient.list_all` returns the whole corpus in a single call — and
    the rows are projected onto every mirrored post before the lock is taken. Zernio keeps its per-post
    poll. Both feed reconcile_posts inside ONE Ledger.transaction, which re-checks each post's CURRENT
    state under the lock, so a post that moved between read and apply is handled correctly.

    Failure is graded, not uniform. A FATAL AuthError from either backend halts the pass (every read
    would fail). A TRANSPORT failure of the bulk fetch mirrors NOBODY this pass — it is a log line, and
    the ledger is untouched, because a fetch that did not happen is not evidence about any post. A
    Zernio poll error is CAPTURED and re-raised inside the apply so its per-post containment (log,
    leave the row alone) stays where the rest of the per-post logic lives.

    Empty surface -> no transaction. Caller gates on backend/key. Returns the resolved counts."""
    snapshot = Ledger.load(cfg)
    healed = heal_stranded_submitting(cfg)
    log = get_logger(cfg)
    mirrored, token_only, polled = _reconcile_reads(cfg, snapshot, log)
    if not (mirrored or token_only or polled):
        return {"needs_reconcile": len(snapshot.posts_in_state(PostState.needs_reconcile)),
                "published": len(snapshot.posts_in_state(PostState.published)),
                "healed_submitting": healed}
    from fanops.postiz_lifecycle import ensure_up        # work exists: bring the local Postiz stack up to read
    ensure_up(cfg)
    from fanops.meta_graph import credentialed_ig_handles, confirm_post_live
    _cred_ig = credentialed_ig_handles(cfg)
    polled_as: dict[str, str] = {}                       # post_id -> submission_id at read time (stale guard)
    mirror: dict[str, dict] = {}                         # sid -> the observation reconcile_posts applies
    # Every PENDING post on the Postiz side starts UNOBSERVED: status unknown, no postiz_state key, so the
    # apply writes nothing on its behalf. A successful window overwrites that with the real observation. A
    # failed window therefore degrades to exactly this: visited, un-mirrored, un-written.
    for p in mirrored + token_only:
        if p.state in _RECONCILABLE:
            mirror[p.submission_id] = {"status": "unknown"}
            polled_as[p.id] = p.submission_id
    if mirrored:
        window = None
        try:
            from fanops.post.metrics import PostizStatusClient
            window = PostizStatusClient(cfg).list_all()   # ONE call, widest window, no lock held
        except AuthError:
            raise                                        # bad key: every read fails -> halt, don't grind
        except Exception as exc:
            get_logger(cfg)("reconcile", "-", "mirror_fetch_error", err=str(exc)[:200], posts=len(mirrored))
        if window is not None:
            for p in mirrored:
                info = _mirror_info(window.get(p.submission_id))
                if info["status"] == "published" and p.state in _RECONCILABLE:
                    _enrich_poll_liveness(cfg, p, info, cred_ig=_cred_ig, confirm=confirm_post_live,
                                          graph_get=None)
                mirror[p.submission_id] = info
            log("reconcile", "-", "mirror_window", rows=len(window), posts=len(mirrored))
    results: dict[str, object] = {}                      # sid -> info dict OR captured Exception
    if polled:
        poll = _default_get_status(cfg, snapshot)        # only built when Zernio work exists; never dryrun
        for p in polled:
            polled_as[p.id] = p.submission_id
            try:
                info = poll(p.submission_id) or {}       # network, NO lock held
                if (info.get("status") or "").lower() == "published":
                    _enrich_poll_liveness(cfg, p, info, cred_ig=_cred_ig, confirm=confirm_post_live,
                                          graph_get=None)
                results[p.submission_id] = info
            except AuthError:
                raise                                    # bad key (Zernio): every poll fails -> halt
            except Exception as exc:
                _capture_poll_exc(results, p.submission_id, exc)  # re-raised in apply -> logged, no write
    def cached(sid: str) -> dict:
        r = results.get(sid, {})
        if isinstance(r, Exception): raise r             # reconcile_posts' per-post except logs it
        return r
    with Ledger.transaction(cfg) as led:
        led = reconcile_posts(led, cfg, get_status=cached, polled_as=polled_as, mirror=mirror)
        return {"needs_reconcile": len(led.posts_in_state(PostState.needs_reconcile)),
                "published": len(led.posts_in_state(PostState.published)),
                "healed_submitting": healed}


def reconcile_posts(led: Ledger, cfg: Config, *, get_status: Optional[GetStatus] = None,
                    confirm=None, graph_get=None, now: Optional[datetime] = None,
                    polled_as: dict[str, str] | None = None,
                    mirror: dict[str, dict] | None = None) -> Ledger:
    """The in-lock APPLY. `mirror` is the caller's pre-fetched Postiz observations, keyed by submission
    id — this function performs NO bulk read of its own, because the read belongs outside the flock. A
    submission id present in `mirror` is answered from it and never polled; everything else pending goes
    through the `get_status` seam (Zernio's per-post GET, or a direct caller's stub)."""
    poll = get_status or _default_get_status(cfg, led)
    now = now or datetime.now(timezone.utc)               # clock injected by tests; real callers default to UTC now
    log = get_logger(cfg)
    mirror = mirror or {}
    # MOL-117: the IG liveness confirmation seam (confirm_post_live) + the Graph getter, both injectable so
    # tests never touch the network. The credentialed-handle set is read ONCE per pass (a torn accounts.json
    # degrades to [] -> every IG post treated as uncredentialed -> Postiz-rest unchanged, never stranded).
    if confirm is None:
        from fanops.meta_graph import confirm_post_live as confirm
    from fanops.meta_graph import credentialed_ig_handles
    _cred_ig = credentialed_ig_handles(cfg)
    # RESTING posts (published/analyzed) the caller mirrored. The ONLY thing that may happen to one of them
    # here is the postiz_state snapshot: a row that changed, or vanished, is RECORDED and nothing more. It
    # is deliberately not a re-decision — `failed` is re-queueable, so a mirror allowed to move a live post
    # into it is a double-post vector, and no backend row is evidence enough to spend a second publish on.
    for post in [p for p in led.posts.values()
                 if p.state in _MIRROR_RESTING and p.submission_id in mirror]:
        upd = _mirror_update(post, mirror[post.submission_id])
        if upd:
            led.posts[post.id] = post.model_copy(update=upd)
            log("reconcile", post.id, "mirrored", postiz_state=upd["postiz_state"], resting=post.state.value)
    for post in [p for p in led.posts.values() if p.state in _RECONCILABLE]:
        if not post.submission_id:
            log("reconcile", post.id, "skipped: no submission_id")
            continue                       # no id -> cannot be looked up at all -> human reconcile
        if polled_as is not None and polled_as.get(post.id) != post.submission_id:
            log("reconcile", post.id, "skipped: stale poll (submission_id changed)")
            continue                       # M04: post mutated between read and apply — skip cached row
        if post.submission_id in mirror:
            info = mirror[post.submission_id]
        else:
            # Per-post resilience (mirrors publish_due, run.py:70-76): one post's poll error must NOT
            # abort the whole pass — uncaught, that raise strands every post LATER in iteration order
            # (an order-dependent availability bug). Contain it to THIS post.
            try:
                info = poll(post.submission_id) or {}
            except AuthError:
                raise                      # bad key/401 (Postiz OR Zernio): EVERY read will fail ->
                                           # halt, don't grind. Type-matched on the shared AuthError
                                           # base (P2) so any backend's 401 halts.
            except Exception as exc:
                # A read failure is NOT evidence about the post — it MAY be live. It buys a LOG LINE
                # and nothing else: no state, and no prose into error_reason, which three substring
                # parsers read and which used to carry this as a permanent do-not-look-again latch.
                # The (state, age) escalation still applies, because it never consulted the read.
                term = _apply_age_terminal(post, now)
                if term is not None:
                    led.posts[post.id] = post.model_copy(update=term["update"])
                    log("reconcile", post.id, term["log"])
                    continue
                get_logger(cfg)("reconcile", post.id, "poll-error", err=str(exc)[:200])   # the detail rides the log stream
                continue
        mirror_upd = _mirror_update(post, info)           # {} unless the observed row token actually moved
        if mirror_upd:
            # Land the snapshot FIRST and rebind, so it survives whichever branch runs below (each one
            # model_copy's from `post`) and so it lands ALONE when none of them writes. Nothing else in
            # this pass may be inferred from it: it is what the row said, not what we concluded.
            post = post.model_copy(update=mirror_upd)
            led.posts[post.id] = post
            log("reconcile", post.id, "mirrored", postiz_state=mirror_upd["postiz_state"])
        status = (info.get("status") or "").lower()
        if status == "published":
            from fanops.models import Platform
            liv = info.get("liveness")
            if liv is not None:
                # M04: apply reads cached liveness only — no network under the flock.
                if liv.get("published_no_url"):
                    led.set_post_state(post.id, PostState.needs_reconcile, error_reason=(
                        "publish_missing_url_at_reconcile: backend reports published but no valid "
                        "https url captured (M2 safe_public_url rejected it); re-polling next pass"))
                    log("reconcile", post.id, "published_no_url_parked")
                    continue
                captured_url = liv["captured_url"]
                new_sub = liv.get("new_sub")
                _rid = liv.get("release_id")
                _reason = None
                if post.platform is Platform.tiktok:
                    if not liv.get("tiktok_ok"):
                        _reason = _UNVERIFIED_TIKTOK
                elif post.platform is Platform.instagram:
                    _verdict = liv.get("ig_verdict")
                    if _verdict == _GATE_PARK:
                        _reason = _UNVERIFIED_IG
                    elif _verdict == _GATE_FAILOPEN:
                        log("reconcile", post.id, "ig_confirm_transport_failopen")
                        continue
                if _reason is not None:
                    led.set_post_state(post.id, PostState.needs_reconcile, error_reason=_reason)
                    log("reconcile", post.id, "unverified_identity_parked")
                    continue
            else:
                # Inline liveness (no poll cache — direct reconcile_posts callers / tests).
                real = next((info[k] for k in ("postSubmissionId", "id", "submissionId")
                             if is_real_submission_id(info.get(k))), None)
                new_sub = real or (post.submission_id if is_real_submission_id(post.submission_id) else None)
                captured_url = safe_public_url(info.get("publicUrl")) or post.public_url
                reported_username = info.get("tiktokUsername")
                if not (captured_url or "").strip():
                    from fanops.models import Platform as _Plat
                    if post.platform is _Plat.tiktok:
                        try:
                            from fanops.post.metrics import zernio_analytics_url_and_username
                            _u, _un = zernio_analytics_url_and_username(cfg, post.submission_id, post.account_id)
                            captured_url = _u or captured_url
                            reported_username = reported_username or _un
                        except Exception as exc:
                            get_logger(cfg)("reconcile", post.id, "tiktok_analytics_fallback_error", err=str(exc)[:120])
                if not (captured_url or "").strip():
                    led.set_post_state(post.id, PostState.needs_reconcile, error_reason=(
                        "publish_missing_url_at_reconcile: backend reports published but no valid "
                        "https url captured (M2 safe_public_url rejected it); re-polling next pass"))
                    log("reconcile", post.id, "published_no_url_parked")
                    continue
                _reason = None
                _rid = info.get("releaseId")
                _rid = _rid.strip() if isinstance(_rid, str) and _rid.strip() else None
                if post.platform is Platform.tiktok:
                    if not _tiktok_url_confirmed(cfg, post, captured_url, new_sub, reported_username):
                        _reason = _UNVERIFIED_TIKTOK
                elif post.platform is Platform.instagram:
                    _verdict = _ig_rest_verdict(cfg, post, _rid, _cred_ig, confirm, graph_get)
                    if _verdict == _GATE_PARK:
                        _reason = _UNVERIFIED_IG
                    elif _verdict == _GATE_FAILOPEN:
                        log("reconcile", post.id, "ig_confirm_transport_failopen")
                        continue
                if _reason is not None:
                    led.set_post_state(post.id, PostState.needs_reconcile, error_reason=_reason)
                    log("reconcile", post.id, "unverified_identity_parked")
                    continue
            upd = {"public_url": captured_url,
                   # Report 11 §5 (I-7): the identity question is now SETTLED — this row's OWN submission_id
                   # polled `published` and passed the platform liveness gate above, on evidence that never
                   # touched the candidate. Spent evidence must not outlive the ambiguity it described, or a
                   # resolved row keeps showing the operator an unverified pointer to some other post.
                   "reconcile_candidate_id": None}
            if post.platform is Platform.instagram and _rid:
                upd["media_id"] = _rid                    # MOL-112: IG object id from Postiz row — no feed match required
            if not (post.published_at or "").strip():
                upd["published_at"] = iso_z(now)         # mirror _publish_one: reconcile-only promote must carry a ship stamp
            # Leg 3 (timing): bucket the ship stamp into operator-local (hour, weekday) — mirror _publish_one
            # so a reconcile-recovered publish is rankable by timing_bias too. Uses the published_at we
            # just set (else the pre-existing one). Fail-safe (None,None) leaves the dim unranked.
            _ph, _pd = publish_buckets(upd.get("published_at") or post.published_at, cfg)
            upd["publish_hour"], upd["publish_dow"] = _ph, _pd
            if new_sub: upd["submission_id"] = new_sub
            led.posts[post.id] = post.model_copy(update=upd)
            # state + clear error_reason via owner (MOL-779); a transient poll-error reason must not survive a successful publish
            led.set_post_state(post.id, PostState.published, error_reason=None, error_kind=None)
            if new_sub is None:                           # published but still no real id -> attribution can't bind
                log("reconcile", post.id, "published_no_real_id")   # first-class: a logged outcome, not silence
            try:                                          # CULM-Q3: archive includes reconcile-recovered posts
                from fanops.post.run import _archive_published   # lazy: reconcile must not import the publish stage eagerly
                _archive_published(cfg, led.posts[post.id])
            except Exception as exc:
                get_logger(cfg)("reconcile", post.id, "archive_error", err=str(exc)[:120])   # fail-open: never block a recovered publish
            log("reconcile", post.id, "published")
        elif status == "failed":
            # Report 11 §5 (I-7): a `failed` poll of THIS row's OWN submission_id does NOT disprove a
            # reconcile_candidate_id. They name DIFFERENT objects — the candidate is the record Zernio said
            # it already holds when it rejected us as a duplicate (409), and we never polled it (deliberately:
            # polling it and adopting its result is the misattribution this design exists to prevent). So its
            # disposition is untouched by this answer. Downgrading to `failed` would make the row RE-QUEUEABLE
            # (_requeue_transient_failed_for_daemon reads posts_in_state(failed)) and licence a re-POST while a
            # possibly-live duplicate stands — the exact double-post R-3 was about. Hold needs_reconcile until
            # an operator identity decision; the candidate stays EVIDENCE and never becomes submission_id.
            cand = (getattr(post, "reconcile_candidate_id", None) or "").strip()
            if cand:
                led.set_post_state(post.id, PostState.needs_reconcile, error_reason=(
                        f"reconciled: this row's own submission reports failed "
                        f"({str(info.get('errorMessage', 'no detail'))[:100]}), but an UNVERIFIED duplicate "
                        f"candidate={cand} remains unchecked — held for an operator identity decision, NOT "
                        f"re-queueable (report 11 §5)")[:400])
                log("reconcile", post.id, "failed_held_unverified_candidate", candidate=cand)
                continue
            led.set_post_state(post.id, PostState.failed, error_kind=ErrorKind.unknown, error_reason=(
                f"reconciled: poster reports failed ({info.get('errorMessage', 'no detail')})"))
            log("reconcile", post.id, "failed")
        else:
            # QUEUE / in-progress / scheduled / unknown / absent -> the observation did not RESOLVE the
            # post, which is not the same as the post being lost. The only write left here is the (state,
            # age) escalation out of the in-flight-submit lane; the mirror snapshot has already landed
            # above. Nothing stamps lateness: the digest DERIVES it from this row and the schedule on
            # every read, so it is always current, whereas a stamped "stuck 9h" is wrong an hour later
            # and was the do-not-look-again latch that made a strand silent in the first place.
            term = _apply_age_terminal(post, now)
            if term is not None:
                led.posts[post.id] = post.model_copy(update=term["update"])
                log("reconcile", post.id, term["log"])
                continue
            log("reconcile", post.id, f"left: {status or 'unknown'}")
    return led


def report_terminals(led: Ledger, now: Optional[datetime] = None) -> list[dict]:
    """REPORT-ONLY: for every reconcilable post, what the (state, age) escalation WOULD write THIS pass —
    consulting the SAME `_apply_age_terminal` the live pass uses, but WRITING NOTHING.

    TWO kinds of row, one shape (`post_id`/`state`/`event`/`would_set_state`/`reason`), escalations first:

      escalation — the one surviving ladder rung (submitting -> needs_reconcile). `would_set_state`
                   DIFFERS from `state`: this WOULD write.
      lateness   — `pending_lateness` below. `would_set_state` EQUALS `state`: nothing would write, the
                   row is here so the preview shows the pending posts the backend has stopped resolving
                   instead of leaving them invisible until an operator goes looking.

    An empty list means the escalation would write nothing AND nothing pending is past its schedule."""
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    for post in led.posts.values():
        if post.state not in _RECONCILABLE:
            continue
        term = _apply_age_terminal(post, now)
        if term is None:
            continue
        upd = term["update"]
        new_state = upd["state"].value if upd.get("state") is not None else post.state.value
        out.append({"post_id": post.id, "state": post.state.value, "event": term["log"],
                    "would_set_state": new_state, "reason": upd.get("error_reason", "")})
    for late in pending_lateness(led, now):
        # MOL-791: lateness rides the SAME preview so `--report-terminals` shows the whole picture the
        # ladder used to hide behind a give-up stamp. These rows are REPORT-ONLY in the strongest sense:
        # `would_set_state` IS the current state, because nothing would happen to a late post — that is
        # the entire point (waiting is not failing). The key set is identical to an escalation row's, so
        # cli.py's single loop renders both without knowing there are two kinds.
        lp = led.posts[late["post_id"]]
        out.append({"post_id": lp.id, "state": lp.state.value, "event": "note lateness",
                    "would_set_state": lp.state.value,
                    "reason": (f"{late['hours_late']}h past scheduled_time; "
                               f"postiz_state={late['postiz_state'] or 'never mirrored'}")})
    return out


def pending_lateness(led: Ledger, now: Optional[datetime] = None) -> list[dict]:
    """REPORT-ONLY: every pending post whose schedule has passed and whose mirrored Postiz row has NOT
    reported publication. This is the LATENESS the module header names — DERIVED from the row and the
    schedule at read time, never stamped, because a stamp is a decision and this module makes none.

    PURE: reads the ledger, writes nothing, and is a function of (state, schedule, submission id,
    postiz_state) alone. One dict per late post — `post_id`, `platform`, `hours_late`, `postiz_state`
    (None = never mirrored) — in ledger order, like every other read-only surface here.

    The predicate, whole:

      state in _RECONCILABLE   — the pending set. `queued` is deliberately OUT: it has no outbound
                                 attempt and therefore no backend row to be late against.
      real submission id       — a `fanops_` client token can never match a Postiz window row, so its
                                 postiz_state is permanently unknowable; reporting it as late would be
                                 a claim about a backend we cannot have heard from.
      _parked_age > 0          — the schedule has actually passed (a future post is not late).
      postiz_state != PUBLISHED — the row is still non-terminal. A pending post whose row DID publish is
                                 held back by a liveness gate, not by the backend; that is a different
                                 problem and this section must not claim it as lateness.

    Deliberately NO grace window: "past schedule" is the whole test, so a post that parked
    `needs_reconcile` moments ago reports 0h. Inventing a threshold would be this module deciding
    when waiting becomes a problem — exactly the judgement the give-up rung was deleted for."""
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    for post in led.posts.values():
        if post.state not in _RECONCILABLE or not is_real_submission_id(post.submission_id):
            continue
        age = _parked_age(post, now)
        if age is None or age.total_seconds() <= 0:
            continue
        if (post.postiz_state or "").strip().upper() == _MIRROR_PUBLISHED:
            continue
        out.append({"post_id": post.id, "platform": post.platform.value,
                    "hours_late": int(age.total_seconds() // 3600), "postiz_state": post.postiz_state})
    return out
