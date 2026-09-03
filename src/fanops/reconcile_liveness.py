"""IG/TikTok liveness gates for reconcile publish promotion (MOL-117, REST-gate quarantine).

Pure helpers and pre-poll enrichment: confirm a backend `published` observation before reconcile_posts
rests a post in a terminal-positive state. Network I/O is allowed only in enrichment (lock-free poll path)
or via injectable getters in the verdict helpers (tests never touch the network)."""
from __future__ import annotations

from typing import Optional

from fanops.config import Config
from fanops.log import get_logger
from fanops.models import is_real_submission_id
from fanops.text import safe_public_url


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


def _ig_rest_verdict(cfg: Config, post, media_id, credentialed_handles, confirm, graph_get,
                     ig_usernames: Optional[dict] = None) -> str:
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
        username == the intended handle (or this account's Graph username). A DEFINITIVE non-confirmation
        (object absent) or an owner MISMATCH -> _GATE_PARK (never rest on Postiz's word). A TRANSPORT
        failure during the confirm (the injected probe saw the getter raise) OR a Graph username lookup
        hiccup after a token is present -> _GATE_FAILOPEN: a network miss is NOT a verdict, so don't
        strand the post — retry next tick. Mirrors TikTok's posture: fail-closed on a real identity
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
    cache = ig_usernames if ig_usernames is not None else {}
    key = handle.lstrip("@").lower()
    if key not in cache:
        cache[key] = _ig_username_for_handle(cfg, handle, graph_get)
    name, kind = cache[key] if isinstance(cache.get(key), tuple) else (cache.get(key), "no_creds")
    if res.get("confirmed") and _owner_matches(res.get("owner"), handle, name if kind == "ok" else None):
        return _GATE_REST                                    # platform-confirmed AND owned by this account's IG user
    if probe["transport_failed"] or kind == "transport":
        return _GATE_FAILOPEN                                # confirmed=False rode a transport hiccup -> fail OPEN
    return _GATE_PARK                                        # DEFINITIVE: object absent or owner mismatch -> fail CLOSED


def _owner_matches(owner, handle, *aliases) -> bool:
    """The Graph-reported owner username == the intended IG identity.

    Compared case-insensitively and '@'-insensitively against the FanOps handle AND any extra aliases
    (the Graph username of this account's `ig_user_id`). The handle is an internal alias and is often
    not the IG username; matching only the handle parks every live reel on those accounts forever."""
    if not owner:
        return False
    got = owner.strip().lstrip("@").lower()
    names = (handle,) + aliases
    return any(got == (n or "").strip().lstrip("@").lower() for n in names if n)


def _ig_username_for_handle(cfg: Config, handle: str, graph_get) -> tuple[Optional[str], str]:
    """Graph username of this FanOps handle's own ig_user_id, as `(name, kind)`.

    kind is `"ok"` | `"no_creds"` | `"transport"`. No handle / registry error / no ig_user_id / no
    token -> `(None, "no_creds")` so the caller matches the handle only (existing tests without
    META_GRAPH_TOKEN stay handle-only and never hit the network). Token present but Graph miss
    (non-dict body / empty username) -> `(None, "transport")` — a hiccup, not "this handle has no
    alias." Do not cache a bare None."""
    h = (handle or "").strip().lstrip("@").lower()
    if not h:
        return None, "no_creds"
    from fanops.accounts import load_accounts_safe
    from fanops.meta_graph import _graph_get, resolve_meta_creds
    accts, err = load_accounts_safe(cfg)
    if err:
        return None, "no_creds"
    row = next((a for a in accts.accounts if (a.handle or "").lstrip("@").lower() == h), None)
    uid = (getattr(row, "ig_user_id", None) or "").strip() if row is not None else ""
    if not uid:
        return None, "no_creds"
    creds = resolve_meta_creds(cfg, handle=row.handle)
    if not creds.token:
        return None, "no_creds"
    body = _graph_get(cfg, uid, {"fields": "username"}, get=graph_get, token=creds.token)
    if not isinstance(body, dict):
        return None, "transport"
    name = body.get("username")
    if isinstance(name, str) and name.strip():
        return name.strip(), "ok"
    return None, "transport"


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


def _enrich_poll_liveness(cfg: Config, post, info: dict, *, cred_ig, confirm, graph_get,
                          ig_usernames: Optional[dict] = None) -> None:
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
        liv["ig_verdict"] = _ig_rest_verdict(cfg, post, _rid, cred_ig, confirm, graph_get,
                                              ig_usernames=ig_usernames)
    info["liveness"] = liv
