"""Publish stage. publish_due(now) submits ONLY posts whose scheduled_time <= now (FIX F12 —
v1 dumped the whole queue at once). Crash-safe: mark a post 'submitting' and SAVE before the
network call, so a crash mid-submit cannot lose the fact and cause a duplicate live post on
resume (FIX F11). Media is ensured ONCE PER CLIP (FIX F44). Failed submit -> PostState.failed
(retryable), never analyzed (FIX F22). Held/retired clips never reach here (crosspost skips)."""
from __future__ import annotations
import json
import os
import random
import re
import time
from datetime import datetime, timezone
import requests
from pathlib import Path
from fanops.config import Config
from fanops.accounts import Accounts
from fanops.errors import AuthError, redact
from fanops.ledger import Ledger
from fanops.models import Post, PostState, is_real_submission_id
from fanops.post import get_poster, get_media_uploader
from fanops.post.media import ensure_clip_media, _uploader_kwargs
from fanops.timeutil import parse_iso as _parse, iso_z, publish_buckets as _publish_buckets, is_scheduled_due, schedule_utc
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
        hook = ""
        try:
            hook = _moment_hook(Ledger.load(cfg), post)
        except Exception:
            pass
        rec = {"post_id": post.id, "clip_id": post.parent_id, "account": post.account,
               "platform": post.platform.value, "caption": post.caption, "hashtags": list(post.hashtags or []),
               "public_url": post.public_url, "scheduled_time": post.scheduled_time,
               "created_at": post.created_at, "published_at": post.published_at,
               "render_id": post.render_id, "hook": hook,
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

_PUBLISH_TRANSIENT_MAX = 3   # MOL-115: bounded retry for pre-send / upload transients; never a hot loop
_DAEMON_TRANSIENT_MAX = 3    # MOL-125: daemon re-queue cycles for failed-but-transient (no submission_id)


def _is_transient_publish_error(exc: Exception) -> bool:
    """True for network/timeout/5xx blips where retrying (or parking needs_reconcile) beats terminal failed.
    Permanent 4xx/auth/validation -> False (retrying won't help). AuthError is never transient."""
    if isinstance(exc, AuthError):
        return False
    if isinstance(exc, requests.exceptions.RequestException):
        return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout,
                                requests.exceptions.Timeout, requests.exceptions.ReadTimeout))
    if isinstance(exc, RuntimeError):
        msg = str(exc)
        lower = msg.lower()
        m = re.search(r'\((\d{3})\)', msg)
        if m:
            code = int(m.group(1))
            if code == 401:
                return False
            if 400 <= code < 500:
                return False
            if 500 <= code < 600:
                return True
        if "upstream request 401" in lower and "timed out" in lower:
            return False
        if any(x in lower for x in ("nameresolution", "name resolution", "failed to resolve",
                                    "read timed out", "max retries exceeded", "connection refused",
                                    "connection reset", "connection aborted")):
            return True
        if "timed out" in lower or "timeout" in lower:
            return True
    return False


def _is_fatal_auth_error(exc: Exception) -> bool:
    """Auth/config errors mean EVERY post will fail — halt the run instead of marking one post
    failed and grinding through the rest. Matched by the TYPE AuthError (base of PostizAuthError +
    ZernioAuthError), NOT by a substring in the message (AUDIT H8): the old `"401" in msg or
    "API_KEY" in msg` both UNDER-fired (a reworded auth error slipped past and burned the
    whole queue — the F52 regression) and OVER-fired (a 5xx whose body contained "401" wrongly
    halted). Each backend's poster/media uploader raises an AuthError subclass on a real auth
    failure; everything else is a per-post failure."""
    return isinstance(exc, AuthError)

