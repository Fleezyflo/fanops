# src/fanops/hookedit.py
"""Feed-aware on-screen-hook editor (Phase 2 of the hook framework). The moment responder answers
each clip's gate in ISOLATION (responder.answer_pending iterates gates independently — no cross-clip
visibility), so it cannot avoid reusing a hook or an opening TEMPLATE across the feed: the diagnosed
round-2 failure was 'before he was Moh Flow' on six clips. This pass collects EVERY decided clip's
hook into ONE feed-level agent gate, lets the editor rewrite the weak/duplicated/templated ones into
strong, DISTINCT hooks (seeing the whole feed at once), then writes them back BEFORE any clip burns
them. The deterministic guard (hookcheck.is_weak_hook) is the floor: a rewrite that is STILL slop or
STILL a cross-feed duplicate is nulled to a clean clip (clean beats slop). Gated by cfg.hook_editor
(DEFAULT OFF, opt-in, fail-open); with the flag off there is no gate and behavior is byte-identical."""
from __future__ import annotations
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentState, HookEditDecision
from fanops.ids import _hash
from fanops.agentstep import write_request, read_response, latest_request_id
from fanops.text import sanitize_generated_text
from fanops.hookcheck import is_weak_hook
from fanops.moments import _guidance

def _editable(led: Ledger) -> list[Moment]:
    """Decided moments that HAVE a hook and have not yet been edited — the batch this pass owns.
    A None hook is a deliberate clean clip (the brain found no honest hook); the editor refines
    EXISTING hooks for strength + cross-feed distinctness, it does not invent hooks for clean clips."""
    return [m for m in led.moments.values()
            if m.state is MomentState.decided and m.hook and not m.hook_edited]

def _digest(items: list[Moment]) -> str:
    # Stable, order-independent feed key: the SET of moment ids being edited. Reseeds (new gate) when
    # the editable set changes; identical across request->ingest within a pass (the responder writes
    # only the response file, never the ledger, so the set + hooks are unchanged between the two).
    return _hash("hookedit", *sorted(m.id for m in items))

def request_hook_edit(led: Ledger, cfg: Config) -> Ledger:
    """Write the single feed-level hookedit gate carrying every editable hook + its grounding context
    (excerpt/reason/language/signal) so the editor can judge truthfulness and diversify. No-op when
    the editor is off or nothing is editable (so the gate never appears unless it has real work)."""
    if not cfg.hook_editor:
        return led
    items = _editable(led)
    if not items:
        return led
    key = _digest(items)
    # Idempotent: the gate key is the EDITABLE SET (ids only), unchanged until ingest flips
    # hook_edited. Re-writing it every pass would mint a fresh request_id and DELETE the editor's
    # already-written response (write_request invalidates the old answer) -> the gate would never
    # clear and clip rendering would HOLD forever. So write once per feed set; a changed set yields a
    # new digest -> a new gate.
    if latest_request_id(cfg, "hookedit", key) is not None:
        return led
    payload = {"guidance": _guidance(cfg),
               "items": [{"moment_id": m.id, "hook": m.hook,
                          "transcript_excerpt": m.transcript_excerpt, "reason": m.reason,
                          "language": led.sources[m.parent_id].language if m.parent_id in led.sources else None,
                          "signal_score": m.signal_score} for m in items]}
    write_request(cfg, kind="hookedit", key=key, payload=payload)
    return led

def hook_edit_pending(led: Ledger, cfg: Config) -> bool:
    """True when the editor is ON, there is an editable batch, and its gate is not yet answered —
    the signal for the pipeline to HOLD clip rendering (don't burn a hook the editor may rewrite)."""
    if not cfg.hook_editor:
        return False
    items = _editable(led)
    if not items:
        return False
    return read_response(cfg, "hookedit", _digest(items), HookEditDecision) is None

def ingest_hook_edit(led: Ledger, cfg: Config) -> Ledger:
    """Apply the editor's response: each moment's hook becomes the editor's rewrite (or its original
    when the editor omitted it), validated through the SAME deterministic guard the brain's hooks pass
    — sanitized (em-dash strip) + rejected to None on slop or a cross-feed duplicate. Every moment in
    the batch latches hook_edited=True so the pass never repeats (no loop). No-op until the response
    lands (the gate stays pending)."""
    if not cfg.hook_editor:
        return led
    items = _editable(led)
    if not items:
        return led
    dec = read_response(cfg, "hookedit", _digest(items), HookEditDecision)
    if dec is None:
        return led
    by_id = {it.moment_id: it for it in dec.items}
    edit_ids = {m.id for m in items}
    # cross-feed dedup: seed `used` from hooks on moments OUTSIDE this batch (already-edited / other
    # passes), then add each kept hook as we go so two clips in this batch can't land the same hook.
    used = {(m.hook or "").strip().lower() for m in led.moments.values()
            if m.hook and m.id not in edit_ids}
    # Editor's EXPLICIT rewrites claim hooks before clips falling back to their original, so a
    # deliberate rewrite wins a cross-feed tie over a kept-original (stable within each group).
    ordered = sorted(items, key=lambda m: 0 if (by_id.get(m.id) and by_id[m.id].hook) else 1)
    for m in ordered:
        it = by_id.get(m.id)
        candidate = it.hook if (it is not None and it.hook) else m.hook   # rewrite wins; else keep original
        new = None
        if candidate:
            h = sanitize_generated_text(candidate.strip())
            if h and not is_weak_hook(h, used):
                new = h
                used.add(h.lower())
        led.moments[m.id].hook = new
        led.moments[m.id].hook_edited = True
    return led
