# src/fanops/casting.py — Account-First Studio: per-account moment casting (Face 3).
# A PURE assignment layer over the already-decided moment pool: NO LLM, NO ffmpeg, NO per-account author
# re-run (moments are SOURCE-keyed, so the base render stays shared — per-account differentiation is the
# existing cheap hook overlay). It scores each decided moment per active account by persona fit, assigns
# each account up to cfg.cast_pick_budget of its best-fitting moments BOUNDED by that moment's batch
# target, and stamps Moment.affinities. crosspost then fans a cast moment ONLY to its accounts.
# C1-safe: reads persona + signal_score, writes ONLY affinities — never touches amplify/retire/cascade/track.
from __future__ import annotations
from fanops.models import MomentState
from fanops.variant_transfer import _persona_tokens
from fanops.log import get_logger


def persona_fit_score(persona, moment) -> tuple:
    """Deterministic, totally-ordered fit key (higher = better): (overlap_count, signal_score, id).
    overlap = persona tokens ∩ the moment corpus (reason + hook + transcript_excerpt). A zero-overlap
    persona still orders by signal_score, so no account is ever zero-cast while moments exist. Pure."""
    corpus = f"{moment.reason} {moment.hook or ''} {moment.transcript_excerpt}".lower().split()
    overlap = len(_persona_tokens(persona) & set(corpus))
    return (overlap, moment.signal_score, moment.id)


def cast_moments(led, cfg, accounts, *, account_target=None):
    """Assign per-account affinities over the decided, uncast moment pool; returns `led`. Each active
    account is allotted up to cfg.cast_pick_budget of its best persona-fit moments, bounded PER MOMENT by
    that moment's batch target (Source.batch_id -> Batch.target_accounts; empty/missing -> all active), so
    affinities ⊆ the batch target (it can only NARROW). account_target overrides the per-moment resolution
    for standalone callers (None -> resolve per moment). Idempotent (only affinities==[] moments are
    considered) + fail-open (returns led unchanged, logged once, on any internal error). NON-DURABLE across
    a moment re-decision: reconcile_moments rebuilds the Moment -> affinities reset to []; re-derived each
    gated pass. The caller holds the transaction (pipeline.advance), mirroring crosspost_clips."""
    try:
        active = list(accounts.active())
        active_handles = {a.handle for a in active}
        pool = [m for m in led.moments.values() if m.state is MomentState.decided and not m.affinities]
        budget = cfg.cast_pick_budget
        def allowed(m):
            if account_target is not None: return set(account_target) & active_handles
            src = led.sources.get(m.parent_id)
            bid = getattr(src, "batch_id", None) if src is not None else None
            b = led.get_batch(bid) if bid else None
            bt = b.target_accounts if b is not None else None
            return (set(bt) if bt else active_handles) & active_handles   # [] target == ALL active
        assign = {}
        for a in active:
            eligible = [m for m in pool if a.handle in allowed(m)]
            eligible.sort(key=lambda m: persona_fit_score(a.persona, m), reverse=True)
            for m in eligible[:budget]:
                assign.setdefault(m.id, set()).add(a.handle)
        for mid, handles in assign.items():
            led.moments[mid].affinities = sorted(handles)
        return led
    except Exception as e:
        try: get_logger(cfg)("casting", "-", "error", err=str(e)[:120])
        except Exception: pass
        return led
