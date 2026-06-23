# src/fanops/casting.py — Account-First Studio: per-account moment casting (Face 3).
# A PURE assignment layer over the already-decided moment pool: NO LLM, NO ffmpeg, NO per-account author
# re-run (moments are SOURCE-keyed, so the base render stays shared — per-account differentiation is the
# existing cheap hook overlay). It scores each decided moment per active account by persona fit, assigns
# each account up to cfg.cast_pick_budget of its best-fitting moments BOUNDED by that moment's batch
# target, and stamps Moment.affinities. crosspost then fans a cast moment ONLY to its accounts.
# C1-safe: reads persona + signal_score, writes ONLY affinities — never touches amplify/retire/cascade/track.
from __future__ import annotations
from datetime import datetime, timezone
from fanops.models import MomentState, MomentCastingRequest, MomentCastingDecision, SelectionFact, SelectionMethod
from fanops.variant_transfer import _persona_tokens
from fanops.personas import casting_directive
from fanops.agentstep import write_request, read_response, latest_request_id
from fanops.control import load_guidance
from fanops.ids import child_id
from fanops.timeutil import iso_z
from fanops.log import get_logger


def _record_fact(led, m, handle, *, method, overlap=None, signal=None, rank=None) -> None:
    """M4: persist the DURABLE selection fact for (moment, account) — the audit trail of WHO got WHAT and WHY.
    BEST-EFFORT (own try/except): a fact-write error must NEVER lose the casting (affinities are already set);
    the fact is the record, not the decision. Content-addressed one-per-(moment, account) -> a re-cast overwrites."""
    try:
        src = led.sources.get(m.parent_id)
        led.add_selection_fact(SelectionFact(
            id=child_id("selfact", m.id, handle), moment_id=m.id, account=handle, method=method,
            reason=(m.reason or ""), overlap=overlap, signal=signal, rank=rank,
            source_id=m.parent_id, batch_id=getattr(src, "batch_id", None),
            created_at=iso_z(datetime.now(timezone.utc))))
    except Exception:
        pass


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
            # score each eligible moment ONCE (persona_fit_score = (overlap, signal, id); total order, no ties),
            # sort desc, take the budget. score[0] is reused as the fact's `overlap` (no double-compute).
            scored = sorted(((persona_fit_score(a.persona, m), m) for m in pool if a.handle in allowed(m)),
                            key=lambda t: t[0], reverse=True)
            for rank, (score, m) in enumerate(scored[:budget]):
                assign.setdefault(m.id, set()).add(a.handle)
                _record_fact(led, m, a.handle, method=SelectionMethod.heuristic,
                             overlap=score[0], signal=m.signal_score, rank=rank)   # M4: the durable why + pick rank
        for mid, handles in assign.items():
            led.moments[mid].affinities = sorted(handles)
        return led
    except Exception as e:
        try: get_logger(cfg)("casting", "-", "error", err=str(e)[:120])
        except Exception: pass
        return led


# ---- M1 (Option C): LLM-driven per-account moment SELECTION (the generous, persona-smart selector) ----
# cast_moments above is the deterministic token-overlap HEURISTIC (the no-LLM fallback / manual mode). The
# gate below is the default selector when account_casting is ON with a responder: it sees each persona and
# the whole decided pool and assigns each account its OWN moments — genuinely different per account, GENEROUS
# (no count cap), reusing Moment.affinities + the existing crosspost gate. Request/respond/ingest mirrors the
# moments gate; the deterministic ingest is fully testable with a mocked decision (no live LLM).