# Network-determined fields merged back at finalize: the union a poster.publish mutates
# ({state, submission_id, error_reason, public_url}) + the two run.py sets here (media_urls upload
# result, published_at stamp). The throwaway network ledger is otherwise DISCARDED — only these
# travel into the persisted ledger, so a concurrent writer's other changes are never clobbered (B4).
# XC-5: account_id is merged back so a published post records the integration it ACTUALLY published to
# (the network-phase refresh) — "in-flight wins" is deliberate (a post must carry the id it published TO,
# not a remap that landed after the POST). The finalize writes it ONLY when it changed, so a concurrent
# Go-Live remap to a DIFFERENT channel is not churn-clobbered by an identical value.
# Report 11 §5: reconcile_candidate_id rides here for ONE reason — a poster writes it on the throwaway
# network ledger, so without it in this union the write is silently DISCARDED at finalize and the operator
# loses the only pointer a 409 handed back. It is propagation only; run.py never reads or acts on it.
_NET_POST_FIELDS = ("state", "submission_id", "error_reason", "public_url", "media_urls", "published_at", "account_id",
                    "reconcile_candidate_id")

# Sprint 2: per-(backend, integration) publish throttle — in-process only (daemon is single-process).
_publish_throttle_last: dict[tuple[str, str], float] = {}

# Indirection so the throttle/retry WAIT is stubbable (mirrors llm.py's `_sleep`). Production points at the
# real time.sleep; the test suite neutralizes it globally (tests/conftest.py autouse) so no test ever burns
# real wall-clock seconds on the publish throttle. The throttle LOGIC still runs (per_min unchanged).
_sleep = time.sleep


def reset_publish_throttle() -> None:
    """Test-only: clear the in-process publish throttle state."""
    _publish_throttle_last.clear()


def _publish_throttle_key(provider: str, account_id: str | None) -> tuple[str, str]:
    return (provider, (account_id or "").strip() or "_")


def _publish_throttle_wait(cfg: Config, provider: str, account_id: str | None) -> None:
    """Sleep if the last publish on this (provider, integration) was too recent. Postiz-only when live."""
    if provider != "postiz" or not cfg.is_live:
        return
    per_min = cfg.postiz_publish_per_min
    if per_min <= 0:
        return
    min_gap = 60.0 / per_min
    key = _publish_throttle_key(provider, account_id)
    now = time.monotonic()
    last = _publish_throttle_last.get(key)
    if last is not None:
        wait = min_gap - (now - last)
        if wait > 0:
            _sleep(wait)
    _publish_throttle_last[key] = time.monotonic()


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



def _moment_hook(led, post: Post) -> str:
    clip = led.clips.get(post.parent_id)
    if clip is None: return ""
    m = led.moments.get(clip.parent_id)
    return (m.hook or "").strip() if m is not None else ""


def _materialize_variant_media(led: Ledger, cfg: Config, post: Post, accts: Accounts) -> None:
    """P9: owner-moment hook is burned on the shared clip at render_moment — no per-post materialize."""
    return

def _resolve_publish_account_id(accounts: Accounts, post: Post, *, cfg: Config | None = None) -> str | None:
    """The CURRENT poster/integration id for this post's channel, re-resolved at publish time so a Go-Live
    integration REMAP since crosspost reaches the post (account_id is otherwise frozen onto the post at
    crosspost). FAIL-OPEN: an unresolvable channel (removed account / empty id) returns None and the frozen
    post.account_id stands — never crash a publish over a mapping lookup. #10: when cfg is threaded in, the
    fallback breadcrumbs so the frozen-id use is visible, not silent."""
    try:
        return accounts.resolve_account_id(post.account, post.platform)
    except Exception as e:
        if cfg is not None:                              # #10: breadcrumb when the frozen-id fallback fires (safe value None unchanged)
            get_logger(cfg)("publish", getattr(post, "id", "-"), "account_id_fallback", account=post.account, platform=post.platform.value, err=str(e)[:120])
        return None


