# src/fanops/casting.py — Account-First Studio: per-account moment casting (Face 3).
# Two selectors over the already-decided moment pool, both writing ONLY Moment.affinities (crosspost then
# fans a cast moment ONLY to its accounts): the default-ON **LLM gate** (`request_moment_casting`/
# `ingest_moment_casting`, wired into the pipeline — an LLM SELECTION, GENEROUS, no count cap), and the
# retained-but-unwired token-overlap **heuristic** (`cast_moments` — a pure persona-fit scorer that assigns
# each account up to a `budget` (default 6) of its best-fitting moments BOUNDED by that moment's batch target).
# Neither does ffmpeg or a per-account author re-run (moments are SOURCE-keyed, so the base render stays
# shared — per-account differentiation is the existing cheap hook overlay).
# C1-safe: reads persona + signal_score, writes ONLY affinities — never touches amplify/retire/cascade/track.
from __future__ import annotations
import contextlib
from datetime import datetime, timezone
from fanops.models import (MomentState, MomentCastingRequest, MomentCastingDecision, SelectionFact,
                           SelectionMethod, AccountSelection, account_selection_id)
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


def cast_moments(led, cfg, accounts, *, account_target=None, budget: int = 6):
    """Assign per-account affinities over the decided, uncast moment pool; returns `led`. The token-overlap
    HEURISTIC (no-LLM fallback / manual mode — UNWIRED from the pipeline). Each active account is allotted up
    to `budget` of its best persona-fit moments (default 6; this is the heuristic's OWN cap, not a global
    config knob — the wired LLM path is uncapped by design), bounded PER MOMENT by that moment's batch target
    (Source.batch_id -> Batch.target_accounts; empty/missing -> all active), so affinities ⊆ the batch target
    (it can only NARROW). account_target overrides the per-moment resolution for standalone callers (None ->
    resolve per moment). Idempotent (only affinities==[] moments are considered) + fail-open (returns led
    unchanged, logged once, on any internal error). NON-DURABLE across a moment re-decision."""
    try:
        active = list(accounts.active())
        active_handles = {a.handle for a in active}
        pool = [m for m in led.moments.values() if m.state is MomentState.decided and not m.affinities]
        budget = max(1, int(budget))
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
    # P1: decided OR clipped-and-uncast — a stranded source whose moments raced decided->clipped before the
    # casting answer landed gets ONE re-open here (write-once guard below makes it idempotent), so the gate is
    # not permanently missed. affinities==[] keeps it to genuinely-uncast moments; a cast source never re-opens.
    pool = sorted([m for m in led.moments.values()
                   if m.parent_id == source_id and m.state in (MomentState.decided, MomentState.clipped)],
                  key=lambda m: (m.start, m.end))
    personas = [{"handle": a.handle, "persona": instr}   # the CASTING directive per active account
                for a in accounts.active() if (instr := casting_directive(a))]
    if not pool or not personas: return led           # nothing to cast / no persona to differentiate -> no gate
    if latest_request_id(cfg, "moment_casting", source_id) is not None:
        return led                                    # write-ONCE: never re-stamp an in-flight gate
    moments = [{"moment_id": m.id, "reason": m.reason, "hook": m.hook or "",
                "transcript_excerpt": m.transcript_excerpt, "signal_score": m.signal_score,
                "start": m.start, "end": m.end} for m in pool]
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
                if m is None or m.parent_id != source_id or m.state not in (MomentState.decided, MomentState.clipped):
                    continue                           # foreign / unknown / not-yet-decided -> skip. P1: `clipped` is
                                                       # ACCEPTED (a late answer still applies; affinity_admits reads
                                                       # affinities, not state). `retired`/`error` are excluded by
                                                       # enumeration AND independently dropped from crosspost's seed list.
                add.setdefault(mid, set()).add(handle)
        for mid, handles in add.items():
            led.moments[mid].affinities = sorted(set(led.moments[mid].affinities) | handles)
            for handle in handles:                        # M4: durable fact per LLM-selected (account, moment) —
                _record_fact(led, led.moments[mid], handle, method=SelectionMethod.llm)   # no heuristic score/rank
        # RF1: write a DURABLE, account-owned AccountSelection for each PICKED account — the un-collapsible,
        # always-visible crosspost-gate input (Task 3 reads it, replacing the non-durable affinities tag). An
        # account the selector OMITTED gets NO selection: the gate's "cast source, no record -> DENY" branch
        # excludes it (true per-account differentiation; this is the no-fan-to-all-leak contract). fan_all_default
        # is NEVER auto-written here — it is an operator override (Task 6) or a migration label, not a casting
        # fallback (auto-fanning an unpicked account to all would resurrect the silent collapse RF1 closes).
        src = led.sources.get(source_id)
        bid = getattr(src, "batch_id", None)
        now = iso_z(datetime.now(timezone.utc))
        per_account: dict = {}
        for mid, handles in add.items():
            for h in handles: per_account.setdefault(h, []).append(mid)
        for h, mids in per_account.items():
            led.add_account_selection(AccountSelection(
                id=account_selection_id(source_id, h), source_id=source_id, account=h,
                moment_ids=sorted(mids), method=SelectionMethod.llm, batch_id=bid, created_at=now))
        # WS1 (audit c5-f1/xc-1): a persona-LESS active account is NEVER in the brief (request_moment_casting
        # filters on a truthy casting_directive), so the selector cannot place it; on a CAST source it would hit
        # account_selection_admits' "no record -> DENY" branch and silently post NOTHING. Give each never-
        # candidate active account an EXPLICIT fan_all_default selection so it ships fan-to-all via the LABELLED
        # gate branch (casting.py account_selection_admits) — VISIBLE, not a silent admit, so RF1's no-collapse
        # contract holds: an in-brief-but-unpicked account still has NO record and still DENIES (true
        # differentiation). Only when the source actually became cast (per_account non-empty); the picked-no-one
        # case is left to the existing fan-to-all fallback + degraded_reason below.
        if per_account:
            candidates = {a.handle for a in accounts.active() if casting_directive(a)}
            for a in accounts.active():
                if a.handle in candidates or a.handle in per_account: continue
                if led.account_selection_for(source_id, a.handle) is not None: continue
                led.add_account_selection(AccountSelection(
                    id=account_selection_id(source_id, a.handle), source_id=source_id, account=a.handle,
                    moment_ids=[], method=SelectionMethod.fan_all_default, batch_id=bid, created_at=now))
                with contextlib.suppress(Exception):
                    get_logger(cfg)("casting", source_id, "fan_all_default", account=a.handle)
        if not per_account and src is not None and active:   # casting ran but picked NO ONE -> visible, never silent
            led.sources[source_id] = src.model_copy(
                update={"degraded_reason": "casting produced no selections (source falls back to fan-to-all)"})
        return led
    except Exception as e:
        with contextlib.suppress(Exception): get_logger(cfg)("casting", source_id, "error", err=str(e)[:120])
        src = led.sources.get(source_id)          # RF1: route the fail-open through the VISIBLE degradation channel
        if src is not None:                        # (Source.degraded_reason) — fail-open preserved, but never silent
            led.sources[source_id] = src.model_copy(update={"degraded_reason": f"casting failed: {str(e)[:120]}"})
        return led