def request_moment_casting(led, cfg, source_id, accounts):
    """Open ONE per-account moment-SELECTION gate for this source. Carries the source's DECIDED moments
    (reason + hook + excerpt + signal + window) and each active persona; the agent returns, per account,
    that account's OWN set of moments. Write-ONCE. Skipped when there is nothing to differentiate (no decided
    moments, OR no persona-bearing active account -> heuristic territory / nothing to select). Returns led."""
    src = led.sources.get(source_id)
    if src is None: return led
    decided = sorted([m for m in led.moments.values()
                      if m.parent_id == source_id and m.state is MomentState.decided],
                     key=lambda m: (m.start, m.end))
    personas = [{"handle": a.handle, "persona": instr, "clip_count": a.clip_count}   # the CASTING directive + per-account clip ceiling
                for a in accounts.active() if (instr := casting_directive(a))]
    if not decided or not personas: return led        # nothing to cast / no persona to differentiate -> no gate
    if latest_request_id(cfg, "moment_casting", source_id) is not None:
        return led                                    # write-ONCE: never re-stamp an in-flight gate
    moments = [{"moment_id": m.id, "reason": m.reason, "hook": m.hook or "",
                "transcript_excerpt": m.transcript_excerpt, "signal_score": m.signal_score,
                "start": m.start, "end": m.end} for m in decided]
    payload = MomentCastingRequest(source_id=source_id, request_id="", moments=moments, personas=personas,
                                   language=src.language, guidance=load_guidance(cfg)).model_dump()
    payload.pop("request_id", None)
    write_request(cfg, kind="moment_casting", key=source_id, payload=payload)
    return led


def ingest_moment_casting(led, cfg, source_id, accounts):
    """Apply the per-account selection to Moment.affinities (the crosspost gate honors it). AUTHORITATIVE +
    GENEROUS: each selected (account, moment) appends the handle to that DECIDED moment's affinities (sorted
    union, deduped) — NO count cap, overlap allowed. Skips unknown moment ids, moments of another source,
    non-decided moments, and inactive handles. No response yet -> no-op (pending). Fail-open. Returns led."""
    try:
        dec = read_response(cfg, "moment_casting", source_id, MomentCastingDecision)
        if dec is None: return led                    # still pending -> leave affinities as-is
        active = {a.handle for a in accounts.active()}
        add: dict = {}
        for handle, mids in (dec.selections or {}).items():
            if handle not in active: continue          # an inactive/unknown handle never casts
            for mid in mids:
                m = led.moments.get(mid)
                if m is None or m.parent_id != source_id or m.state is not MomentState.decided:
                    continue                           # foreign / unknown / not-yet-decided -> skip
                add.setdefault(mid, set()).add(handle)
        for mid, handles in add.items():
            led.moments[mid].affinities = sorted(set(led.moments[mid].affinities) | handles)
            for handle in handles:                        # M4: durable fact per LLM-selected (account, moment) —
                _record_fact(led, led.moments[mid], handle, method=SelectionMethod.llm)   # no heuristic score/rank
        return led
    except Exception as e:
        try: get_logger(cfg)("casting", source_id, "error", err=str(e)[:120])
        except Exception: pass
        return led


# ---- M5: caption scoping. The AFFINITY gate as ONE shared predicate so crosspost (the enforcement gate)
# and the caption-request scoper can never drift (the H1 lesson). Both pure, no I/O. ----
def affinity_admits(cfg, moment, account) -> bool:
    """Admit `account` for `moment` under the affinity rule. True when casting is OFF (flag-OFF IGNORES
    persisted affinities — invariant A2), OR the moment is uncast (affinities==[] -> fan to all), OR the
    account is in the cast set. Equivalent to the negation of the crosspost affinity gate."""
    if not cfg.account_casting: return True
    if moment is None or not moment.affinities: return True
    return account in moment.affinities

def scoped_caption_surfaces(cfg, moment, surfaces):
    """M5: the surfaces a clip's captions are REQUESTED for — the affinity-admitted subset. Returns the full
    list unchanged when casting is OFF or the moment is uncast (byte-identical / fan-to-all). Within a
    decision cycle this is a SUPERSET of the crosspost survivors (which narrow further by batch target), so
    every minted post has a caption; a post-captioning re-cast SWAP is backstopped by crosspost's cap-is-None
    skip. `surfaces` is an iterable of Surface; returns the (account, platform) tuples request_captions wants."""
    return [(s.account, s.platform) for s in surfaces if affinity_admits(cfg, moment, s.account)]