def _ensure_media(led: Ledger, cfg: Config, post: Post, backend: str, *, account_id: str | None = None) -> None:
    """Resolve post.media_urls to network-fetchable URLs (FIX F44 cache on the Clip). In-memory only;
    runs in the LOCK-FREE network phase. `backend` is the POST's resolved backend (per-account routing),
    not the global — so a TikTok-via-Zernio variant uploads to Zernio even if the global is Postiz."""
    aid = (account_id or post.account_id or "").strip() or None
    _materialize_variant_media(led, cfg, post, Accounts.load(cfg))
    from fanops.post.compress import apply_shrink_to_post, upload_cap_bytes
    if upload_cap_bytes(cfg, post, backend) is not None:
        apply_shrink_to_post(cfg, led, post, backend=backend)
    if not post.media_urls:
        post.media_urls = [ensure_clip_media(led, cfg, post.parent_id, backend, account_id=aid)]
    elif backend != "dryrun":
        # AUDIT (stage-6 HIGH): a variant post is BORN with media_urls=["file://<variant render>"]
        # (crosspost.py stamps the per-account hook-burned file). Pre-stamped media used to skip the
        # upload and ship the LOCAL path to the hosted backend, which cannot fetch it — every live variant post
        # died. Upload the variant FILE itself, NOT ensure_clip_media (the clip cache holds the
        # parent's BASE render — using it would drop the burned hook). dryrun keeps file:// (offline).
        from fanops.post.media import ensure_render_media
        new = []
        for u in post.media_urls:
            if u.startswith("file://") and post.render_id:
                new.append(ensure_render_media(led, cfg, post.render_id, u.removeprefix("file://"), backend,
                                               account_id=aid))   # CULM-2: once per render; Zernio needs the id to mint
            elif u.startswith("file://"):
                new.append(get_media_uploader(cfg, backend)(cfg, Path(u.removeprefix("file://")),
                                                            **_uploader_kwargs(backend, aid)))
            else:
                new.append(u)
        post.media_urls = new


def _missing_integration_id(backend: str, account_id: str | None, post: Post) -> bool:
    """CULM-1: a live backend with no integration id would ship integration:{id:\"\"} — never POST."""
    return backend != "dryrun" and not ((account_id or post.account_id or "").strip())


def _unclaim_no_integration(cfg: Config, post_id: str, post: Post, *, unclaim: bool) -> None:
    """Log no_integration_id and optionally un-claim submitting->queued (inner path after claim)."""
    if unclaim:
        with Ledger.transaction(cfg) as led2:
            p2 = led2.posts.get(post_id)
            if p2 is not None and p2.state is PostState.submitting:
                p2.state = PostState.queued
    get_logger(cfg)("publish", post_id, "no_integration_id", account=post.account, platform=post.platform.value)


