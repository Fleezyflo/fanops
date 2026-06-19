# src/fanops/hookjudge.py
"""Specificity critic (Phase 3 of the on-screen-hook framework) — the INDEPENDENT LLM judge the
deterministic floor (hookcheck.is_weak_hook) references ("a later LLM critic") but that was never
built. The editor (hookedit.py) AUTHORS each hook grounded in the clip's frames; this pass JUDGES the
result against the verified retention rubric and REJECTS to a clean clip whatever does not clear it —
the teeth that make 'specific, not generic' ENFORCED, not merely suggested in a prompt. Per-hook
verdict (keep|reject); a reject nulls the hook (clean beats slop). Gated by cfg.hook_editor (the same
subsystem as the editor): it runs AFTER the editor on each kept hook. Fail-open: a missing/garbled
verdict, or any verdict that is not an EXPLICIT reject, KEEPS the editor's hook — the judge never
strips a hook on its own silence or failure."""
from __future__ import annotations
import os
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentState, HookJudgeDecision
from fanops.ids import _hash
from fanops.agentstep import write_request, read_response, latest_request_id
from fanops.keyframes import extract_keyframes
from fanops.hookscore import narration_signature
from fanops.control import load_guidance
from fanops.log import get_logger

def _frames(led: Ledger, cfg: Config, m: Moment) -> list[str]:
    """A few source frames in the moment's window — the critic's eyes (mirrors hookedit._frames). The
    judge SEES the footage so it can reject a hook that is untrue to what is shown. Fail-open: no real
    source file (tests / not-yet-downloaded) → [] → the critic degrades to text-only, never spawns
    ffmpeg on a path that isn't there."""
    src = led.sources.get(m.parent_id)
    if not (src and src.source_path and os.path.exists(src.source_path)):
        return []
    return extract_keyframes(src.source_path, m.start, m.end, count=3,
                             out_dir=cfg.agent_io / "keyframes" / m.id)

# A VISION critic now (Task 6): it sends a few FRAMES per item, so — like the editor — the judgeable
# set is CHUNKED into gates of at most this many moments to keep image counts per claude call sane.
# 4 (not 8) for the same reason as _MAX_EDIT_BATCH: 8 x 3 = 24 images on OPUS exceeded the 300s
# `claude -p` ceiling on a 46-clip corpus -> timeout -> blocked. 12 images/call stays well under it.
_MAX_JUDGE_BATCH = 4
# Task 7: one bounded author<->critic repair round. A reject while hook_rounds < this re-opens the hook
# for ONE editor pass; a reject at the cap nulls to a clean clip. 1 = at most one repair (clean beats slop).
_MAX_REPAIR = 1

def _judgeable(led: Ledger) -> list[Moment]:
    """Decided moments whose hook the editor has finalized (hook_edited) but the critic has not yet
    judged. A None hook is already a clean clip — nothing to judge. Requiring hook_edited sequences the
    critic AFTER the editor so it judges the final hook, never a seed the editor is about to rewrite.
    Sorted by id so chunk boundaries are stable across request->ingest within a pass."""
    return sorted([m for m in led.moments.values()
                   if m.state is MomentState.decided and m.hook and m.hook_edited and not m.hook_judged],
                  key=lambda m: m.id)

def _batches(items: list[Moment]) -> list[list[Moment]]:
    # Task 7: partition by repair ROUND before chunking — a batch must NEVER mix rounds. Otherwise a
    # round-1 moment shares a gate with a still-pending round-0 moment, the merged gate answers only
    # partly, and the round-0 clip is STRANDED (never renders). Group by hook_rounds, then chunk each
    # group to _MAX_JUDGE_BATCH (items arrive id-sorted from _judgeable, so groups stay id-ordered).
    out: list[list[Moment]] = []
    for r in sorted({m.hook_rounds for m in items}):
        grp = [m for m in items if m.hook_rounds == r]
        out += [grp[i:i + _MAX_JUDGE_BATCH] for i in range(0, len(grp), _MAX_JUDGE_BATCH)]
    return out

def _digest(batch: list[Moment]) -> str:
    # Round-keyed (mirrors hookedit._digest): a re-opened (round-incremented) moment yields a FRESH gate,
    # never the answered round-0 one — so a repaired hook is re-judged, not silently latched to the stale
    # verdict. A batch never mixes rounds (see _batches), so "{id}:{rounds}" is well-defined per batch.
    return _hash("hookjudge", *sorted(f"{m.id}:{m.hook_rounds}" for m in batch))

