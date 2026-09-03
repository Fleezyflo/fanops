"""Caption stage. request_captions() asks the agent for a per-surface caption set (different
wording per surface — opsec + platform fit). ingest_captions() validates each, runs the
brand-risk HOLD in BOTH English and Arabic (FIX F33), REQUIRES a caption for every requested
surface (FIX F74 — no silent default), stores clean captions keyed by the documented
'account/platform' contract (FIX F43), and advances only if nothing is held."""
from __future__ import annotations
import logging
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState, Platform, CaptionSet
from fanops.agentstep import write_request, read_response, request_path
# Creative-variation v2: the SAFE half of the A/B loop. Imported here (caption side) ONLY — the
# amplify/delete-cascade path (track.py/pipeline.py) MUST stay blind to the learner (C1 invariant,
# enforced by an isolation grep test). Bound at module scope so request_captions' fail-open path is
# unit-patchable (tests monkeypatch fanops.caption.best_hooks to prove a raising scorer is swallowed).
from fanops.variant_learning import best_hooks
# Creative-variation v3 (the bandit): the alternative OWN-surface allocator, selected by
# FANOPS_VARIANT_UCB inside _learned_hooks. SAME safe caption-request side as best_hooks (the
# amplify/delete path stays blind to it; isolation tests enforce it). Bound at module scope so the
# fail-open path is unit-patchable (tests monkeypatch fanops.caption.ucb_rank to prove a raising
# scorer is swallowed). variant_amplify keeps using best_hooks as its floor — v3 does not change that.
from fanops.variant_learning import ucb_rank
# Cross-surface transfer (the v2 follow-up): SAME safe side as best_hooks — imported here ONLY
# (the amplify/delete path stays blind to it; the isolation tests enforce it). Bound at module scope
# so request_captions' fail-open path is unit-patchable (tests monkeypatch fanops.caption.transferred_hooks).
from fanops.variant_transfer import transferred_hooks
from fanops.personas import caption_directive
from fanops.hashtags import ship_from_lock, load_measurements
from fanops.control import load_guidance
from fanops.caption_compose import (_hashtag_metrics_for, _source_lock_completed, _source_lock_tags)
from fanops.caption_ingest import (_caption_entry, _lang_base, _platform_for_surface, _request_surfaces,
                                    _tags_in, brand_risk_flag, is_tags_only_caption)

logger = logging.getLogger(__name__)

# P2 coherent variations. The CHEAP-TEXT axes a justified variant may move (render-expensive frame/
# length axes are a P4-gated follow-up, NOT here).
VARIATION_AXES = ("hook_string", "caption_angle", "hook_placement")


def _surface_str(account: str, platform: Platform) -> str:
    return f"{account}/{platform.value}"                  # the documented lookup contract

def caption_request_stale(cfg: Config, clip_id: str, want_surfaces: list[tuple[str, Platform]]) -> bool:
    """True when the on-disk caption gate must be (re)opened: no request yet, or the requested surface
    set no longer matches what casting would ask for now (e.g. IG surfaces added after a TikTok-only
    request), or any surface lacks the request-record platform key (legacy hand-edited request).
    A current request awaiting an answer is NOT stale — the responder still needs to land."""
    want = {_surface_str(a, p) for a, p in want_surfaces}
    if not want:
        return False
    if not request_path(cfg, "captions", clip_id).exists():
        return True
    try:
        got, _, surface_platform, *_ = _request_surfaces(cfg, clip_id)
    except Exception as exc:
        logger.warning("caption staleness: request surfaces unreadable for %s (%s); regenerating", clip_id, exc)
        return True
    if got != want:
        return True
    return any(not surface_platform.get(s) for s in got)   # missing platform -> regenerate next pass