def casting_gate_pending(cfg, source_id) -> bool:
    """P1: True iff casting is ON and this source's moment_casting gate is OPEN but UNANSWERED — the crosspost
    fan-out must WAIT (else a post is minted fan-to-all BEFORE affinities land, and posts never un-mint). A
    source with no gate (no personas / casting OFF / nothing to cast) returns False -> fan out now. Fail-open
    to False (a probe glitch must never permanently strand a clip). Mirrors how the caption gate blocks
    crosspost: a clip is fan-out-eligible only once its prerequisite gate has converged."""
    try:
        if not cfg.account_casting: return False
        if latest_request_id(cfg, "moment_casting", source_id) is None: return False   # no gate -> nothing to wait for
        return read_response(cfg, "moment_casting", source_id, MomentCastingDecision) is None
    except Exception as e:
        with contextlib.suppress(Exception): get_logger(cfg)("casting", source_id, "gate_probe_error", err=str(e)[:120])  # fail-open, but leave a trace
        return False


def casting_gate_failed_to_open(cfg, led, accounts, source_id) -> bool:
    """WS1 (audit xc-2): True iff casting is ON and this source SHOULD have opened a casting gate this pass —
    it has CANDIDATE accounts (a truthy casting_directive, so the brief would list them) AND castable
    (decided/clipped) moments — but NONE opened AND no selections were written. That is the fingerprint of a
    request_moment_casting I/O failure: without this, account_selection_admits falls back to the legacy 'no
    selections -> fan-to-all' path and a transient disk error silently downgrades a differentiated source to
    undifferentiated fan-out for the pass. Crosspost must DEFER (retry next pass), mirroring casting_gate_pending.
    Distinct from the LEGIT no-gate case (no candidate accounts -> nothing to differentiate -> fan-to-all is
    correct), which returns False. Fail-open to False (never permanently strand a clip on a probe glitch)."""
    try:
        if not cfg.account_casting: return False
        if latest_request_id(cfg, "moment_casting", source_id) is not None: return False  # gate exists -> casting_gate_pending owns it
        if led.selections_of_source(source_id): return False                              # casting ran fine -> selections written
        if not any(casting_directive(a) for a in accounts.active()): return False          # no candidate -> legit no-gate -> fan-to-all
        castable = [m for m in led.moments.values()
                    if m.parent_id == source_id and m.state in (MomentState.decided, MomentState.clipped)]
        if not castable: return False                                                      # nothing to cast -> no gate expected
        if any(m.affinities for m in castable): return False                              # legacy/heuristic affinities cast (no gate by design) -> not a failure
        return True                                                                        # candidates + castable + no gate + no selections + no affinities -> request failed -> defer
    except Exception as e:
        with contextlib.suppress(Exception): get_logger(cfg)("casting", source_id, "gate_probe_error", err=str(e)[:120])
        return False


