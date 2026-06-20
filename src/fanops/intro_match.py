# src/fanops/intro_match.py
"""M6 (structural-hooks): the LLM-vision intro PAIRING matcher — the gate that decides WHICH intro asset
pairs with a clean clip and what the "wait for it / [X] incoming" tease says. A vision gate: request
writes one agent gate per router-reserved (clean_awaiting_strategy:intro_tease) moment carrying the clip's
context (keyframes, router reason, transcript, hook) against the candidate THIRD-PARTY intro assets
(thumbnail, origin_kind); the llm responder answers RANKED pairings; ingest writes them onto
Moment.intro_matches for the producer (stitch_render._intro_tease_candidates).

Gated on cfg.intro_tease + FANOPS_RESPONDER=llm and FAIL-OPEN: no responder, no
answer, or a corrupt answer leaves the moment unmatched (-> no intro_tease plan), and impact-cut + the bare
clip are unaffected — a poisoned matcher pair must never wedge the loop. The gate key is EPHEMERAL (moment +
candidate set + MATCHER_VERSION), SEPARATE from the durable content-addressed stitch_plan id, so changing
the candidate pool or bumping the matcher reseeds the gate."""
from __future__ import annotations
import os
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Moment, MomentState, Source, SourceState, IntroMatchDecision
from fanops.ids import _hash
from fanops.agentstep import write_request, read_response, latest_request_id
from fanops.router import awaiting
from fanops.keyframes import extract_keyframes
from fanops.control import load_guidance
from fanops.compose import _IMAGE_EXTS

MATCHER_VERSION = "v1"                            # bump to re-run every pairing when the matcher logic changes
_INTRO_AWAITING = awaiting("intro_tease")         # "clean_awaiting_strategy:intro_tease"
# a candidate intro asset is unusable in these states: retired/error are gone/broken; discovered is an
# unconfirmed rebuild orphan (M1) — inert until promoted, so never offered to the matcher.
_UNUSABLE_SOURCE_STATES = (SourceState.retired, SourceState.error, SourceState.discovered)


def _candidates(led: Ledger) -> list[Source]:
    """The intro-asset pool: THIRD-PARTY sources (operator-handed outside footage, M1) in a usable state,
    sorted by id so the gate key + payload are stable across request->ingest within a pass."""
    return sorted([s for s in led.sources.values()
                   if s.origin_kind == "third_party" and s.state not in _UNUSABLE_SOURCE_STATES],
                  key=lambda s: s.id)


_TERMINAL_MOMENT_STATES = (MomentState.retired, MomentState.error)

def _reserved(led: Ledger) -> list[Moment]:
    """Moments the router reserved for intro_tease — what this matcher owns. NOT filtered to `decided`: the
    bare-render loop advances a reserved moment to `clipped` BEFORE this matcher runs, and the reservation
    (hook_strategy) persists across that — so matching on the reservation (minus terminal states) is correct,
    mirroring the producer. Sorted by id for stable gate keys across request->ingest within a pass."""
    return sorted([m for m in led.moments.values()
                   if (m.hook_strategy or "") == _INTRO_AWAITING and m.state not in _TERMINAL_MOMENT_STATES],
                  key=lambda m: m.id)


def _gate_key(m: Moment, cands: list[Source]) -> str:
    """Ephemeral key per (moment, candidate set, matcher version): reseeds (new gate) when the pool or the
    matcher changes, so a new candidate or a matcher bump re-asks. Keyed on the MOMENT (content unit), not a
    per-aspect clip — a moment's N aspect clips share one pairing decision, so matching once per moment is
    correct + avoids redundant identical calls."""
    return _hash("intro_match", m.id, *[s.id for s in cands], MATCHER_VERSION)


def _frames(led: Ledger, cfg: Config, m: Moment) -> list[str]:
    """A few source frames in the moment's window — the matcher's eyes on the clip. Fail-open: no real
    source file (tests / not-yet-downloaded) -> [] -> the matcher judges on text only, never spawns ffmpeg
    on an absent path."""
    src = led.sources.get(m.parent_id)
    if not (src and src.source_path and os.path.exists(src.source_path)):
        return []
    return extract_keyframes(src.source_path, m.start, m.end, count=3,
                             out_dir=cfg.agent_io / "keyframes" / m.id)


