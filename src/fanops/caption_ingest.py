"""Caption ingest helpers: brand-risk, request parsing, meta_caption entry shape."""
from __future__ import annotations
import json
import re
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Platform, PostState
from fanops.agentstep import request_path
from fanops.hashtags import CAPTION_TAG_RE, norm_tag

_TAG_RE = re.compile(r"#\S+")

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


def _tags_in(caption: str | None) -> list[str]:
    """Hashtags found inside a caption line (the model's tags live in the array AND the caption
    text); used as the fallback when the structured `hashtags` array is empty."""
    return _TAG_RE.findall(caption or "")


def is_tags_only_caption(caption: str, tags=None) -> bool:
    """True when `caption` is empty/whitespace, or only hashtags + leftover punctuation.

    After stripping `hashtags.CAPTION_TAG_RE` matches (and any explicit `tags` tokens) plus leftover
    punctuation/whitespace, nothing remains → True. A sentence plus tags → False."""
    text = caption or ""
    if not text.strip():
        return True
    text = CAPTION_TAG_RE.sub("", text)
    if tags:
        for raw in tags:
            tok = str(raw).strip() if raw is not None else ""
            if tok:
                text = text.replace(tok, "")
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE) == ""


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


def _request_surfaces(cfg: Config, clip_id: str) -> tuple[set, dict, dict, dict, list | None]:
    """The crosspost request is the source of truth for completeness: which surfaces were ASKED for,
    the per-surface REQUESTED platform (AGENT-6 — the vetting truth), and the per-surface
    hashtag_store (the source lock). corpus/content_tags are still parsed for shape stability
    but ingest no longer uses them as the caption menu.
    Returns (requested, surface_corpus, surface_platform, surface_store, content_tags). Pure read."""
    req = json.loads(request_path(cfg, "captions", clip_id).read_text())
    surfaces = req.get("surfaces", [])
    requested = {s["surface"] for s in surfaces}
    surface_corpus = {s["surface"]: s.get("corpus") for s in surfaces}
    surface_platform = {s["surface"]: s.get("platform") for s in surfaces}   # AGENT-6: the REQUESTED platform (truth)
    surface_store = {s["surface"]: s.get("hashtag_store") for s in surfaces}  # source lock (HV1-PR3)
    raw = req.get("content_tags")
    content_tags = [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else None
    return requested, surface_corpus, surface_platform, surface_store, content_tags


def _platform_for_surface(surface: str, surface_platform: dict) -> Platform:
    """AGENT-6 / MOL-168: the platform we ASKED to caption (from the request record), not a re-parse of
    the model's echoed surface string. A mangled model surface can no longer vet/cap under the wrong
    platform's discovery set. Absent or invalid platform -> visible malformed-request error."""
    p = surface_platform.get(surface)
    if not p:
        raise ValueError(f"caption request missing platform for {surface!r}")
    try:
        return Platform(p)
    except ValueError:
        raise ValueError(f"caption request has invalid platform {p!r} for {surface!r}") from None


def _caption_entry(tags: list, hashtags_raw: list, *, caption: str, fallback: bool = False, tag_sources: dict | None = None) -> dict:
    """One surface's stored meta_captions entry. `caption` is the stripped model sentence, never the
    joined tag line. `hashtags` is the vetted <=4 list; hashtags_raw keeps the model's RAW picks
    verbatim (Studio shows picked-vs-vetted; display-only). hook/axis/rationale stay None (AGENT-7;
    the moment gate owns m.hook). They stay on the persisted entry as the dormant variant-A/B
    contract (variant_amplify/digest read entry.get("hook"); crosspost reads cap.get("axis")).
    `fallback` is not a license to manufacture caption = join(tags).
    `tag_sources` is the per-tag provenance ({tag: source}) — every shipped tag traces to a real
    signal (content|corpus|region|graph-reach|discovery|genre-floor); Review renders it. Absent -> {}."""
    entry = {"caption": (caption or "").strip(), "hashtags": tags, "hashtags_raw": hashtags_raw,
             "hook": None, "axis": None, "rationale": None, "tag_sources": tag_sources or {}}
    if fallback:
        entry["fallback"] = True
    return entry


def _recent_tags(led: Ledger, handle: str, *, n: int = 1) -> list[str]:
    """The last n non-rejected posts for `handle`, newest first — ordered-dedup union of their hashtags."""
    posts = [p for p in led.posts_of_account(handle) if p.state is not PostState.rejected]
    posts.sort(key=lambda p: (p.created_at or p.scheduled_time or ""), reverse=True)
    out: list[str] = []; seen: set[str] = set()
    for p in posts[:n]:
        for t in (p.hashtags or []):
            h = norm_tag(t) if isinstance(t, str) else ""
            if h and h not in seen: seen.add(h); out.append(h)
    return out
