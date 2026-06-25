"""Publish stage. publish_due(now) submits ONLY posts whose scheduled_time <= now (FIX F12 —
v1 dumped the whole queue at once). Crash-safe: mark a post 'submitting' and SAVE before the
network call, so a crash mid-submit cannot lose the fact and cause a duplicate live post on
resume (FIX F11). Media is ensured ONCE PER CLIP (FIX F44). Failed submit -> PostState.failed
(retryable), never analyzed (FIX F22). Held/retired clips never reach here (crosspost skips)."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.errors import AuthError
from fanops.ledger import Ledger
from fanops.models import Post, PostState
from fanops.post import get_poster, get_media_uploader
from fanops.post.media import ensure_clip_media
from fanops.timeutil import parse_iso as _parse, iso_z
from fanops.log import get_logger

def _now(now: str | None) -> datetime:
    return _parse(now) if now else datetime.now(timezone.utc)

def _archive_published(cfg: Config, post: Post) -> None:
    """Day-bucketed, human-browsable record of a just-published post -> 06_published/<YYYY-MM-DD>/<post_id>.json
    (the dir existed but nothing wrote it). FAIL-OPEN: any write/mkdir error is logged and swallowed — the
    archive is a convenience artifact, NEVER a publish blocker (a full disk must not strand a live post). Day =
    post.published_at, else created_at, else scheduled_time, else now (content-lifecycle Phase 3)."""
    try:
        day = None
        for ts in (post.published_at, post.created_at, post.scheduled_time):
            if ts:
                try:
                    dt = _parse(ts)
                    if dt.tzinfo is not None: day = dt.date().isoformat(); break
                except (ValueError, TypeError): pass
        if day is None: day = datetime.now(timezone.utc).date().isoformat()
        d = cfg.published / day; d.mkdir(parents=True, exist_ok=True, mode=0o700)
        try: os.chmod(d, 0o700)             # L2 (audit): tighten a pre-existing world-listable day dir too
        except OSError: pass
        rec = {"post_id": post.id, "clip_id": post.parent_id, "account": post.account,
               "platform": post.platform.value, "caption": post.caption, "hashtags": list(post.hashtags or []),
               "public_url": post.public_url, "scheduled_time": post.scheduled_time,
               "created_at": post.created_at, "published_at": post.published_at,
               # Render foundation: durably record the per-account render identity (id + the on-screen hook
               # text + the file path) so "what hook/media shipped for @a on this day" is reconstructable
               # forever — even after the Render entity + its file are GC-swept from the live ledger.
               "render_id": post.render_id, "variant_hook": post.variant_hook,
               "media": (post.media_urls[0] if post.media_urls else None)}
        ap = d / f"{post.id}.json"
        # L2 (audit): create 0600 ATOMICALLY (no write-then-chmod world-readable window) — the archive carries
        # the operator handle + live permalink + creative. Mirrors log.py's create-0600 pattern.
        with os.fdopen(os.open(ap, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(rec, fh, indent=2, ensure_ascii=False)
        try: os.chmod(ap, 0o600)            # tighten a re-archived file that pre-existed at a looser mode (O_TRUNC keeps it)
        except OSError: pass
    except Exception as exc:
        try: get_logger(cfg)("publish", post.id, "archive_error", err=str(exc)[:160])
        except Exception: pass

def _is_fatal_auth_error(exc: Exception) -> bool:
    """Auth/config errors mean EVERY post will fail — halt the run instead of marking one post
    failed and grinding through the rest. Matched by the TYPE AuthError (base of BlotatoAuthError +
    PostizAuthError), NOT by a substring in the message (AUDIT H8): the old `"401" in msg or
    "BLOTATO_API_KEY" in msg` both UNDER-fired (a reworded auth error slipped past and burned the
    whole queue — the F52 regression) and OVER-fired (a 5xx whose body contained "401" wrongly
    halted). Each backend's poster/media uploader raises an AuthError subclass on a real auth
    failure; everything else is a per-post failure."""
    return isinstance(exc, AuthError)

# Network-determined fields merged back at finalize: the union a poster.publish mutates
# ({state, submission_id, error_reason, public_url}) + the two run.py sets here (media_urls upload
# result, published_at stamp). The throwaway network ledger is otherwise DISCARDED — only these
# travel into the persisted ledger, so a concurrent writer's other changes are never clobbered (B4).
_NET_POST_FIELDS = ("state", "submission_id", "error_reason", "public_url", "media_urls", "published_at")


def _post_provider(cfg: Config, accounts: Accounts, post: Post) -> str | None:
    """The provider to publish THIS post (M3 — provider is per-channel, live is global). `dryrun` when the
    system is NOT live (cfg.is_live False -> write payloads, post NOTHING; the global on/off switch governs
    ALL channels, even one with an explicit provider — so dryrun can never be bypassed by a per-channel
    override). When live: the channel's effective provider (explicit accounts.json provider, else the
    legacy-global bridge). None when live but the channel has NO provider -> publish SKIPS it with a
    breadcrumb (never global-defaults a new deployment, never marks it failed)."""
    if not cfg.is_live:
        return "dryrun"
    return accounts.effective_provider(post.account, post.platform)


def _ensure_media(led: Ledger, cfg: Config, post: Post, backend: str) -> None:
    """Resolve post.media_urls to network-fetchable URLs (FIX F44 cache on the Clip). In-memory only;
    runs in the LOCK-FREE network phase. `backend` is the POST's resolved backend (per-account routing),
    not the global — so a TikTok-via-Zernio variant uploads to Zernio even if the global is Postiz."""
    if not post.media_urls:
        post.media_urls = [ensure_clip_media(led, cfg, post.parent_id)]
    elif backend != "dryrun":
        # AUDIT (stage-6 HIGH): a variant post is BORN with media_urls=["file://<variant render>"]
        # (crosspost.py stamps the per-account hook-burned file). Pre-stamped media used to skip the
        # upload and ship the LOCAL path to Blotato, which cannot fetch it — every live variant post
        # died. Upload the variant FILE itself, NOT ensure_clip_media (the clip cache holds the
        # parent's BASE render — using it would drop the burned hook). dryrun keeps file:// (offline).
        _upload = get_media_uploader(cfg, backend)
        post.media_urls = [_upload(cfg, Path(u.removeprefix("file://")))
                           if u.startswith("file://") else u for u in post.media_urls]


def _publish_one(cfg: Config, post_id: str, backend: str) -> str | None:
    """Publish ONE post via claim -> network -> finalize, with the network OUTSIDE the ledger flock.

    CLAIM (tight txn): re-read under lock; publish ONLY if still 'queued' (the double-post guard — a
      lost race / already-submitting post is a clean no-op); flip 'queued'->'submitting' and persist
      BEFORE any network (FIX F11 crash-safety — a crash mid-network leaves it 'submitting', never
      re-driven, healed by reconcile/`fanops resolve`).
    NETWORK (lock-free): on a THROWAWAY loaded ledger, ensure media (upload) + poster.publish. A
      per-post failure marks THIS post failed (FIX F54); a needs_reconcile park is NOT downgraded to
      failed (AUDIT C1/#17 — failed is re-queueable => double-post); a FATAL AuthError RE-RAISES (H8).
    FINALIZE (tight txn): merge ONLY the network-determined post fields + the clip media cache into a
      FRESHLY loaded ledger — never persist the stale full snapshot (B4 lost-update). Returns the
      final post-state value (or None if not claimable)."""
    # ---- CLAIM ----
    with Ledger.transaction(cfg) as led:
        post = led.posts.get(post_id)
        if post is None or post.state is not PostState.queued:
            return None                                # lost the race / not eligible — no-op (F11)
        post.state = PostState.submitting              # crash-safe intent, persisted on txn exit (F11/B4)
    # ---- NETWORK (no lock held) ----
    led = Ledger.load(cfg)
    post = led.posts.get(post_id)
    if post is None or post.state is not PostState.submitting:
        return None                                    # vanished/changed under us — leave it be
    poster = get_poster(cfg, backend)                  # per-account backend (slice 2), default = global
    try:
        _ensure_media(led, cfg, post, backend)
        led = poster.publish(led, post.id)
        if post.state is PostState.submitted:
            post.state = PostState.published
            post.published_at = iso_z(datetime.now(timezone.utc))   # TRUE publish time (Posted-archive day-anchor)
    except Exception as exc:
        if _is_fatal_auth_error(exc):
            raise                                      # bad key/401: halt, don't burn the queue (H8)
        if post.state is not PostState.needs_reconcile:   # C1/#17: don't downgrade an ambiguous-live park
            post.state = PostState.failed
            post.error_reason = f"publish failed: {str(exc)[:200]}"
    net = {f: getattr(post, f) for f in _NET_POST_FIELDS}
    clip = led.clips.get(post.parent_id)
    clip_media = clip.media_url if clip is not None else None   # carry the F44 upload cache forward
    final_state = net["state"]
    # ---- FINALIZE ----
    with Ledger.transaction(cfg) as led:
        p = led.posts.get(post_id)
        if p is None:
            return final_state.value if final_state else None   # gone (shouldn't happen) — nothing to merge
        for f, v in net.items(): setattr(p, f, v)
        c = led.clips.get(p.parent_id)
        if c is not None and clip_media and not c.media_url:
            c.media_url = clip_media                   # persist the once-per-clip upload (FIX F44)
    # content-lifecycle Phase 3: fail-open day-bucketed record, OUTSIDE the finalize txn so an archive
    # write can NEVER roll back the just-committed publish. The network-phase `post` carries every field
    # the archive reads (loaded from disk) PLUS the network mutations. Fires only on a confirmed publish.
    if final_state is PostState.published:
        _archive_published(cfg, post)
    return final_state.value if final_state else None


def _due_or_fail(cfg: Config, post: Post, cutoff: datetime) -> bool:
    """Schedule gate (FIX F12): True if the post is due now. A malformed/naive scheduled_time (hand-edit,
    corruption) is a per-post FAILURE — marked failed in a short txn (FIX F54), returns False."""
    if not post.scheduled_time:
        return True                                    # no schedule => due now
    try:
        return _parse(post.scheduled_time) <= cutoff
    except (ValueError, TypeError) as exc:
        with Ledger.transaction(cfg) as led:
            p = led.posts.get(post.id)
            if p is not None and p.state is PostState.queued:
                p.state = PostState.failed
                p.error_reason = f"bad schedule time {post.scheduled_time!r}: {str(exc)[:120]}"
        return False


def publish_due(cfg: Config, *, now: str | None = None) -> dict:
    """Publish every DUE queued post, each via _publish_one (network OUTSIDE the ledger lock). Only
    'queued' is considered: a 'submitting' post stranded by a crash is NOT re-driven here (reconcile's
    job — auto-resubmitting could double-post a live post, FIX F11). A FATAL AuthError propagates
    (halt the queue, H8). Returns a small summary."""
    cutoff = _now(now)
    accounts = Accounts.load(cfg)                      # one load; per-post provider resolved off it (M3)
    led = Ledger.load(cfg)                             # lock-free snapshot of the due queue
    due = [post for post in led.posts_in_state(PostState.queued) if _due_or_fail(cfg, post, cutoff)]
    log = get_logger(cfg)
    published = no_provider = 0
    for post in due:
        provider = _post_provider(cfg, accounts, post)
        if provider is None:                           # live but the channel has no provider -> skip, leave queued
            no_provider += 1
            log("publish", post.id, "no_provider", account=post.account, platform=post.platform.value)
            continue
        if _publish_one(cfg, post.id, provider) == PostState.published.value:
            published += 1
    return {"due": len(due), "published": published, "no_provider": no_provider}


def publish_post(cfg: Config, post_id: str) -> str | None:
    """Publish ONE queued post NOW, IGNORING its schedule — the operator clicked 'Publish now' in the
    Studio. Same per-post claim->network->finalize path as publish_due but with NO due-gate and scoped
    to a single post. A missing/non-queued post is a no-op (returns None). A FATAL AuthError propagates
    (halt), matching publish_due. Returns the final post-state value (e.g. 'published'/'failed') or
    None when nothing was claimable."""
    post = Ledger.load(cfg).posts.get(post_id)         # resolve the per-channel provider for this one post
    if post is None:
        return None                                    # no such post -> nothing to claim
    provider = _post_provider(cfg, Accounts.load(cfg), post)
    if provider is None:                               # live but the channel has no provider -> can't publish
        get_logger(cfg)("publish", post_id, "no_provider", account=post.account, platform=post.platform.value)
        return None
    return _publish_one(cfg, post_id, provider)
