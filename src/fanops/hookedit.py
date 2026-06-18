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
import os
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentState, HookEditDecision
from fanops.ids import _hash
from fanops.agentstep import write_request, read_response, latest_request_id
from fanops.text import sanitize_generated_text
from fanops.hookcheck import is_weak_hook, normalize_hook_pattern
from fanops.keyframes import extract_keyframes
from fanops.control import load_guidance

def _frames(led: Ledger, cfg: Config, m: Moment) -> list[str]:
    """A few source frames in the moment's window — the editor's eyes. Fail-open: no real source
    file (tests / not-yet-downloaded) → [] → the editor degrades to text-only, never spawns ffmpeg
    on a path that isn't there."""
    src = led.sources.get(m.parent_id)
    if not (src and src.source_path and os.path.exists(src.source_path)):
        return []
    return extract_keyframes(src.source_path, m.start, m.end, count=3,
                             out_dir=cfg.agent_io / "keyframes" / m.id)

# Vision sends a few FRAMES per item; one gate over the whole feed would pile ~all clips' frames
# into a single claude call (46 clips x 3 = ~138 images). So the editable set is CHUNKED into gates
# of at most this many moments — sane image counts per call, while ingest's cross-batch `used` dedup
# still enforces feed-wide distinctness.
_MAX_EDIT_BATCH = 8

def _editable(led: Ledger) -> list[Moment]:
    """Decided moments that HAVE a hook and have not yet been edited — what this pass owns. A None
    hook is a deliberate clean clip (the brain found no honest hook); the editor refines EXISTING
    hooks for strength + cross-feed distinctness, it does not invent hooks for clean clips. Sorted by
    id so the chunk boundaries are stable across request->ingest within a pass."""
    return sorted([m for m in led.moments.values()
                   if m.state is MomentState.decided and m.hook and not m.hook_edited],
                  key=lambda m: m.id)

def _batches(items: list[Moment]) -> list[list[Moment]]:
    # Task 7: partition by repair ROUND before chunking (mirrors hookjudge._batches) — a batch must NEVER
    # mix rounds, else a re-opened round-1 moment shares a gate with a round-0 moment and the partly
    # answered gate strands one of them. Group by hook_rounds, then chunk each group to _MAX_EDIT_BATCH.
    out: list[list[Moment]] = []
    for r in sorted({m.hook_rounds for m in items}):
        grp = [m for m in items if m.hook_rounds == r]
        out += [grp[i:i + _MAX_EDIT_BATCH] for i in range(0, len(grp), _MAX_EDIT_BATCH)]
    return out

def _digest(batch: list[Moment]) -> str:
    # Round-keyed (mirrors hookjudge._digest): a re-opened (round-incremented) moment yields a FRESH gate,
    # not the answered round-0 one — so the repair edit is a new request, not a stale latched answer.
    # Identical across request->ingest within a pass (the responder writes only the response, not the
    # ledger, so the set + rounds are unchanged). A batch never mixes rounds (see _batches).
    return _hash("hookedit", *sorted(f"{m.id}:{m.hook_rounds}" for m in batch))

def request_hook_edit(led: Ledger, cfg: Config) -> Ledger:
    """Write the single feed-level hookedit gate carrying every editable hook + its grounding context
    (excerpt/reason/language/signal) so the editor can judge truthfulness and diversify. No-op when
    the editor is off or nothing is editable (so the gate never appears unless it has real work)."""
    if not cfg.hook_editor:
        return led
    items = _editable(led)
    if not items:
        return led
    for batch in _batches(items):
        key = _digest(batch)
        # Idempotent per batch: the gate key is that batch's id SET, unchanged until ingest flips
        # hook_edited. Re-writing it would mint a fresh request_id and DELETE the editor's already
        # written response (write_request invalidates the old answer) -> the gate never clears and
        # rendering HOLDS forever. Write once per batch; a changed set yields a new digest -> new gate.
        if latest_request_id(cfg, "hookedit", key) is not None:
            continue
        payload = {"guidance": load_guidance(cfg),
                   "items": [{"moment_id": m.id, "hook": m.hook, "hook_pattern": m.hook_pattern,
                              "transcript_excerpt": m.transcript_excerpt, "reason": m.reason,
                              "language": led.sources[m.parent_id].language if m.parent_id in led.sources else None,
                              "signal_score": m.signal_score, "frames": _frames(led, cfg, m),
                              "critic_feedback": m.hook_feedback} for m in batch]}
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
    return any(read_response(cfg, "hookedit", _digest(b), HookEditDecision) is None
               for b in _batches(items))

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
    edit_ids = {m.id for m in items}
    # cross-feed dedup spans ALL batches: seed `used` from hooks on moments OUTSIDE this edit set
    # (already-edited / prior passes), then accumulate each kept hook as we walk the batches, so two
    # clips anywhere in the feed can't land the same hook.
    used = {(m.hook or "").strip().lower() for m in led.moments.values()
            if m.hook and m.id not in edit_ids}
    for batch in _batches(items):
        dec = read_response(cfg, "hookedit", _digest(batch), HookEditDecision)
        if dec is None:
            continue                                     # this batch's gate not answered yet
        by_id = {it.moment_id: it for it in dec.items}
        # Editor's EXPLICIT rewrites claim hooks before clips falling back to their original, so a
        # deliberate rewrite wins a cross-feed tie over a kept-original (stable within each group).
        ordered = sorted(batch, key=lambda m: 0 if (by_id.get(m.id) and by_id[m.id].hook) else 1)
        for m in ordered:
            it = by_id.get(m.id)
            old_pattern = m.hook_pattern
            candidate = it.hook if (it is not None and it.hook) else m.hook   # rewrite wins; else keep
            new = None
            if candidate:
                h = sanitize_generated_text(candidate.strip())
                if h and not is_weak_hook(h, used):
                    new = h
                    used.add(h.lower())
            # P1: track the pattern with the surviving hook — an editor rewrite carries its declared
            # pattern (else keep the old one); a kept original keeps its pattern; a nulled hook -> None.
            if new is None:
                pattern = None
            elif it is not None and it.hook:
                pattern = normalize_hook_pattern(it.hook_pattern) or old_pattern
            else:
                pattern = old_pattern
            led.moments[m.id].hook = new
            led.moments[m.id].hook_pattern = pattern
            led.moments[m.id].hook_edited = True
            led.moments[m.id].hook_feedback = None       # Task 8: repair consumed — clear the critic's note
    return led