def _thumb(cfg: Config, s: Source) -> str | None:
    """One still representing a candidate intro: an image IS its own thumbnail; a video yields a keyframe
    near its head. Fail-open to None (absent file / no ffmpeg) — the matcher then sees only label+origin."""
    p = s.source_path or ""
    if not os.path.exists(p):
        return None
    if p.lower().endswith(_IMAGE_EXTS):
        return p
    fr = extract_keyframes(p, 0.0, 2.0, count=1, out_dir=cfg.agent_io / "introthumbs" / s.id)
    return fr[0] if fr else None


def request_intro_match(led: Ledger, cfg: Config) -> Ledger:
    """Write one matcher gate per reserved moment (clip context vs the candidate set). No-op when the
    matcher is off, no candidate assets exist, or nothing is reserved (the gate never appears without real
    work). Idempotent per (moment, candidate set): re-writing would mint a fresh request_id and DELETE the
    responder's answer (write_request invalidates it) -> the gate never clears. Write once; a changed set
    yields a new key -> a new gate."""
    if not (cfg.intro_tease and cfg.responder_mode == "llm"):
        return led
    cands = _candidates(led)
    reserved = _reserved(led)
    if not cands or not reserved:
        return led
    cand_payload = [{"asset_id": s.id, "origin_kind": s.origin_kind,
                     "label": os.path.basename(s.source_path or ""), "thumbnail": _thumb(cfg, s)}
                    for s in cands]
    for m in reserved:
        key = _gate_key(m, cands)
        if latest_request_id(cfg, "intro_match", key) is not None:
            continue
        payload = {"guidance": load_guidance(cfg), "matcher_version": MATCHER_VERSION,
                   "clip": {"moment_id": m.id, "router_reason": m.hook_strategy, "hook": m.hook,
                            "transcript_excerpt": m.transcript_excerpt, "reason": m.reason,
                            "frames": _frames(led, cfg, m)},
                   "candidates": cand_payload}
        write_request(cfg, kind="intro_match", key=key, payload=payload)
    return led


def intro_match_pending(led: Ledger, cfg: Config) -> bool:
    """True when the matcher is ON, there is a reserved moment + a candidate set, and any gate is not yet
    answered — a queryable "matcher still working" read-model (e.g. for Studio status). NOTE: the
    pipeline does NOT hold rendering on this — the bare clip always ships and the
    intro stitch is purely additive, so the producer self-defers (a moment with no `intro_matches` yields no
    candidate) until the pairings land. False when off / nothing reserved / no candidates (never spurious)."""
    if not (cfg.intro_tease and cfg.responder_mode == "llm"):
        return False
    cands = _candidates(led)
    reserved = _reserved(led)
    if not cands or not reserved:
        return False
    return any(read_response(cfg, "intro_match", _gate_key(m, cands), IntroMatchDecision) is None
               for m in reserved)


def ingest_intro_match(led: Ledger, cfg: Config) -> Ledger:
    """Apply the matcher's response: write each reserved moment's RANKED pairings onto Moment.intro_matches
    (best fit first), keeping only pairings that name a REAL candidate asset AND carry a tease_text (a
    pairing the prepend can actually render). No-op until the gate lands (stays unmatched). Fail-open: a
    corrupt/invalid response reads as None (agentstep logs it) -> the moment stays unmatched, never raises."""
    if not (cfg.intro_tease and cfg.responder_mode == "llm"):
        return led
    cands = _candidates(led)
    if not cands:
        return led
    valid_ids = {s.id for s in cands}
    for m in _reserved(led):
        dec = read_response(cfg, "intro_match", _gate_key(m, cands), IntroMatchDecision)
        if dec is None:
            continue
        pairings = [{"asset_id": it.asset_id, "fit_score": float(it.fit_score),
                     "rationale": it.rationale, "tease_text": it.tease_text}
                    for it in dec.items
                    if it.moment_id == m.id and it.asset_id in valid_ids and it.tease_text]
        pairings.sort(key=lambda p: (-p["fit_score"], p["asset_id"]))   # best fit first, deterministic tie-break
        if pairings:
            led.moments[m.id].intro_matches = pairings
    return led
