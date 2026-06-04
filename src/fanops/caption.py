"""Caption stage. request_captions() asks the agent for a per-surface caption set (different
wording per surface — opsec + platform fit). ingest_captions() validates each, runs the
brand-risk HOLD in BOTH English and Arabic (FIX F33), REQUIRES a caption for every requested
surface (FIX F74 — no silent default), stores clean captions keyed by the documented
'account/platform' contract (FIX F43), and advances only if nothing is held."""
from __future__ import annotations
import json
import re
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import ClipState, Platform, CaptionSet
from fanops.agentstep import write_request, read_response, request_path

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

def _guidance(cfg: Config) -> str:
    return cfg.context_path.read_text() if cfg.context_path.exists() else ""

def _surface_str(account: str, platform: Platform) -> str:
    return f"{account}/{platform.value}"                  # the documented lookup contract

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

def request_captions(led: Ledger, cfg: Config, clip_id: str,
                     surfaces: list[tuple[str, Platform]]) -> Ledger:
    clip = led.clips[clip_id]
    moment = led.moments[clip.parent_id]
    src = led.sources.get(moment.parent_id)
    payload = {
        "clip_id": clip_id,
        "transcript_excerpt": moment.transcript_excerpt,
        "language": src.language if src else None,
        "guidance": _guidance(cfg),
        "surfaces": [{"surface": _surface_str(acct, plat), "platform": plat.value}
                     for acct, plat in surfaces],
    }
    write_request(cfg, kind="captions", key=clip_id, payload=payload)
    led.set_clip_state(clip_id, ClipState.captions_requested)
    return led

def ingest_captions(led: Ledger, cfg: Config, clip_id: str) -> Ledger:
    cs = read_response(cfg, "captions", clip_id, CaptionSet)
    if cs is None:
        return led                                       # pending or stale
    clip = led.clips[clip_id]
    # the clip's source language is the contract the caption must match (AUDIT H5).
    src = led.sources.get(led.moments[clip.parent_id].parent_id)
    # what surfaces did we ask for? (the request is the source of truth for completeness)
    req = json.loads(request_path(cfg, "captions", clip_id).read_text())
    requested = {s["surface"] for s in req.get("surfaces", [])}
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
        if reason and held_reason is None:
            held_reason = reason
        clip.meta_captions[item.surface] = {"caption": item.caption, "hashtags": item.hashtags}
    answered = {item.surface for item in cs.items}
    missing = requested - answered
    if missing and held_reason is None:
        held_reason = f"missing caption for surfaces: {sorted(missing)}"
    if held_reason:
        clip.held = True
        clip.held_reason = held_reason
        clip.state = ClipState.held                      # FIX: explicit held state, not 'rendered'
        return led
    clip.held = False
    led.set_clip_state(clip_id, ClipState.captioned)
    return led