def request_hook_judge(led: Ledger, cfg: Config) -> Ledger:
    """Open the critic gate over every judgeable hook (chunked), carrying each hook + its grounding
    context (excerpt/reason/language/pattern/signal), the clip's FRAMES (the judge SEES the footage so it
    can reject a hook untrue to what is shown), and a narration `structure_flag` — narration_signature
    flags a third-person recap with no viewer address as 'third_person_narration' so the critic
    scrutinises it (a SIGNAL, never a gate; it rejects nothing on its own). No-op when the subsystem is
    off or nothing is judgeable (the gate never appears without real work)."""
    if not cfg.hook_judge:
        return led
    items = _judgeable(led)
    if not items:
        return led
    for batch in _batches(items):
        key = _digest(batch)
        # Idempotent per batch: the gate key is that batch's id SET, unchanged until ingest flips
        # hook_judged. Re-writing it would mint a fresh request_id and DELETE an already-written answer
        # (write_request invalidates the old response) -> the gate never clears and rendering HOLDS.
        if latest_request_id(cfg, "hookjudge", key) is not None:
            continue
        payload = {"guidance": load_guidance(cfg),
                   "items": [{"moment_id": m.id, "hook": m.hook, "hook_pattern": m.hook_pattern,
                              "transcript_excerpt": m.transcript_excerpt, "reason": m.reason,
                              "language": led.sources[m.parent_id].language if m.parent_id in led.sources else None,
                              "signal_score": m.signal_score, "frames": _frames(led, cfg, m),
                              "structure_flag": "third_person_narration" if narration_signature(m.hook) else None}
                             for m in batch]}
        write_request(cfg, kind="hookjudge", key=key, payload=payload)
    return led

def in_repair(m: Moment) -> bool:
    """True while a moment is mid-repair: the critic rejected it (hook_rounds > 0) and it has NOT been
    re-finalized (hook_judged False). The pipeline must HOLD rendering — burning the rejected hook now
    would defeat the repair, and neither hold_hooks (computed before the reject re-opened it) nor
    hold_judge (the re-opened moment drops out of _judgeable) covers this single pass. A capped-null or a
    kept hook latches hook_judged=True, so it is NOT in repair and renders normally (clean or final)."""
    return m.hook_rounds > 0 and not m.hook_judged

def hook_judge_pending(led: Ledger, cfg: Config) -> bool:
    """True when the critic is ON, there is a judgeable batch, and its gate is not yet answered — the
    signal for the pipeline to HOLD clip rendering (don't burn a hook the critic may reject)."""
    if not cfg.hook_judge:
        return False
    items = _judgeable(led)
    if not items:
        return False
    return any(read_response(cfg, "hookjudge", _digest(b), HookJudgeDecision) is None
               for b in _batches(items))

def ingest_hook_judge(led: Ledger, cfg: Config) -> Ledger:
    """Apply the critic's verdicts. An EXPLICIT keep=False is NOT terminal on the first reject (Task 7):
    while hook_rounds < _MAX_REPAIR the hook is RE-OPENED for one editor repair — feedback set, round
    advanced, hook_edited/hook_judged reset so editor + critic both run again (the hook TEXT is kept so
    the editor has something to rewrite). At the cap a reject nulls to a clean clip (clean beats slop).
    A keep finalizes (hook_judged=True, feedback cleared). Every other moment is kept and latched
    hook_judged so the pass never loops. Fail-open: an omitted verdict KEEPS its hook (the judge never
    strips on its own silence). No-op until the response lands.

    Double-apply is prevented by the round-keyed _digest + the _judgeable filter, NOT by an extra
    hook_judged guard: re-opening sets hook_edited=False, which drops the moment from _judgeable until
    the editor re-runs; and the round-1 critic gate has a fresh digest, so the stale round-0 response is
    never matched again. (The plan's 'hook_judged is currently True' guard was incoherent — a moment in
    _judgeable always has hook_judged=False here — so it is intentionally omitted.) The re-open mutation
    is wrapped so any failure falls through to the safe null path, never a half-mutated render-old state."""
    if not cfg.hook_judge:
        return led
    advisory = cfg.hook_critic_advisory               # M2: read once -> this pass enforces or advises
    for batch in _batches(_judgeable(led)):
        dec = read_response(cfg, "hookjudge", _digest(batch), HookJudgeDecision)
        if dec is None:
            continue                                     # this batch's gate not answered yet
        by_id = {it.moment_id: it for it in dec.items}
        for m in batch:
            it = by_id.get(m.id)
            mo = led.moments[m.id]
            if it is not None and not it.keep:           # EXPLICIT reject
                reopened = False
                if mo.hook_rounds < _MAX_REPAIR:
                    try:                                 # re-open for ONE repair pass (atomic group)
                        mo.hook_feedback = it.why or None
                        mo.hook_rounds += 1
                        mo.hook_edited = False
                        mo.hook_judged = False
                        reopened = True
                    except Exception as exc:             # any failure -> fall to the safe null path (+ breadcrumb)
                        get_logger(cfg)("hookjudge", m.id, "reopen_error", err=str(exc)[:120])
                        reopened = False
                if reopened:
                    continue                             # editor + critic will run again; do NOT finalize
                if advisory:                             # M2: critic is advisory -> KEEP the raw hook + pattern,
                    mo.hook_feedback = None              # clear the unread carrier (the trace is the log line below)
                    get_logger(cfg)("hookjudge", m.id, "advisory_keep", why=(it.why or "")[:120])
                else:
                    mo.hook = None                       # enforce: cap reached (or re-open failed) -> clean clip
                    mo.hook_pattern = None
                    mo.hook_feedback = None
            elif it is not None:                         # explicit keep -> finalize, clear any feedback
                mo.hook_feedback = None
            mo.hook_judged = True
    return led