def _publish_one(cfg: Config, post_id: str, backend: str, *, accounts: "Accounts | None" = None,
                 account_id: str | None = None,
                 _tally: dict | None = None, due_cutoff: datetime | None = None) -> str | None:
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
    # Pre-claim guard (CULM-1): same gate as publish_due — never claim a post we can't address.
    pre = Ledger.load(cfg).posts.get(post_id)
    if pre is not None and pre.state is PostState.queued and _missing_integration_id(backend, account_id, pre):
        _unclaim_no_integration(cfg, post_id, pre, unclaim=False)
        if _tally is not None:
            _tally["no_integration_id"] = _tally.get("no_integration_id", 0) + 1
        return None
    # ---- CLAIM ----
    with Ledger.transaction(cfg) as led:
        post = led.posts.get(post_id)
        if post is None or post.state is not PostState.queued:
            return None                                # lost the race / not eligible — no-op (F11)
        if due_cutoff is not None and not is_scheduled_due(post, due_cutoff):   # M08: re-check dueness under lock
            return None
        # RC-3b (S07): the producer and the SOLE consumer of `submitting` must agree on backend capability.
        # A post may enter `submitting` ONLY on a channel `channel_provider_if_ready` ADMITS — the exact
        # per-channel predicate `is_live_backend`/`live_ready_channels` gate reconcile (the sole resolver of
        # `submitting`) on. Before S07 the producer claimed whenever a provider merely RESOLVED, while the
        # consumer additionally required CREDS — so a cred-less live channel minted a `submitting` post that
        # reconcile, disabled by that very same missing creds, would never touch (stranded forever, the
        # producer/consumer gating asymmetry). Refuse the claim HERE, cleanly: leave it `queued` and visible.
        # (`accounts is None` only for the direct-internal test callers that exercise the network path; the
        # two production producers — publish_due, publish_post — always pass it.)
        if accounts is not None and accounts.channel_provider_if_ready(post.account, post.platform) is None:
            get_logger(cfg)("publish", post_id, "skip_not_live_ready", account=post.account, platform=post.platform.value)
            if _tally is not None:
                _tally["not_live_ready"] = _tally.get("not_live_ready", 0) + 1
            return None                                # leave it `queued` — reconcile is not available for this channel
        # RC-1 (S03): refuse the claim HERE for a post that ALREADY carries a real submission_id — it has
        # been POSTed, and re-POSTing the SAME post id is the double-POST we forbid (MOL-115). Declining
        # INSIDE the claim is a clean no-op: the post stays `queued` and visible. The bug this fixes was
        # declining ONE PHASE LATER, in the network phase, AFTER the claim had already committed
        # `submitting` — which stranded the post claimed-but-never-published with nothing to un-claim it.
        # (Reposting CONTENT freely is `repost_post`, which mints a NEW id. PD-1: refuse + surface the
        # skipped count, WITHOUT a republish action.)
        if is_real_submission_id(post.submission_id):
            get_logger(cfg)("publish", post_id, "skip_resubmit_existing_id", sub=post.submission_id)
            if _tally is not None:
                _tally["skip_resubmit_existing_id"] = _tally.get("skip_resubmit_existing_id", 0) + 1
            return None                                # leave it `queued` — never claimed, never stranded
        post.state = PostState.submitting              # crash-safe intent, persisted on txn exit (F11/B4)
    # ---- NETWORK (no lock held) ----
    led = Ledger.load(cfg)
    post = led.posts.get(post_id)
    if post is None or post.state is not PostState.submitting:
        return None                                    # vanished/changed under us — leave it be
    if account_id and account_id != post.account_id:   # #1: a Go-Live integration REMAP since crosspost
        get_logger(cfg)("publish", post_id, "account_id_refreshed", was=post.account_id, new=account_id)
        post.account_id = account_id                    # send the CURRENT integration id, not the frozen one
    if _missing_integration_id(backend, None, post):
        # CULM-1: defensive post-claim — id cleared under us or publish_post skipped the pre-claim tally.
        _unclaim_no_integration(cfg, post_id, post, unclaim=True)
        if _tally is not None:
            _tally["no_integration_id"] = _tally.get("no_integration_id", 0) + 1
        return None
    if is_real_submission_id(post.submission_id):
        # MOL-115 defense-in-depth: RC-1's guard now lives in the CLAIM, which refuses a real-id post and
        # leaves it `queued`, so this branch is UNREACHABLE on the normal path. If a concurrent writer
        # stamped a real id onto this `submitting` post between claim and here, refuse the POST and leave
        # it `submitting`: the post WAS posted before (that is why it carries a real id), so reconcile
        # POLLS that id and resolves it — exactly its job for a stranded `submitting`. Never double-POST,
        # never re-drive it here, and never un-claim it back to `queued` (which would re-attempt a publish).
        get_logger(cfg)("publish", post_id, "skip_resubmit_existing_id", sub=post.submission_id)
        if _tally is not None:
            _tally["skip_resubmit_existing_id"] = _tally.get("skip_resubmit_existing_id", 0) + 1
        return None
    poster = get_poster(cfg, backend)              # per-account backend (slice 2), default = global
    delay = 0.5
    for attempt in range(_PUBLISH_TRANSIENT_MAX):
        try:
            _ensure_media(led, cfg, post, backend, account_id=post.account_id)
            _publish_throttle_wait(cfg, backend, post.account_id)   # throttle only before the real POST
            led = poster.publish(led, post.id)
            post = led.posts[post_id]
            if post.state is PostState.submitted:
                # R1/D2: gate the submitted -> published promotion on public_url. A backend that returns
                # 'submitted' without a permalink (a Postiz async-permalink case, a misbehaving stub, or
                # the pre-R1 DryRunPoster) MUST park in needs_reconcile — reconcile.py back-fills the URL
                # on the next pass. Without this gate, the post promotes to 'published' with public_url=''
                # and the Pydantic R1 invariant would refuse the ledger save below; fail-closed BEFORE
                # construction so the operator sees a clean needs_reconcile row, not a ValidationError 500.
                if (post.public_url or "").strip():
                    post.state = PostState.published
                    post.published_at = iso_z(datetime.now(timezone.utc))   # TRUE publish time (Posted-archive day-anchor)
                    # Leg 3 (timing): bucket the true publish time into operator-local (hour, weekday) so
                    # timing_bias can rank reach-by-hour without every reader re-doing tz math. Single tz
                    # home (timeutil.publish_buckets); fail-safe (None,None) leaves the dim unranked.
                    post.publish_hour, post.publish_dow = _publish_buckets(post.published_at, cfg)
                else:
                    post.state = PostState.needs_reconcile
                    post.error_reason = ("publish_missing_url: backend returned submitted without a permalink — "
                                         "reconcile will back-fill on next pass (R1/D2 gate)")
                    get_logger(cfg)("publish", post_id, "publish_missing_url",
                                    backend=backend, submission_id=post.submission_id)
            break                                        # poster decided (submitted/needs_reconcile/failed) or promoted
        except Exception as exc:
            if _is_fatal_auth_error(exc):
                raise                                  # bad key/401: halt, don't burn the queue (H8)
            if _is_transient_publish_error(exc) and attempt < _PUBLISH_TRANSIENT_MAX - 1:
                _sleep(delay + random.uniform(0, delay * 0.5)); delay = min(delay * 2, 8.0)
                continue
            if post.state is not PostState.needs_reconcile:   # C1/#17: don't downgrade an ambiguous-live park
                if _is_transient_publish_error(exc):
                    red = redact(str(exc), cfg.postiz_api_key, cfg.zernio_api_key)
                    if is_real_submission_id(post.submission_id):
                        post.state = PostState.needs_reconcile
                        post.error_reason = "publish transient error (retries exhausted): " + red
                    else:
                        from fanops.studio.views_common import transient_daemon_retry_count
                        n = transient_daemon_retry_count(post.error_reason)
                        post.state = PostState.failed
                        msg = "publish failed: " + red
                        post.error_reason = (f"transient_daemon_retry={n}/{_DAEMON_TRANSIENT_MAX}|{msg}"
                                             if n else msg)
                else:
                    post.state = PostState.failed
                    post.error_reason = "publish failed: " + redact(str(exc), cfg.postiz_api_key,
                                                                    cfg.zernio_api_key)   # scrub any leaked key
            break
    net = {f: getattr(post, f) for f in _NET_POST_FIELDS}
    clip = led.clips.get(post.parent_id)
    clip_media = clip.media_url if clip is not None else None   # carry the F44 upload cache forward
    render = led.get_render(post.render_id) if post.render_id else None
    render_media = render.media_url if render is not None else None   # CULM-2: persist the once-per-render upload
    render_path = render.path if render is not None else None         # shrink may update render.path pre-upload
    final_state = net["state"]
    # ---- FINALIZE ----
    with Ledger.transaction(cfg) as led:
        p = led.posts.get(post_id)
        if p is None:
            return final_state.value if final_state else None   # gone (shouldn't happen) — nothing to merge
        for f, v in net.items():
            if f == "account_id" and v == getattr(p, f): continue   # XC-5: don't rewrite an unchanged id over a fresher on-disk one
            setattr(p, f, v)
        c = led.clips.get(p.parent_id)
        if c is not None and clip_media and not c.media_url:
            c.media_url = clip_media                   # persist the once-per-clip upload (FIX F44)
        r = led.get_render(p.render_id) if p.render_id else None
        if r is not None and render_media and not r.media_url:
            r.media_url = render_media                 # CULM-2: persist the once-per-render upload (FIX-F44 parity)
        if p.render_id and render_path:
            r2 = led.get_render(p.render_id)
            if r2 is not None and r2.path != render_path:
                led.renders[p.render_id] = r2.model_copy(update={"path": render_path})
    # content-lifecycle Phase 3: fail-open day-bucketed record, OUTSIDE the finalize txn so an archive
    # write can NEVER roll back the just-committed publish. The network-phase `post` carries every field
    # the archive reads (loaded from disk) PLUS the network mutations. Fires only on a confirmed publish.
    if final_state is PostState.published:
        _archive_published(cfg, post)
    return final_state.value if final_state else None


