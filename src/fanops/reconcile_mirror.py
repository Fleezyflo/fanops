"""Postiz mirror projection and reconcile read-phase routing (bulk window + Zernio poll keys).

Splits the network READ shape from reconcile_posts' in-lock APPLY: one Postiz list_all window
projected onto mirrored posts, Zernio per-post status pollers keyed by submission_id routing, and
the (_reconcile_reads) partition into mirrored / token_only / polled surfaces."""
from __future__ import annotations

import re
from typing import Callable, Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.log import get_logger
from fanops.models import PostState, is_real_submission_id

# Postiz cuid2 (this deployment): leftover on a Zernio channel. GET /posts/{id} 400s Invalid post ID format.
_POSTIZ_CUID = re.compile(r"^c[a-z0-9]{24}$")

# States whose true outcome is unknown and pollable: a publish was (or may have been) sent.
_RECONCILABLE = (PostState.submitting, PostState.submitted, PostState.needs_reconcile)
# States a post RESTS in once its publication is settled. The Postiz mirror keeps observing them for life —
# not to re-decide them (nothing here may move a resting post; see reconcile module header) but because the row is
# the only place a later platform-side change is visible at all, and a mirror that stops looking the moment a
# post succeeds can only ever report the moment of success.
_MIRROR_RESTING = (PostState.published, PostState.analyzed)
# Posts that already have a real vendor id but are NOT pending and NOT resting. Observation is still owed:
# `failed` was written from an ERROR row, then Postiz moved (QUEUE / PUBLISHED / deleted) and Studio kept
# showing the stamp because this set used to be invisible. `queued` with a real sid is skip_resubmit — the
# daemon will not POST again — so when Postiz later PUBLISHES, only the mirror can promote it.
# Do NOT dump these into _RECONCILABLE: QUEUE maps to status `scheduled`, and the pending else-branch
# leaves the row unchanged (a failed+QUEUE post would stay failed). Promote-out rules live in
# reconcile_posts.
_MIRROR_HELD = (PostState.failed, PostState.error, PostState.queued)
# The Post.postiz_state sentinel (MOL-784 vocabulary) for "the mirrored window held NO row for this post's
# submission id". Written ONLY for a post whose id is a real backend id — an id that COULD have matched.
_MIRROR_ABSENT = "absent"
# The one RAW Postiz token (same MOL-784 vocabulary, kept verbatim off the row) that means the backend
# considers the row DONE. Compared case-folded because the mirror deliberately never normalises what it
# stores. It lives HERE, beside the sentinel, because it is the SAME vocabulary and a second literal in a
# second module is the copied-number defect class — `pending_lateness` below and the digest's mirror-drift
# section (digest._postiz_drift) both read it from reconcile_mirror (re-exported by reconcile).
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
    elif row["status"] == "failed":
        from fanops.post.metrics import poster_fail_reason
        raw_row = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        msg = poster_fail_reason(row.get("errorMessage"), row.get("error"),
                                 raw_row.get("errorMessage"), raw_row.get("error"))
        if msg:
            out["errorMessage"] = msg
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


def _reconcile_reads(cfg: Config, snapshot: Ledger, log) -> tuple[list, list, list]:
    """Split the reconcile surface by RESOLVED BACKEND — the read SHAPES are not interchangeable, so the
    split is the first thing the pass decides. Returns (mirrored, token_only, polled).

    Fetch = mirrored / polled. Network. Visit = token_only + local apply. No network. Age ladder
    and husk reject run even when fetch is empty. Unpollable is not invisible: a Postiz cuid on a
    Zernio channel is the same miss as `fanops_` — do not GET it; still visit it.

      mirrored   — Postiz-backed, a REAL submission id, pending OR resting. One bulk window answers all
                   of them; a resting post is here so its row keeps being observed after it succeeds.
      token_only — unpollable, still visited. A `fanops_` client token (Postiz OR Zernio), OR a leftover
                   Postiz cuid on a Zernio channel. NO backend row can ever carry that id, so there is
                   nothing to mirror and nothing to poll — but the post is still VISITED, because the
                   (state, age) escalation is what un-strands it.
      polled     — Zernio-backed, pending, and a REAL submission id that is not a leftover Postiz cuid:
                   the per-post GET /posts/{id}. Zernio is NOT mirrored, so a Zernio-backed resting
                   post is out of the surface entirely and is never written an `absent` it was never
                   asked about.

    A post whose channel resolves to no live provider is skipped; that is logged for a pending post
    (it is work not done) and silent for a resting one (there is nothing it was owed)."""
    routing = _reconcilable_routing(
        cfg, snapshot, states=_RECONCILABLE + _MIRROR_RESTING + _MIRROR_HELD)
    mirrored, token_only, polled = [], [], []
    for p in snapshot.posts.values():
        resting = p.state in _MIRROR_RESTING
        held = p.state in _MIRROR_HELD and is_real_submission_id(p.submission_id)
        if not (resting or held or p.state in _RECONCILABLE) or not p.submission_id:
            continue
        try:
            backend = _poll_backend_for_sid(cfg, routing, p.submission_id)
        except RuntimeError:
            if not resting:
                log("reconcile", p.id, "skipped: no live provider")
            continue
        if backend != "postiz":
            if held:
                continue                                 # held observation is Postiz-mirror only (no remint path)
            if not resting:
                if is_real_submission_id(p.submission_id):
                    if _POSTIZ_CUID.match(p.submission_id):
                        log("reconcile", p.id, "skipped: postiz id on zernio channel")
                        token_only.append(p)   # unpollable, still visited
                        continue
                    polled.append(p)                     # Zernio: per-post GET of a real backend id
                else:
                    token_only.append(p)                 # fanops_ birth token is not a GET key (I4)
        elif is_real_submission_id(p.submission_id):
            mirrored.append(p)
        elif not resting and not held:
            token_only.append(p)                         # a client token names no row -> age ladder only
    return mirrored, token_only, polled