def _learned_hooks(led: Ledger, cfg: Config,
                   surfaces: list[tuple[str, Platform]]) -> list[str]:
    """Creative-variation v2 — the loop-closing read. When FANOPS_VARIANT_LEARNING is on, ask the
    gated scorer for each surface's trustworthy winning hook and return the de-duplicated union
    (insertion order preserved -> deterministic). Gated OFF by default -> []. FAIL-OPEN: any error
    is logged once and yields [] so a learning failure can never block a caption or hold the clip."""
    if not cfg.variant_learning:
        return []
    try:
        learned: list[str] = []
        seen: set[str] = set()
        scorer = ucb_rank if cfg.variant_ucb else best_hooks   # v3 bandit vs v2 gated-greedy
        for acct, plat in surfaces:
            for h in scorer(led, cfg, acct, plat):
                if h not in seen:
                    seen.add(h)
                    learned.append(h)
        return learned
    except Exception:
        logger.warning("variant_learning hint skipped (fail-open)", exc_info=True)
        return []

def _transferred_hooks(led: Ledger, cfg: Config, accounts,
                       surfaces: list[tuple[str, Platform]]) -> list[str]:
    """Cross-surface transfer — the cold-start prior. When FANOPS_VARIANT_TRANSFER is on, ask the
    gated transfer scorer for each surface's borrowed STYLE(s) and return the de-duplicated union
    (insertion order preserved -> deterministic). Gated OFF by default, or no accounts registry -> [].
    FAIL-OPEN: any error is logged once and yields [] so a transfer failure can never block a caption."""
    if not cfg.variant_transfer or accounts is None:
        return []
    from fanops.validation_gate import learning_validated
    if not learning_validated(cfg):
        return []                              # VALIDATION-FROZEN (Phase 2): never bias a caption toward a style
                                               # measured on an UNCONFIRMED lift — mirrors variant_amplify's gate
    try:
        out: list[str] = []
        seen: set[str] = set()
        for acct, plat in surfaces:
            for h in transferred_hooks(led, cfg, accounts, acct, plat):
                if h not in seen:
                    seen.add(h)
                    out.append(h)
        return out
    except Exception:
        logger.warning("variant transfer prior skipped (fail-open)", exc_info=True)
        return []


def request_captions(led: Ledger, cfg: Config, clip_id: str,
                     surfaces: list[tuple[str, Platform]], accounts=None) -> Ledger:
    clip = led.clips[clip_id]
    moment = led.moments[clip.parent_id]
    src = led.sources.get(moment.parent_id)
    # HV1-WALK: do not open the caption gate until this source has a completed lock row.
    # Empty completed lock (`researched_at` + `lock: []`) DOES open and ships empty tags.
    # Missing sidecar / no researched_at → clip stays rendered; no request file.
    if not _source_lock_completed(cfg, src):
        return led
    learned = _learned_hooks(led, cfg, surfaces)
    transferred = _transferred_hooks(led, cfg, accounts, surfaces)
    # Per-surface persona (the UI-set fan voice). Rides the payload so it survives to ingest (which reads the
    # request back). Absent registry / None value -> no key (byte-identical to before).
    personas = {a.handle: caption_directive(a) for a in accounts.accounts} if accounts is not None else {}
    lock = _source_lock_tags(cfg, src)
    meas = load_measurements(cfg)
    hashtag_metrics = _hashtag_metrics_for(meas, lock)
    payload = {
        "clip_id": clip_id,
        "transcript_excerpt": moment.transcript_excerpt,
        "language": src.language if src else None,
        "guidance": load_guidance(cfg),
        "surfaces": [{"surface": _surface_str(acct, plat), "platform": plat.value,
                      **({"persona": pv} if (pv := personas.get(acct)) else {}),
                      **({"hashtag_store": lock} if lock else {})}
                     for acct, plat in surfaces],
        **({"hashtag_metrics": hashtag_metrics} if hashtag_metrics else {}),
        # variation v2: only present when a surface crossed the trust gate -> OFF/below-gate keeps
        # the payload byte-identical to pre-v2 (caption_prompt renders this block when present).
        **({"learned_hooks": learned} if learned else {}),
        # transfer (v2 follow-up): a borrowed cross-surface STYLE for a COLD recipient — separate
        # key so own-signal reads as primary; absent unless the flag is on AND a donor qualifies.
        **({"learned_hooks_transferred": transferred} if transferred else {}),
    }
    write_request(cfg, kind="captions", key=clip_id, payload=payload)
    led.set_clip_state(clip_id, ClipState.captions_requested)
    return led