def _due_or_fail(cfg: Config, post: Post, cutoff: datetime) -> bool:
    """Schedule gate (FIX F12): True if the post is due now. Unparseable scheduled_time is a per-post FAILURE
    (mark failed in a short txn, FIX F54). Naive parseable times are canonical UTC (M07)."""
    if not post.scheduled_time:
        # CULM-4: a queued post with NO scheduled_time is NOT due — it parks (breadcrumb, stays queued), so a
        # timeless queued post can no longer auto-publish (no-auto-publish defense-in-depth; clear_time
        # un-approves first, but enforce it in code not by convention). publish_post (manual) is unaffected.
        get_logger(cfg)("publish", post.id, "timeless_queued_parked", account=post.account, platform=post.platform.value)
        return False
    if schedule_utc(post.scheduled_time) is None:
        with Ledger.transaction(cfg) as led:
            p = led.posts.get(post.id)
            if p is not None and p.state is PostState.queued:
                p.state = PostState.failed
                p.error_reason = f"bad schedule time {post.scheduled_time!r}: unparseable"
        return False
    return is_scheduled_due(post, cutoff)


def _requeue_transient_failed_for_daemon(cfg: Config) -> int:
    """MOL-125: before publish_due, re-queue failed transient posts (no real submission_id) for another
    daemon attempt. Bounded by _DAEMON_TRANSIENT_MAX — after that they stay terminal failed."""
    from fanops.studio.views_common import is_transient_failure_reason, transient_daemon_retry_count
    from fanops.timeutil import iso_z
    requeued = 0
    led = Ledger.load(cfg)
    candidates = [p for p in led.posts_in_state(PostState.failed)
                  if not is_real_submission_id(p.submission_id)
                  and is_transient_failure_reason(p.error_reason)
                  and transient_daemon_retry_count(p.error_reason) < _DAEMON_TRANSIENT_MAX]
    if not candidates:
        return 0
    now = datetime.now(timezone.utc)
    try:
        with Ledger.transaction(cfg) as lg:
            for p in candidates:
                cur = lg.posts.get(p.id)
                if cur is None or cur.state is not PostState.failed:
                    continue
                if is_real_submission_id(cur.submission_id):
                    continue
                if not is_transient_failure_reason(cur.error_reason):
                    continue
                n = transient_daemon_retry_count(cur.error_reason) + 1
                if n > _DAEMON_TRANSIENT_MAX:
                    continue
                cur.state = PostState.queued
                cur.submission_id = None
                cur.error_reason = f"transient_daemon_retry={n}/{_DAEMON_TRANSIENT_MAX}|"
                if not (cur.scheduled_time or "").strip():
                    cur.scheduled_time = iso_z(now)
                requeued += 1
    except Exception:
        return requeued
    return requeued


