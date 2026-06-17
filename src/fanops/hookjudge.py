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
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentState, HookJudgeDecision
from fanops.ids import _hash
from fanops.agentstep import write_request, read_response, latest_request_id
from fanops.control import load_guidance

# Text-only critic (no frames — the editor already did the vision truth-check), so a gate can carry
# more hooks per call than the vision editor. Still chunked so a huge feed stays a sane prompt size.
_MAX_JUDGE_BATCH = 12

def _judgeable(led: Ledger) -> list[Moment]:
    """Decided moments whose hook the editor has finalized (hook_edited) but the critic has not yet
    judged. A None hook is already a clean clip — nothing to judge. Requiring hook_edited sequences the
    critic AFTER the editor so it judges the final hook, never a seed the editor is about to rewrite.
    Sorted by id so chunk boundaries are stable across request->ingest within a pass."""
    return sorted([m for m in led.moments.values()
                   if m.state is MomentState.decided and m.hook and m.hook_edited and not m.hook_judged],
                  key=lambda m: m.id)

def _batches(items: list[Moment]) -> list[list[Moment]]:
    return [items[i:i + _MAX_JUDGE_BATCH] for i in range(0, len(items), _MAX_JUDGE_BATCH)]

def _digest(batch: list[Moment]) -> str:
    # Stable, order-independent key for ONE batch: the SET of its moment ids (mirrors hookedit._digest).
    return _hash("hookjudge", *sorted(m.id for m in batch))

def request_hook_judge(led: Ledger, cfg: Config) -> Ledger:
    """Open the critic gate over every judgeable hook (chunked), carrying each hook + its grounding
    context (excerpt/reason/language/pattern/signal) so the judge can test anchoring + portability. No
    frames: the editor already grounded against the footage; the critic judges specificity from text.
    No-op when the subsystem is off or nothing is judgeable (the gate never appears without real work)."""
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
                              "signal_score": m.signal_score} for m in batch]}
        write_request(cfg, kind="hookjudge", key=key, payload=payload)
    return led

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
    """Apply the critic's verdicts: a hook with an EXPLICIT keep=False is nulled to a clean clip (its
    pattern cleared too); every other hook is kept. Every moment in an answered batch latches
    hook_judged=True so the pass never repeats (no loop). Fail-open: a moment the critic omitted, or a
    verdict that is not an explicit reject, KEEPS its hook — the judge never strips a hook on its own
    silence/failure. No-op until the response lands (the gate stays pending)."""
    if not cfg.hook_judge:
        return led
    for batch in _batches(_judgeable(led)):
        dec = read_response(cfg, "hookjudge", _digest(batch), HookJudgeDecision)
        if dec is None:
            continue                                     # this batch's gate not answered yet
        by_id = {it.moment_id: it for it in dec.items}
        for m in batch:
            it = by_id.get(m.id)
            if it is not None and not it.keep:           # EXPLICIT reject -> clean clip (clean beats slop)
                led.moments[m.id].hook = None
                led.moments[m.id].hook_pattern = None
            led.moments[m.id].hook_judged = True
    return led