def ingest_captions(led: Ledger, cfg: Config, clip_id: str, *, pass_recent: dict[str, list[str]] | None = None) -> Ledger:
    cs = read_response(cfg, "captions", clip_id, CaptionSet)
    if cs is None:
        return led                                       # pending or stale
    clip = led.clips[clip_id]
    # the clip's source language is the contract the caption must match (AUDIT H5).
    src = led.sources.get(led.moments[clip.parent_id].parent_id)
    # what surfaces did we ask for, and their per-surface lock store? (the request is the truth)
    requested, _surface_corpus, surface_platform, _surface_store, _content_tags = _request_surfaces(cfg, clip_id)
    # AUDIT H6: a caption targeting a surface we never requested (e.g. a typo'd key) is held with
    # a SPECIFIC reason NAMING the bad surface(s) — diagnosed before the generic missing-caption
    # logic so a typo'd-but-present caption is not mislabelled "missing".
    unknown = [item.surface for item in cs.items if item.surface not in requested]
    if unknown:
        clip.held = True
        clip.held_reason = f"caption(s) for unknown surface(s): {', '.join(unknown)}"
        led.set_clip_state(clip_id, ClipState.held)
        return led
    held_reason = None
    tags_only = False
    for item in cs.items:
        # AUDIT H5: a caption declared in a language other than the source's is held for a human
        # (conservative — hold the WHOLE clip on first mismatch). Compare on the BASE language
        # subtag (en-US == EN == en) so a region/casing variant is NOT a false mismatch (Phase-C
        # adversarial finding). Only compare when BOTH languages are known. RESIDUAL (documented,
        # mitigated at the prompt — see prompts.caption_prompt): a None item.language is treated as
        # "not a declared mismatch" and passes — blanket-holding undeclared captions would
        # false-positive every legitimately-undeclared caption and halt an autonomous run; instead
        # our committed prompt REQUIRES the model to self-declare `language`, so our own path always
        # carries a tag (a wrong-language caption then carries a wrong tag and IS held here).
        src_base = _lang_base(src.language) if src else None
        item_base = _lang_base(item.language)
        if src_base and item_base and item_base != src_base:
            clip.held = True
            clip.held_reason = (f"caption language {item.language!r} != source language "
                                f"{src.language!r} for {item.surface}")
            led.set_clip_state(clip_id, ClipState.held)
            return led
        reason = brand_risk_flag(item.caption, cfg)          # audit b: honor tuning.json override
        # brand-risk runs on the ORIGINAL caption (the guardrail stays on what the model wrote);
        if reason and held_reason is None:
            held_reason = reason
        # ...THEN ship picks ∩ sidecar lock. Request hashtag_store is the menu, not membership.
        _platform_for_surface(item.surface, surface_platform)   # AGENT-6: request platform still required
        handle = item.surface.split("/", 1)[0]
        picks = item.hashtags or _tags_in(item.caption)
        tags = ship_from_lock(picks, _source_lock_tags(cfg, src))
        if pass_recent is not None: pass_recent.setdefault(handle, []).extend(tags)
        clip.meta_captions[item.surface] = _caption_entry(
            tags, [str(h) for h in (item.hashtags or [])],
            caption=(item.caption or "").strip(), tag_sources={})
        if is_tags_only_caption(item.caption, item.hashtags):
            tags_only = True
    answered = {item.surface for item in cs.items}
    missing = requested - answered
    # Brand-risk (already scored on present items) wins over tags-only, which wins over missing.
    # Do not manufacture caption = join(tags) for a missing surface / items:[].
    if held_reason is None and tags_only:
        held_reason = "caption_tags_only"
    if held_reason is None and missing:
        held_reason = "caption_missing_language"
    if held_reason:
        clip.held = True
        clip.held_reason = held_reason
        led.set_clip_state(clip_id, ClipState.held)     # FIX: explicit held state, not 'rendered'
        return led
    clip.held = False
    clip.held_reason = None                              # a clean re-ingest must not keep a prior hold's reason (held=False -> held_reason=None)
    led.set_clip_state(clip_id, ClipState.captioned)
    return led
