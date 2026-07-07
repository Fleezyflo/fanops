"""ledger-rebuild M4 (MOL-32/33): the fall-away of unbacked rows — the "wipe".

This is a SEPARATE, one-shot, operator-gated verb. It is NOT `_delete_moment_cascade` and does NOT touch
`Ledger._PROTECTED_POST_STATES` (the routine cascade guard is untouched — pinned byte-identical by test).

THE PREDICATE (PRD credential-scope section, authoritative; corrects Linear MOL-33's original wrong "19
clips/19 moments" concretization): remove ONLY rows whose ENTIRE descendant closure contains NO KEPT post.
A KEPT post is one that carries HISTORY — `analyzed` state OR non-empty metrics. The keep-guard keys on
POST STATE/history, NEVER on a live-match: `META_IG_USER_ID` is a single-handle credential while shipped
history spans multiple handles, so under a single-credential probe 6 of 7 real shipped posts look
"unbacked" — a live-match guard would wipe real history. The keep-set is TRANSITIVE: a kept post's ancestor
chain (its clip, that clip's moment, the source) and its renders are EXCLUDED from removal.

PER-ENTITY DISPOSITION (PRD "Full entity-graph disposition"):
  - posts     : removed if not kept
  - clips     : removed if NO kept post hangs off the clip
  - moments   : removed if NO kept post lives in the moment's clip closure
  - sources   : removed if NO kept post descends from the source (else it STAYS — the live 1 source stays)
  - renders   : follow their parent clip (removed iff the clip is removed)
  - stitch_plans : removed iff their clip is removed
  - batches   : removed iff NO kept row (source/post) references the batch
  - tag_log   : entries keyed "account|clip_id" — removed iff the clip is removed
  - variant_streaks : keyed "account|platform" AGGREGATES (no lineage id) — removed iff the account has NO
                      kept post at all (the account is fully wiped); an account that keeps any post keeps its
                      streak. This is the faithful closure reading for an id-less aggregate.

Snapshot-first (MOL-32): `execute_wipe` REFUSES (SnapshotRequired) unless handed a VERIFIED-restorable
snapshot path, and REFUSES (WipeNotConfirmed) without an explicit operator confirm. Both are enforced in
CODE, not just documentation. The one write is a single Ledger.transaction.

MACHINERY ONLY — nothing here runs against live 00_control automatically; the operator triggers it through
the Studio typed-confirm surface, later. fan-accounts-repost-freely: this removes UNBACKED cache, never
adds any supersede/dedupe; no new auto-publish path."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import PostState

logger = logging.getLogger(__name__)


class SnapshotRequired(Exception):
    """execute_wipe was called without a verified pre-wipe snapshot — refused (MOL-32 enforced in code)."""


class WipeNotConfirmed(Exception):
    """execute_wipe was called without the explicit operator confirm — refused."""


@dataclass
class WipePlan:
    """The would-remove id-set, per entity. A pure function of the ledger (compute_wipe_set) — no I/O."""
    post_ids: set = field(default_factory=set)
    moment_ids: set = field(default_factory=set)
    clip_ids: set = field(default_factory=set)
    source_ids: set = field(default_factory=set)
    render_ids: set = field(default_factory=set)
    stitch_plan_ids: set = field(default_factory=set)
    batch_ids: set = field(default_factory=set)
    tag_log_keys: set = field(default_factory=set)
    variant_streak_keys: set = field(default_factory=set)
    kept_post_ids: set = field(default_factory=set)     # the backed posts (reported; NEVER removed)


def _is_kept_post(post) -> bool:
    """A post carries HISTORY -> kept. Keys on POST STATE/metrics, NEVER on a live-match (credential-scope
    invariant): `analyzed` OR any non-empty metrics dict. An awaiting_approval/queued/rejected never-shipped
    row with no metrics is NOT kept (it is the unbacked cache that falls away)."""
    if post.state is PostState.analyzed:
        return True
    return bool(post.metrics)                            # a post that ever recorded metrics has real history


def compute_wipe_set(led: Ledger) -> WipePlan:
    """Derive the transitive-complement wipe set. Pure over the ledger (no I/O, no mutation). The keep-set
    is computed FIRST (kept posts + their ancestor chain + their renders); everything reachable-but-unkept
    falls into the plan. A source/clip/moment survives iff ANY kept post lives in its descendant closure."""
    plan = WipePlan()
    # 1) kept posts (POST-STATE/history guard) + the clips/moments/sources they anchor.
    kept_clips: set = set()
    kept_moments: set = set()
    kept_sources: set = set()
    for p in led.posts.values():
        if _is_kept_post(p):
            plan.kept_post_ids.add(p.id)
            kept_clips.add(p.parent_id)
            c = led.clips.get(p.parent_id)
            if c is not None:
                kept_moments.add(c.parent_id)
                m = led.moments.get(c.parent_id)
                if m is not None:
                    kept_sources.add(m.parent_id)
    # 2) posts: every non-kept post is removed.
    for p in led.posts.values():
        if p.id not in plan.kept_post_ids:
            plan.post_ids.add(p.id)
    # 3) clips: removed iff NOT a kept-post anchor. (A clip anchoring a kept post survives even if it also
    #    carries never-shipped sibling posts — those sibling posts are still individually removed above.)
    for c in led.clips.values():
        if c.id not in kept_clips:
            plan.clip_ids.add(c.id)
    # 4) moments: removed iff no kept clip descends from them.
    for m in led.moments.values():
        if m.id not in kept_moments:
            plan.moment_ids.add(m.id)
    # 5) sources: removed iff no kept post descends from them (else STAY — the live 1 source stays).
    for s in led.sources.values():
        if s.id not in kept_sources:
            plan.source_ids.add(s.id)
    # 6) renders follow their parent clip.
    for r in led.renders.values():
        if r.clip_id in plan.clip_ids:
            plan.render_ids.add(r.id)
    # 7) stitch_plans follow their clip.
    for st in led.stitch_plans.values():
        if st.clip_id in plan.clip_ids:
            plan.stitch_plan_ids.add(st.id)
    # 10) batches: removed iff NO kept row (a surviving source or a kept post) references the batch.
    kept_batches: set = set()
    for s in led.sources.values():
        if s.id not in plan.source_ids and getattr(s, "batch_id", None):
            kept_batches.add(s.batch_id)
    for pid in plan.kept_post_ids:
        b = getattr(led.posts[pid], "batch_id", None)
        if b:
            kept_batches.add(b)
    for b in led.batches.values():
        if b.id not in kept_batches:
            plan.batch_ids.add(b.id)
    # 11) tag_log entries keyed "account|clip_id" -> removed iff the clip is removed.
    for key in led.tag_log:
        cid = key.split("|", 1)[-1] if "|" in key else ""
        if cid in plan.clip_ids:
            plan.tag_log_keys.add(key)
    # 12) variant_streaks keyed "account|platform" aggregates -> removed iff the account has NO kept post.
    kept_accounts = {led.posts[pid].account for pid in plan.kept_post_ids}
    for key in led.variant_streaks:
        acct = key.split("|", 1)[0] if "|" in key else key
        if acct not in kept_accounts:
            plan.variant_streak_keys.add(key)
    return plan


def wipe_preview(led: Ledger) -> dict:
    """A READ-ONLY preview (the would-remove id-set + per-entity counts + kept-post count) for the Studio
    surface BEFORE the typed confirm. Pure — computes the plan, never writes."""
    plan = compute_wipe_set(led)
    counts = {"posts": len(plan.post_ids), "moments": len(plan.moment_ids), "clips": len(plan.clip_ids),
              "sources": len(plan.source_ids), "renders": len(plan.render_ids),
              "stitch_plans": len(plan.stitch_plan_ids), "batches": len(plan.batch_ids),
              "tag_log": len(plan.tag_log_keys), "variant_streaks": len(plan.variant_streak_keys)}
    detail = {"counts": counts, "post_ids": sorted(plan.post_ids), "kept_posts": len(plan.kept_post_ids),
              "total": sum(counts.values())}
    detail["token"] = preview_token(detail)
    return detail


def preview_token(preview_detail: dict) -> str:
    """A deterministic fingerprint of a would-remove set (MOL-71). The Studio confirm carries the token from
    the preview it showed; confirm_wipe recomputes a FRESH preview and refuses unless the tokens match — so a
    confirm that never previewed (no token) or previewed a since-changed ledger (stale token) is server-refused
    BEFORE any snapshot/removal, without weakening the typed-word/snapshot code gates. Pure over the id-set +
    counts + total (a ledger change flips the fingerprint); no secret/session infra needed."""
    import hashlib, json
    payload = json.dumps({"post_ids": preview_detail.get("post_ids", []),
                          "counts": preview_detail.get("counts", {}),
                          "total": preview_detail.get("total", 0)}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def snapshot_is_restorable(snapshot_path: "Path | str") -> bool:
    """Verify a snapshot file is a LOADABLE ledger image (MOL-32 'verified restorable'): it parses as the
    on-disk ledger doc. A corrupt / non-JSON / non-ledger file returns False (the wipe must not proceed on
    an unrestorable snapshot). Read-only, never raises."""
    import json
    try:
        src = Path(snapshot_path)
        if not src.exists():
            return False
        doc = json.loads(src.read_text())
        # the ledger doc is a dict carrying the id->unit maps; a valid image has at least the posts map key.
        return isinstance(doc, dict) and "posts" in doc
    except Exception:
        logger.warning("snapshot restorability check failed (fail-open, treated as unrestorable)", exc_info=True)
        return False


def execute_wipe(cfg: Config, *, confirmed: bool, snapshot_path: "Optional[Path | str]") -> dict:
    """Run the fall-away. GATED, in code:
      - WipeNotConfirmed unless `confirmed` (the explicit operator confirm — mirrors the Go-Live gate).
      - SnapshotRequired unless `snapshot_path` is a VERIFIED-restorable snapshot (MOL-32: cannot run
        without the snapshot succeeding first). The Studio surface takes the snapshot, verifies it, then
        passes it here — so a skip is impossible.
    Removes EXACTLY compute_wipe_set(led) in a single transaction; returns the removed-count summary.
    Reversible: Ledger.restore_snapshot(cfg, snapshot_path) brings every removed row back."""
    if not confirmed:
        raise WipeNotConfirmed("the wipe requires an explicit operator confirm")
    if not snapshot_path or not snapshot_is_restorable(snapshot_path):
        raise SnapshotRequired("a verified-restorable pre-wipe snapshot is mandatory before the wipe runs")
    removed = {}
    with Ledger.transaction(cfg) as led:
        plan = compute_wipe_set(led)
        for pid in plan.post_ids: led.posts.pop(pid, None)
        for cid in plan.clip_ids: led.clips.pop(cid, None)
        for mid in plan.moment_ids: led.moments.pop(mid, None)
        for sid in plan.source_ids: led.sources.pop(sid, None)
        for rid in plan.render_ids: led.renders.pop(rid, None)
        for stid in plan.stitch_plan_ids: led.stitch_plans.pop(stid, None)
        for bid in plan.batch_ids: led.batches.pop(bid, None)
        for k in plan.tag_log_keys: led.tag_log.pop(k, None)
        for k in plan.variant_streak_keys: led.variant_streaks.pop(k, None)
        removed = {"posts": len(plan.post_ids), "moments": len(plan.moment_ids), "clips": len(plan.clip_ids),
                   "sources": len(plan.source_ids), "renders": len(plan.render_ids),
                   "stitch_plans": len(plan.stitch_plan_ids), "batches": len(plan.batch_ids),
                   "tag_log": len(plan.tag_log_keys), "variant_streaks": len(plan.variant_streak_keys)}
    return {"removed": removed, "snapshot": str(snapshot_path)}
