"""Publish stage. publish_due(now) submits ONLY posts whose scheduled_time <= now (FIX F12 —
v1 dumped the whole queue at once). Crash-safe: mark a post 'submitting' and SAVE before the
network call, so a crash mid-submit cannot lose the fact and cause a duplicate live post on
resume (FIX F11). Media is ensured ONCE PER CLIP (FIX F44). Failed submit -> PostState.failed
(retryable), never analyzed (FIX F22). Held/retired clips never reach here (crosspost skips)."""
from __future__ import annotations
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.accounts import Account, AccountStatus, Accounts
from fanops.errors import redact
from fanops.ledger import Ledger
from fanops.models import ErrorKind, Post, PostState, is_real_submission_id, validate_account_handle
from fanops.post import get_poster, get_media_uploader
from fanops.post.media import ensure_clip_media, _uploader_kwargs, _media_cache_hit
from fanops.post.publish_archive import _archive_published
from fanops.post.publish_dryrun import _handle_dryrun_boundary
from fanops.post.publish_errors import _is_fatal_auth_error, _is_transient_publish_error
from fanops.post.publish_requeue import _requeue_failed_posts
from fanops.timeutil import parse_iso as _parse, iso_z, publish_buckets as _publish_buckets, is_scheduled_due, schedule_utc
from fanops.log import get_logger

# Re-exports: keep test/studio/reconcile import sites stable (from fanops.post.run import …).
from fanops.post.publish_requeue import (  # noqa: F401
    _DAEMON_TRANSIENT_MAX,
    _requeue_rate_limited_for_daemon,
    _requeue_transient_failed_for_daemon,
)

def _now(now: str | None) -> datetime:
    return _parse(now) if now else datetime.now(timezone.utc)

_PUBLISH_TRANSIENT_MAX = 3   # MOL-115: bounded retry for pre-send / upload transients; never a hot loop

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
_NET_POST_FIELDS = ("state", "submission_id", "error_reason", "error_kind", "daemon_transient_retry",
                    "public_url", "media_urls", "published_at", "account_id", "reconcile_candidate_id")

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


def _local_media_path(led: Ledger, post: Post) -> Path | None:
    """Resolve the on-disk clip/render file for a post when a cached https URL must be re-uploaded."""
    path = None
    if post.render_id:
        r = led.get_render(post.render_id)
        if r is not None and getattr(r, "path", None):
            path = Path(r.path)
    if path is None:
        clip = led.clips.get(post.parent_id)
        if clip is not None and clip.path:
            path = Path(clip.path)
    return path


def _ensure_media(led: Ledger, cfg: Config, post: Post, backend: str, *, account_id: str | None = None) -> None:
    """Resolve post.media_urls to network-fetchable URLs (FIX F44 cache on the Clip). In-memory only;
    runs in the LOCK-FREE network phase. `backend` is the POST's resolved backend (per-account routing),
    not the global — so a TikTok-via-Zernio variant uploads to Zernio even if the global is Postiz."""
    aid = (account_id or post.account_id or "").strip() or None
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
            elif backend == "postiz":
                from fanops.post.postiz import media_host_postiz_can_fetch
                if media_host_postiz_can_fetch(u):
                    new.append(u)
                    continue
                path = _local_media_path(led, post)
                if path is None or not path.is_file():
                    raise ValueError(
                        f"{post.platform.value} post {post.id} media host is unreachable from Postiz "
                        f"and no local file remains to re-upload")
                new.append(get_media_uploader(cfg, backend)(cfg, path, **_uploader_kwargs(backend, aid)))
            elif backend == "zernio":
                from fanops.post.media import _media_cache_hit
                if _media_cache_hit(u, "zernio"):
                    new.append(u)
                    continue
                path = _local_media_path(led, post)
                if path is None or not path.is_file():
                    raise ValueError(
                        f"{post.platform.value} post {post.id} media URL is not cacheable for Zernio "
                        f"and no local file remains to re-upload")
                new.append(get_media_uploader(cfg, backend)(cfg, path, **_uploader_kwargs(backend, aid)))
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
                led2.set_post_state(post_id, PostState.queued)
    get_logger(cfg)("publish", post_id, "no_integration_id", account=post.account, platform=post.platform.value)


