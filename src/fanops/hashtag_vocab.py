# src/fanops/hashtag_vocab.py
"""LLM-expanded niche vocabulary for Layer A search roots (MOL-644).

Persona name/voice/niche are CONTEXT only. The model returns relative subject/scene terms for THIS
persona — never "viral" / high-reach tags. Seeds land in `00_control/hashtag_vocab.json` and fold into
`persona_terms` as EXTRA roots alongside operator `niche`. They never write the corpus; Layer A
measure + inbound-only membership (MOL-643) + play/like ranking still decide what ships.

Fail-open: missing responder, LLM errors, or empty replies leave the previous vocab (or none) untouched.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from fanops.config import Config
from fanops.controlio import write_json_atomic
from fanops.hashtag_hygiene import is_curatable
from fanops.log import get_logger

# Hard reject platform magnets / generic engagement bait — even if the model ignores the prompt.
# Structural curatability alone still admits #fyp; this list is the vocabulary-layer veto.
_PLATFORM_JUNK = frozenset({
    "fyp", "foryou", "foryoupage", "viral", "explore", "explorepage", "reels", "reel",
    "instagood", "instalike", "instadaily", "follow", "followme", "followforfollow",
    "likeforlike", "likeforfollow", "love", "beautiful", "photography", "photooftheday",
    "trending", "xyzbca", "fy",
})
# Function / vibe words the model sometimes echoes from voice prose — never useful search roots.
_VOCAB_STOP = frozenset({
    "within", "about", "there", "their", "these", "those", "where", "which", "while",
    "would", "could", "should", "being", "having", "energy", "mindset", "vibes",
    "believe", "passion", "content", "creator", "lifestyle", "aesthetic",
})

_VOCAB_CAP = 24
_SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {"type": "array", "items": {"type": "string"}, "maxItems": _VOCAB_CAP},
    },
    "required": ["terms"],
}


def claude_json(prompt: str, schema: dict, **kw) -> dict:
    """Indirection seam so tests can monkeypatch without touching fanops.llm."""
    from fanops.llm import claude_json as _real
    return _real(prompt, schema, **kw)


def _seed_token(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    t = raw.strip().lstrip("#").lower().replace("-", "").replace(" ", "")
    if not t or t in _PLATFORM_JUNK or t in _VOCAB_STOP or not is_curatable("#" + t):
        return None
    return t


def _sanitize_terms(raw) -> list[str]:
    out: list[str] = []; seen: set[str] = set()
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        t = _seed_token(item)
        if t and t not in seen:
            seen.add(t); out.append(t)
        if len(out) >= _VOCAB_CAP:
            break
    return out


def load_vocab(cfg: Config) -> dict:
    """Raw `{persona_id: {terms, expanded_at, source}}`. Absent/corrupt → {}."""
    p = cfg.hashtag_vocab_path
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_vocab(cfg: Config, data: dict) -> None:
    """Persist the whole vocab map (atomic). Callers own merge semantics."""
    write_json_atomic(cfg.hashtag_vocab_path, data)


def vocab_terms_for(cfg: Config, pid: str) -> list[str]:
    """Sanitized durable seeds for one persona id (empty when absent)."""
    row = load_vocab(cfg).get(pid) if pid else None
    if not isinstance(row, dict):
        return []
    return _sanitize_terms(row.get("terms"))


def _prompt(per) -> str:
    niche = [str(x) for x in (getattr(per, "niche", None) or []) if isinstance(x, str) and x.strip()]
    voice = (getattr(per, "voice", None) or "").strip()
    name = (getattr(per, "name", None) or getattr(per, "id", "") or "").strip()
    return (
        "You expand Instagram SEARCH ROOTS for ONE persona's content territory.\n"
        "Return relative subject / scene / format vocabulary THIS persona would actually film or talk about.\n"
        "Use the persona name, voice, and declared niche as CONTEXT only — do not copy marketing prose.\n\n"
        "HARD RULES:\n"
        "- Do NOT propose viral, high-reach, trending, or growth-hack hashtags.\n"
        "- Do NOT propose platform magnets (#fyp, #foryou, #reels, #explore, #love, #instagood, …).\n"
        "- Do NOT propose abstract vibe words (believe, energy, mindset, vibes) or lever enums "
        "(punchlines, curiosity, controversy).\n"
        "- Prefer concrete nouns a caption would tag: scenes, subgenres, cities/scenes, formats, craft terms.\n"
        f"- At most {_VOCAB_CAP} short tokens (no # prefix required).\n\n"
        f"Persona name: {name}\n"
        f"Voice (context): {voice[:800]}\n"
        f"Declared niche roots: {', '.join(niche) or '(none)'}\n"
    )


def expand_persona_vocab(cfg: Config, pid: str, *, model=None) -> dict:
    """Ask the LLM for one persona's vocab seeds and durable-write them. Fail-open on errors."""
    from fanops.personas import Personas
    try:
        per = Personas.load(cfg).get(pid)
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "reason": f"personas: {str(exc)[:120]}"}
    if per is None:
        return {"ok": False, "reason": "unknown_persona"}
    try:
        dec = claude_json(_prompt(per), _SCHEMA, model=model, timeout=120.0)
    except Exception as exc:                                 # noqa: BLE001 — fail-open
        get_logger(cfg)("hashtag_vocab", pid, "expand_error", err=f"{type(exc).__name__}: {str(exc)[:120]}")
        return {"ok": False, "reason": f"llm: {type(exc).__name__}"}
    terms = _sanitize_terms((dec or {}).get("terms") if isinstance(dec, dict) else None)
    if not terms:
        return {"ok": False, "reason": "empty_after_sanitize"}
    data = load_vocab(cfg)
    data[pid] = {"terms": terms, "expanded_at": datetime.now(timezone.utc).isoformat(), "source": "llm"}
    write_vocab(cfg, data)
    return {"ok": True, "terms": terms, "n": len(terms)}


def expand_vocab_if_due(cfg: Config, *, max_age_s: int = 43200, model=None) -> dict:
    """12h tick hook: expand vocab for posting personas when FANOPS_RESPONDER=llm. Never raises."""
    marker = cfg.control / ".hashtag_vocab_refresh.json"
    try:
        if cfg.responder_mode != "llm":
            return {"refreshed": False, "reason": "responder_manual"}
        if marker.exists() and (time.time() - marker.stat().st_mtime) < max_age_s:
            return {"refreshed": False, "reason": "fresh"}
        from fanops.fanops_hashtags import _posting_personas
        personas = _posting_personas(cfg)
        ok_n = 0; fail_n = 0
        for per in personas:
            r = expand_persona_vocab(cfg, per.id, model=model)
            if r.get("ok"):
                ok_n += 1
            else:
                fail_n += 1
        write_json_atomic(marker, {"ts": datetime.now(timezone.utc).isoformat(),
                                   "ok": ok_n, "fail": fail_n, "personas": len(personas)})
        return {"refreshed": True, "ok": ok_n, "fail": fail_n, "personas": len(personas)}
    except Exception as exc:                                 # noqa: BLE001
        get_logger(cfg)("hashtag_vocab", "-", "refresh_error", err=f"{type(exc).__name__}: {str(exc)[:120]}")
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}