def publish_due(cfg: Config, *, now: str | None = None, account: str | None = None, batch_id: str | None = None) -> dict:
    """Publish every DUE queued post, each via _publish_one (network OUTSIDE the ledger lock). Only
    'queued' is considered: a 'submitting' post stranded by a crash is NOT re-driven here (reconcile's
    job — auto-resubmitting could double-post a live post, FIX F11). A FATAL AuthError propagates
    (halt the queue, H8). Returns a small summary."""
    cutoff = _now(now)
    accounts = Accounts.load(cfg)                      # one load; per-post provider resolved off it (M3)
    _requeue_transient_failed_for_daemon(cfg)          # MOL-125: bounded daemon retry for transient failed
    led = Ledger.load(cfg)                             # lock-free snapshot of the due queue
    due = [post for post in led.posts_in_state(PostState.queued) if _due_or_fail(cfg, post, cutoff)]
    if account:
        due = [p for p in due if p.account == account]
    if batch_id:
        due = [p for p in due if p.batch_id == batch_id]
    if due:                                            # on-demand: start the local Postiz stack ONLY when there is work
        from fanops.postiz_lifecycle import ensure_up
        ensure_up(cfg)
    log = get_logger(cfg)
    published = no_provider = no_integration_id = not_distributed = skipped_existing_id = not_live_ready = 0
    for post in due:
        provider = _post_provider(cfg, accounts, post)
        if provider is None:                           # live but the channel has no provider -> skip, leave queued
            no_provider += 1
            log("publish", post.id, "no_provider", account=post.account, platform=post.platform.value)
            continue
        if provider == "dryrun":                       # dryrun-boundary (Finding #1): NOT live -> no real backend to
            not_distributed += 1                       # distribute to. The post is built + approved + scheduled; it
            from fanops.post.dryrun import write_preview   # M2: the boundary is the sole place a dryrun post is now
            try:                                          # processed, so the would-send preview sidecar is written HERE
                write_preview(cfg, post)                  # (DryRunPoster.publish is never reached post-M1). Fail-open:
            except Exception as exc:                      # a preview-write error must still leave the post cleanly queued.
                log("publish", post.id, "preview_write_failed", err=str(exc)[:120])
            log("publish", post.id, "dryrun_not_distributed",   # halts here at the processing<->distribution seam,
                account=post.account, platform=post.platform.value)   # staying `queued` — never claimed, never a
            continue                                   # phantom-published row. A live-flip re-derives this each pass.
        acct_id = _resolve_publish_account_id(accounts, post, cfg=cfg)   # #10: cfg breadcrumbs a frozen-id fallback
        tally: dict = {}
        if _publish_one(cfg, post.id, provider, accounts=accounts, account_id=acct_id,
                        _tally=tally, due_cutoff=cutoff) == PostState.published.value:
            published += 1
        no_integration_id += tally.get("no_integration_id", 0)
        skipped_existing_id += tally.get("skip_resubmit_existing_id", 0)   # RC-1/S03: refused-at-claim, left queued
        not_live_ready += tally.get("not_live_ready", 0)                   # RC-3b/S07: cred-less channel, left queued
    return {"due": len(due), "published": published, "no_provider": no_provider,
            "no_integration_id": no_integration_id, "not_distributed": not_distributed,
            "skipped_existing_id": skipped_existing_id, "not_live_ready": not_live_ready}