def _non_active_row(accounts: Accounts | None, handle: str) -> Account | None:
    """Row iff a registry entry exists and is not AccountStatus.active. None when accounts is None,
    the handle has no row, or the row is active — a missing row must not apply this guard
    (empty-registry / unknown-handle parity with channel_provider_if_ready)."""
    if accounts is None:
        return None
    try:
        want = validate_account_handle(handle)
    except ValueError:
        return None
    for a in accounts.accounts:
        if a.handle == want:
            return None if a.status is AccountStatus.active else a
    return None


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
        # F6-I / MOL-980: a row that exists and is not active must never enter submitting. Missing row
        # does NOT apply this guard (empty-registry / unknown-handle parity). accounts is None skips
        # the check (internal test callers that exercise the network path).
        row = _non_active_row(accounts, post.account)
        if row is not None:
            get_logger(cfg)("publish", post_id, "skip_account_not_active",
                            account=post.account, platform=post.platform.value, status=row.status.value)
            if _tally is not None:
                _tally["skipped_not_active"] = _tally.get("skipped_not_active", 0) + 1
            return None                                # leave it `queued` — planned/warming/retired do not ship
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
        # MOL-709: anchor the outbound ATTEMPT to a durable day, in the SAME txn as the claim, so a daily
        # volume ceiling can count in-flight posts (state alone carries no day). Re-stamped every claim —
        # see the field comment in models.py. NOT in _NET_POST_FIELDS: claim-determined, not network-
        # determined (same reason created_at is excluded — finalize must not rewrite it).
        post.submission_started_at = iso_z(datetime.now(timezone.utc))
        led.set_post_state(post_id, PostState.submitting)  # crash-safe intent, persisted on txn exit (F11/B4)
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
                    assert post.public_url, "GB-4: published post must have public_url"
                    post.published_at = iso_z(datetime.now(timezone.utc))   # TRUE publish time (Posted-archive day-anchor)
                    # Leg 3 (timing): bucket the true publish time into operator-local (hour, weekday) so
                    # timing_bias can rank reach-by-hour without every reader re-doing tz math. Single tz
                    # home (timeutil.publish_buckets); fail-safe (None,None) leaves the dim unranked.
                    post.publish_hour, post.publish_dow = _publish_buckets(post.published_at, cfg)
                    led.set_post_state(post_id, PostState.published)
                    post = led.posts[post_id]
                else:
                    led.set_post_state(post_id, PostState.needs_reconcile, error_reason=(
                        "publish_missing_url: backend returned submitted without a permalink — "
                        "reconcile will back-fill on next pass (R1/D2 gate)"))
                    post = led.posts[post_id]
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
                        led.set_post_state(post_id, PostState.needs_reconcile,
                                           error_reason="publish transient error (retries exhausted): " + red)
                        post = led.posts[post_id]
                    else:
                        # MOL-812: retry count lives on Post.daemon_transient_retry — error_reason is prose only.
                        led.set_post_state(post_id, PostState.failed, error_kind=ErrorKind.transient,
                                           error_reason="publish failed: " + red)
                        post = led.posts[post_id]
                else:
                    kind = ErrorKind.bad_payload if isinstance(exc, ValueError) else ErrorKind.unknown
                    led.set_post_state(post_id, PostState.failed, error_kind=kind, error_reason=(
                        "publish failed: " + redact(str(exc), cfg.postiz_api_key, cfg.zernio_api_key)))  # scrub any leaked key
                    post = led.posts[post_id]
            break
    if (_tally is not None and post.state is PostState.failed
            and getattr(post, "error_kind", None) is ErrorKind.rate_limit):
        _tally["rate_limited"] = 1
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
        # MOL-819: Post.state is Field(frozen=True) — merge via model_copy, never setattr(state).
        upd = {f: v for f, v in net.items()
               if not (f == "account_id" and v == getattr(p, f))}  # XC-5: don't rewrite an unchanged id
        if upd:
            led.posts[post_id] = p.model_copy(update=upd)
            p = led.posts[post_id]
        c = led.clips.get(p.parent_id)
        if c is not None and clip_media and _media_cache_hit(clip_media, backend):
            if not c.media_url or not _media_cache_hit(c.media_url, backend):
                c.media_url = clip_media                   # persist/replace once-per-clip upload (FIX F44)
        r = led.get_render(p.render_id) if p.render_id else None
        if r is not None and render_media and _media_cache_hit(render_media, backend):
            if not r.media_url or not _media_cache_hit(r.media_url, backend):
                r.media_url = render_media                 # CULM-2: persist/replace once-per-render upload
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
                led.set_post_state(post.id, PostState.failed, error_kind=ErrorKind.unknown,
                                   error_reason=f"bad schedule time {post.scheduled_time!r}: unparseable")
        return False
    return is_scheduled_due(post, cutoff)


