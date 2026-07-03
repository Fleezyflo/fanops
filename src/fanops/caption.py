"""Caption stage. request_captions() asks the agent for a per-surface caption set (different
wording per surface — opsec + platform fit). ingest_captions() validates each, runs the
brand-risk HOLD in BOTH English and Arabic (FIX F33), REQUIRES a caption for every requested
surface (FIX F74 — no silent default), stores clean captions keyed by the documented
'account/platform' contract (FIX F43), and advances only if nothing is held."""
from __future__ import annotations
import json
import logging
import re
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
from fanops.hashtags import vet_hashtags_traced, content_tag_candidates, load_store
from fanops.log import get_logger
from fanops.control import load_guidance
from fanops.hookcheck import is_weak_hook

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"#\S+")

# P2 coherent variations. The CHEAP-TEXT axes a justified variant may move (render-expensive frame/
# length axes are a P4-gated follow-up, NOT here). normalize_variation_axis maps an LLM label to a
# canonical key (case/space/dash-insensitive), unknown -> None — so a bad label is "unlabeled", never a
# crash. The coherence gate (T2) requires a KNOWN axis + a rationale; P3 attributes reach by the axis.
VARIATION_AXES = ("hook_string", "caption_angle", "hook_placement")

def normalize_variation_axis(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    key = re.sub(r"[\s/\-]+", "_", value.strip().lower())
    return key if key in VARIATION_AXES else None

def coherent_variation(hook, rationale, *, siblings=frozenset()) -> bool:
    """T2 coherence gate: a variant earns its extra post ONLY when it is distinct AND explained —
    (a) a non-empty hook that (b) clears the MECHANICAL floor against its siblings (is_weak_hook:
    empty / exact-dup / opening-template cluster), and (c) carries a non-empty rationale. Else dropped:
    clean beats noise. Pure. NB v2: is_weak_hook no longer judges QUALITY (superlative/hype/narration) —
    that moved to the reasoning critic, which does NOT run on per-surface caption siblings, so a
    quality-weak caption variant can now post; accepted trade (rare; the caption prompt discourages hype)."""
    if not (rationale and str(rationale).strip()):
        return False
    if not (hook and str(hook).strip()):
        return False
    return not is_weak_hook(hook, siblings)


def _tags_in(caption: str | None) -> list[str]:
    """Hashtags found inside a caption line (the model's tags live in the array AND the caption
    text); used as the fallback when the structured `hashtags` array is empty."""
    return _TAG_RE.findall(caption or "")

def _platform_of(surface: str, *, cfg: Config | None = None) -> Platform:
    """The platform half of an 'account/platform' surface key. An unknown/missing platform falls
    back to instagram (a sane default) rather than crashing an autonomous ingest on a typo'd key —
    but the coercion is now LOUD (finding #8): when cfg is threaded in from ingest_captions the bad
    key is logged, so an IG hashtag set landing on a mis-keyed surface is a signal, not a silent swap."""
    tail = (surface or "").rsplit("/", 1)[-1].strip().lower()
    try:
        return Platform(tail)
    except ValueError:
        if cfg is not None:                              # loud-coerce: never crash the run, but leave a trace
            get_logger(cfg)("captions", surface or "-", "platform_coerced", bad_key=tail, coerced_to="instagram")
        return Platform.instagram

# DEFAULT English off-brand / begging / main-brand-linkage anti-patterns. Operator-overridable
# via 00_control/tuning.json -> "offbrand_en" (audit b); when that key is present it REPLACES this
# list. These stay the in-code fallback used whenever no override is supplied.
_OFFBRAND_EN = [r"\bsorry\b", r"\bpls\b", r"\bplease stream\b", r"🥺", r"\bbeg(ging)?\b",
                r"\bofficial (drop|release)\b", r"\bfrom the label\b", r"\blink in bio\b"]
# DEFAULT Arabic equivalents (FIX F33): please / please listen / link in bio / begging / sorry.
# Operator-overridable via tuning.json -> "offbrand_ar".
_OFFBRAND_AR = [r"من فضلك", r"رجاء", r"أرجوكم?", r"اسمعوا", r"لينك في البايو", r"الرابط في البايو",
                r"آسف", r"بليز"]
# Precompiled DEFAULT matcher (compiled once at import — the no-override hot path stays fast).
_RE = re.compile("|".join(_OFFBRAND_EN + _OFFBRAND_AR), re.IGNORECASE)

def _risk_re(cfg: Config | None) -> "re.Pattern[str]":
    """Effective brand-risk matcher. With no cfg (or no tuning override) returns the precompiled
    DEFAULT _RE. When tuning.json supplies "offbrand_en"/"offbrand_ar", those lists REPLACE the
    corresponding default (clearest contract: the operator sees exactly the set they wrote) and we
    compile at CALL TIME. A present-but-empty list disables that language's patterns. A bad regex
    in the override falls back to the default matcher rather than crashing an autonomous run."""
    if cfg is None:
        return _RE
    t = cfg.tuning()
    if "offbrand_en" not in t and "offbrand_ar" not in t:
        return _RE                                          # no override -> default fast path
    en = t["offbrand_en"] if "offbrand_en" in t else _OFFBRAND_EN
    ar = t["offbrand_ar"] if "offbrand_ar" in t else _OFFBRAND_AR
    pats = [p for p in list(en) + list(ar) if p]            # drop empties so "" can't match-all
    if not pats:
        return re.compile(r"(?!)")                          # an operator who cleared both lists -> never flags
    try:
        return re.compile("|".join(pats), re.IGNORECASE)
    except re.error:
        return _RE                                          # malformed override regex -> safe default

def brand_risk_flag(caption: str, cfg: Config | None = None) -> str | None:
    m = _risk_re(cfg).search(caption or "")
    return (f"off-brand / breaks bravado guardrail: matched '{m.group(0)}'") if m else None


def _surface_str(account: str, platform: Platform) -> str:
    return f"{account}/{platform.value}"                  # the documented lookup contract

def caption_request_stale(cfg: Config, clip_id: str, want_surfaces: list[tuple[str, Platform]]) -> bool:
    """True when the on-disk caption gate must be (re)opened: no request yet, or the requested surface
    set no longer matches what casting would ask for now (e.g. IG surfaces added after a TikTok-only
    request). A current request awaiting an answer is NOT stale — the responder still needs to land."""
    want = {_surface_str(a, p) for a, p in want_surfaces}
    if not want:
        return False
    if not request_path(cfg, "captions", clip_id).exists():
        return True
    try:
        got, *_ = _request_surfaces(cfg, clip_id)
    except Exception:
        return True
    return got != want

def _lang_base(tag: str | None) -> str | None:
    """Normalise an IETF-ish language tag to its base subtag for comparison (AUDIT H5 hardening).
    A Phase-C skeptic proved the naive exact-string compare HELD legitimate same-language captions
    whose tag carried a region subtag or different casing — `en-US`, `EN`, `en-GB`, `"en "` were all
    wrongly held against an `en` source (a harmful false-positive that, for an autonomous run,
    silently wedges the clip). Real LLM/Whisper language tags routinely use those variants. We
    therefore compare BASE language only: lowercase, strip surrounding whitespace, and take the
    primary subtag before the first '-' or '_'. None/empty stays None (callers treat unknown as
    'not a declared mismatch' — see ingest_captions)."""
    if not tag:
        return None
    base = tag.strip().lower().replace("_", "-").split("-", 1)[0]
    return base or None

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
    learned = _learned_hooks(led, cfg, surfaces)
    transferred = _transferred_hooks(led, cfg, accounts, surfaces)
    # Per-surface persona (the UI-set fan voice). Rides the payload so it survives to ingest (which reads the
    # request back). Absent registry / None value -> no key (byte-identical to before).
    personas = {a.handle: caption_directive(a) for a in accounts.accounts} if accounts is not None else {}
    # B1: the per-persona curated corpus (hydrated onto the account from its linked Persona) — the SOLE per-
    # account hashtag differentiator since the tag_lean fold (M3). Rides the payload so it survives to ingest
    # (-> vet_hashtags floats it ahead of the frozen rank) AND the prompt shows it. Empty corpus -> no key.
    corpora = {a.handle: list(getattr(a, "hashtag_corpus", []) or []) for a in accounts.accounts} if accounts is not None else {}
    # Per-clip CONTENT signal: deterministic candidate tags derived from THIS clip's transcript. Rides the
    # payload at the clip level (same for every surface of the clip) so it survives to ingest (-> vet_hashtags
    # content= joins the membership + reserves a slot) AND the prompt shows it. Empty (blank/instrumental/
    # Arabic transcript) -> no key -> byte-identical to before this feature.
    content_tags = content_tag_candidates(moment.transcript_excerpt)
    payload = {
        "clip_id": clip_id,
        "transcript_excerpt": moment.transcript_excerpt,
        "language": src.language if src else None,
        "guidance": load_guidance(cfg),
        **({"content_tags": content_tags} if content_tags else {}),
        "surfaces": [{"surface": _surface_str(acct, plat), "platform": plat.value,
                      **({"persona": pv} if (pv := personas.get(acct)) else {}),
                      **({"corpus": cv} if (cv := corpora.get(acct)) else {})}
                     for acct, plat in surfaces],
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

def _request_surfaces(cfg: Config, clip_id: str) -> tuple[set, dict, dict, list]:
    """The crosspost request is the source of truth for completeness: which surfaces were ASKED for, the
    per-surface curated corpus (B1 — the per-account hashtag differentiator) each carried (None/absent when
    unset) so vet_hashtags can float it, the per-surface REQUESTED platform (AGENT-6 — the vetting truth, not a
    re-parse of the model's echoed string), plus the CLIP-level content_tags (the per-clip content signal, same
    for every surface). Returns (requested, surface_corpus, surface_platform, content_tags). Pure read."""
    req = json.loads(request_path(cfg, "captions", clip_id).read_text())
    surfaces = req.get("surfaces", [])
    requested = {s["surface"] for s in surfaces}
    surface_corpus = {s["surface"]: s.get("corpus") for s in surfaces}
    surface_platform = {s["surface"]: s.get("platform") for s in surfaces}   # AGENT-6: the REQUESTED platform (truth)
    content_tags = req.get("content_tags") or []
    return requested, surface_corpus, surface_platform, content_tags

def _platform_for_surface(surface: str, surface_platform: dict, *, cfg: Config | None = None) -> Platform:
    """AGENT-6: the platform we ASKED to caption (from the request record), not a re-parse of the model's
    echoed surface string. A mangled model surface can no longer vet/cap under the wrong platform's discovery
    set. Falls back to the legacy tail-parse ONLY when the request omits the platform (older on-disk request).
    cfg is passed through so a bad-key tail-parse coercion (#8) breadcrumbs at the _platform_of layer."""
    p = surface_platform.get(surface)
    if p:
        try: return Platform(p)
        except ValueError: pass
    return _platform_of(surface, cfg=cfg)   # #8: pass cfg so a bad-key tail-parse coercion breadcrumbs


def _caption_entry(tags: list, hashtags_raw: list, *, fallback: bool = False, tag_sources: dict | None = None) -> dict:
    """One surface's stored meta_captions entry. The posted caption IS the vetted <=4-tag line (hashtags-only).
    hashtags_raw keeps the model's RAW picks verbatim (finding #3: Studio shows picked-vs-vetted; display-only).
    ROOT FIX: the caption gate no longer authors an on-screen hook (the frame-seeing moment gate does, via
    hooks_by_persona) -> hook/axis/rationale are always None here. They are KEPT on the persisted entry (not on
    the CaptionItem model — AGENT-7 dropped those) as the DORMANT variant-A/B contract the dormant readers
    expect (variant_amplify/digest read entry.get("hook"); crosspost reads cap.get("axis")). `fallback` marks a
    seed-tag synthesized entry.
    `tag_sources` is the per-tag provenance ({tag: source}) — proves every shipped tag traces to a real
    signal (content|corpus|region|graph-reach|discovery|genre-floor); Review renders it. Absent -> {}."""
    entry = {"caption": " ".join(tags), "hashtags": tags, "hashtags_raw": hashtags_raw,
             "hook": None, "axis": None, "rationale": None, "tag_sources": tag_sources or {}}
    if fallback:
        entry["fallback"] = True
    return entry


def ingest_captions(led: Ledger, cfg: Config, clip_id: str) -> Ledger:
    cs = read_response(cfg, "captions", clip_id, CaptionSet)
    if cs is None:
        return led                                       # pending or stale
    clip = led.clips[clip_id]
    # the clip's source language is the contract the caption must match (AUDIT H5).
    src = led.sources.get(led.moments[clip.parent_id].parent_id)
    # what surfaces did we ask for, and their per-surface curated corpus? (the request is the truth)
    requested, surface_corpus, surface_platform, content_tags = _request_surfaces(cfg, clip_id)
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
        # ...THEN the hashtags are vetted: the model's tags filtered to the reach-vetted set,
        # reach-ordered, backfilled, and HARD-capped at 4 (operator rule). Whatever it returned
        # (5-15 random words) becomes <=4 proven tags. The posted caption IS that vetted tag line.
        plat = _platform_for_surface(item.surface, surface_platform, cfg=cfg)   # AGENT-6: vet under the REQUESTED platform (#8: cfg breadcrumbs a bad key)
        tags, sources = vet_hashtags_traced(item.hashtags or _tags_in(item.caption), plat,
                            src.language if src else None, store=load_store(cfg),   # M4: live store when present
                            corpus=surface_corpus.get(item.surface),                # B1: per-persona curated pool leads (the hashtag differentiator)
                            content=content_tags, cfg=cfg)                          # per-clip content tags survive + reserve a slot (MOL-76: cfg -> brand-risk screen honors tuning.json)
        clip.meta_captions[item.surface] = _caption_entry(tags, [str(h) for h in (item.hashtags or [])], tag_sources=sources)
    answered = {item.surface for item in cs.items}
    missing = requested - answered
    # SEED-TAG FALLBACK (was: hold). The caption is hashtags-ONLY, and the model frequently returns NO
    # item for a surface — most often a SOFT REFUSAL on a clip's edgy/explicit lyrics (it sends
    # items:[] even though the output is just genre tags that never reproduce the words). The old
    # behavior held the clip on this "missing caption", which silently buried ~83% of a rap catalogue.
    # Instead synthesize the reach-vetted SEED tags + NO hook (clean clip) for each missing surface and
    # let the clip through to the operator's Review queue, logged. This is NOT F74's "silent default to
    # publish": the post is born awaiting_approval, so a human still reviews it before anything ships.
    for surface in sorted(missing):
        plat = _platform_for_surface(surface, surface_platform, cfg=cfg)   # AGENT-6: vet under the REQUESTED platform (#8: cfg breadcrumbs a bad key)
        tags, sources = vet_hashtags_traced(None, plat, src.language if src else None, store=load_store(cfg),
                            corpus=surface_corpus.get(surface),   # B1: the curated corpus leads the seed (the hashtag differentiator)
                            content=content_tags, cfg=cfg)     # ...and the clip's content tags STILL reach the line (the 83% case; MOL-76: cfg -> brand-risk screen honors tuning.json)
        clip.meta_captions[surface] = _caption_entry(tags, [], fallback=True, tag_sources=sources)
        get_logger(cfg)("captions", clip_id, "caption_fallback_seed", surface=surface)
    if held_reason:
        clip.held = True
        clip.held_reason = held_reason
        clip.state = ClipState.held                      # FIX: explicit held state, not 'rendered'
        return led
    clip.held = False
    clip.held_reason = None                              # a clean re-ingest must not keep a prior hold's reason (held=False -> held_reason=None)
    led.set_clip_state(clip_id, ClipState.captioned)
    return led