# ---- M5: caption scoping. The AFFINITY gate as ONE shared predicate so crosspost (the enforcement gate)
# and the caption-request scoper can never drift (the H1 lesson). Both pure, no I/O. ----
def affinity_admits(cfg, moment, account) -> bool:
    """Admit `account` for `moment` under the LEGACY affinity rule (RF1: now only the pre-v9 fallback inside
    account_selection_admits — kept as a separate predicate for sources that never wrote an AccountSelection).
    True when casting is OFF (flag-OFF IGNORES persisted affinities — invariant A2), OR the moment is uncast
    (affinities==[] -> fan to all), OR the account is in the cast set."""
    if not cfg.account_casting: return True
    if moment is None or not moment.affinities: return True
    return account in moment.affinities

def account_selection_admits(cfg, led, moment, account) -> bool:
    """RF1: the crosspost gate predicate, reading the DURABLE AccountSelection instead of the non-durable
    affinities tag. Selection-first, with the legacy affinities path preserved ONLY for a source that never
    wrote a selection (pre-v9 / casting-never-ran). Mirrors affinity_admits' OFF-firewall, but it never
    silently fans a CAST source to all — un-collapsible by construction:
      - casting OFF -> admit all (A2 firewall, same as affinity_admits)
      - missing moment under casting-ON -> DENY (never the old admit-all; scrutiny correction)
      - the account has an AccountSelection: fan_all_default/pending decide on the METHOD (admit-all / hold),
        else admit iff the moment is in its moment_ids (the sum-type makes [] unambiguous)
      - no selection for this account BUT the source has others (casting ran) -> DENY (not silent fan-to-all)
      - the source has NO selections at all -> fall back to affinity_admits (legacy/pre-v9 behavior)."""
    if not cfg.account_casting: return True
    if moment is None: return False
    sel = led.account_selection_for(moment.parent_id, account)
    if sel is None:
        if not led.selections_of_source(moment.parent_id):
            return affinity_admits(cfg, moment, account)   # pre-v9 / casting-never-ran -> legacy fallback
        return False                                       # casting RAN, no record for this account -> DENY
    if sel.method == SelectionMethod.fan_all_default: return True   # EXPLICIT, labelled fan-to-all
    if sel.method == SelectionMethod.pending: return False          # gate open, unconverged -> hold (never fan)
    return moment.id in set(sel.moment_ids)                         # specific picks: admit iff selected

def scoped_caption_surfaces(cfg, led, moment, surfaces):
    """M5/RF1: the surfaces a clip's captions are REQUESTED for — the SAME gate the crosspost enforcer uses
    (account_selection_admits), so caption-scoping can never drift from post-minting (the H1 lesson). Returns
    the full list unchanged when casting is OFF or the source is uncast. Within a decision cycle this is a
    SUPERSET of the crosspost survivors (which narrow further by batch target), so every minted post has a
    caption; a post-captioning re-cast SWAP is backstopped by crosspost's cap-is-None skip. `surfaces` is an
    iterable of Surface; returns the (account, platform) tuples request_captions wants."""
    return [(s.account, s.platform) for s in surfaces if account_selection_admits(cfg, led, moment, s.account)]