def publish_due(cfg: Config, *, now: str | None = None, account: str | None = None, batch_id: str | None = None) -> dict:
    """Publish every DUE queued post, each via _publish_one (network OUTSIDE the ledger lock). Only
    'queued' is considered: a 'submitting' post stranded by a crash is NOT re-driven here (reconcile's
    job — auto-resubmitting could double-post a live post, FIX F11). A FATAL AuthError propagates
    (halt the queue, H8). Returns a small summary."""
    cutoff = _now(now)
    accounts = Accounts.load(cfg)                      # one load; per-post provider resolved off it (M3)
    _requeue_failed_posts(cfg)                         # MOL-125: bounded daemon retry for transient + 429 failed
    led = Ledger.load(cfg)                             # lock-free snapshot of the due queue
    due = [post for post in led.posts_in_state(PostState.queued) if _due_or_fail(cfg, post, cutoff)]
    if account:
        due = [p for p in due if p.account == account]
    if batch_id:
        due = [p for p in due if p.batch_id == batch_id]
    # A queued post whose clip OR parent moment is RETIRED must never ship: the pipeline dropped that lineage,
    # so an approval granted before the drop is stale by definition. The predicate is no longer hand-copied
    # here — the Ledger OWNS it (the call below) and every sibling reader asks that same owner, so a future
    # retirement rule lands in ONE place. One posture change rides along: a post whose CLIP ROW IS MISSING now
    # reads suppressed (the owner fails CLOSED) where the hand-copy's `c is not None` skipped it and shipped.
    # Filtered BEFORE ensure_up/quota so an all-retired queue neither starts Postiz nor reserves a daily slot.
    # Loud, never silent: logged per post under the SAME event name the approve-side guard uses, and counted in
    # the summary — event name and summary key both unchanged.
    stranded = {p.id: p for p in due if not led.can_promote(p)}
    for pid, p in stranded.items():
        get_logger(cfg)("publish", pid, "skipped_retired_lineage", account=p.account)
    due = [p for p in due if p.id not in stranded]
    # F6-I / MOL-980: park non-active handles BEFORE ensure_up so an all-dead due queue does not start
    # Postiz. Same predicate as the _publish_one claim (row exists AND status is not active). Missing
    # row is a no-op — empty registry / unknown handle keeps today's path.
    parked_not_active: dict[str, tuple[Post, Account]] = {}
    for p in due:
        row = _non_active_row(accounts, p.account)
        if row is not None:
            parked_not_active[p.id] = (p, row)
    by_acct: dict[str, tuple[Account, int]] = {}
    for _pid, (p, row) in parked_not_active.items():
        prev = by_acct.get(p.account)
        by_acct[p.account] = (row, (prev[1] if prev else 0) + 1)
    for handle, (row, n) in by_acct.items():
        get_logger(cfg)("publish", handle, "skip_account_not_active",
                        account=handle, status=row.status.value, n=n)
    due = [p for p in due if p.id not in parked_not_active]
    if due:                                            # on-demand: start the local Postiz stack ONLY when there is work
        from fanops.postiz_lifecycle import ensure_up
        ensure_up(cfg)
    log = get_logger(cfg)
    published = no_provider = no_integration_id = not_distributed = skipped_existing_id = not_live_ready = 0
    skipped_not_active = len(parked_not_active)
    tripped: set[tuple[str, str]] = set()
    for post in due:
        provider = _post_provider(cfg, accounts, post)
        if provider is None:                           # live but the channel has no provider -> skip, leave queued
            no_provider += 1
            log("publish", post.id, "no_provider", account=post.account, platform=post.platform.value)
            continue
        if provider == "dryrun":                       # dryrun-boundary (Finding #1): NOT live -> no real backend to
            not_distributed += 1                       # distribute to — preview sidecar only, post stays `queued`
            _handle_dryrun_boundary(cfg, post)
            continue
        acct_id = _resolve_publish_account_id(accounts, post, cfg=cfg)   # #10: cfg breadcrumbs a frozen-id fallback
        key = (provider, (acct_id or post.account_id or "").strip() or "_")
        if key in tripped:
            log("publish", post.id, "skip_rate_limited_circuit",
                account=post.account, platform=post.platform.value)
            continue
        tally: dict = {}
        if _publish_one(cfg, post.id, provider, accounts=accounts, account_id=acct_id,
                        _tally=tally, due_cutoff=cutoff) == PostState.published.value:
            published += 1
        if tally.get("rate_limited"):
            tripped.add(key)
        no_integration_id += tally.get("no_integration_id", 0)
        skipped_existing_id += tally.get("skip_resubmit_existing_id", 0)   # RC-1/S03: refused-at-claim, left queued
        not_live_ready += tally.get("not_live_ready", 0)                   # RC-3b/S07: cred-less channel, left queued
        skipped_not_active += tally.get("skipped_not_active", 0)           # F6-I: non-active row, left queued
    return {"due": len(due), "published": published, "no_provider": no_provider,
            "no_integration_id": no_integration_id, "not_distributed": not_distributed,
            "skipped_existing_id": skipped_existing_id, "not_live_ready": not_live_ready,
            "skipped_retired_lineage": len(stranded), "skipped_not_active": skipped_not_active}


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
    if provider == "dryrun":                           # dryrun-boundary (M2): NOT live -> preview only, stay `queued`
        _handle_dryrun_boundary(cfg, post, post_id=post_id)
        return PostState.queued.value
    return _publish_one(cfg, post_id, provider, accounts=accounts,   # RC-3b/S07: share the readiness gate
                        account_id=_resolve_publish_account_id(accounts, post, cfg=cfg))   # #10: cfg breadcrumbs a frozen-id fallback