def publish_post(cfg: Config, post_id: str) -> str | None:
    """Publish ONE queued post NOW, IGNORING its schedule — the operator clicked 'Publish now' in the
    Studio. Same per-post claim->network->finalize path as publish_due but with NO due-gate and scoped
    to a single post. A missing/non-queued post is a no-op (returns None). A FATAL AuthError propagates
    (halt), matching publish_due. Returns the final post-state value (e.g. 'published'/'failed') or
    None when nothing was claimable."""
    from fanops.postiz_lifecycle import ensure_up
    ensure_up(cfg)                                     # operator clicked Publish-now: bring the local stack up
    post = Ledger.load(cfg).posts.get(post_id)         # resolve the per-channel provider for this one post
    if post is None:
        return None                                    # no such post -> nothing to claim
    accounts = Accounts.load(cfg)                      # resolve the per-channel provider + current integration id
    provider = _post_provider(cfg, accounts, post)
    if provider is None:                               # live but the channel has no provider -> can't publish
        get_logger(cfg)("publish", post_id, "no_provider", account=post.account, platform=post.platform.value)
        return None
    if provider == "dryrun":                           # dryrun-boundary (M2): NOT live -> no backend to distribute to,
        from fanops.post.dryrun import write_preview   # even on an explicit Publish-now click. Write the would-send
        try:                                           # preview and HALT `queued` (never claim -> never a stuck
            write_preview(cfg, post)                   # `submitting` post now that the poster no longer promotes state).
        except Exception as exc:
            get_logger(cfg)("publish", post_id, "preview_write_failed", err=str(exc)[:120])
        get_logger(cfg)("publish", post_id, "dryrun_not_distributed",
                        account=post.account, platform=post.platform.value)
        return PostState.queued.value
    return _publish_one(cfg, post_id, provider, accounts=accounts,   # RC-3b/S07: share the readiness gate
                        account_id=_resolve_publish_account_id(accounts, post, cfg=cfg))   # #10: cfg breadcrumbs a frozen-id fallback
