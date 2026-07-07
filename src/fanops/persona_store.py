# src/fanops/persona_store.py
"""Persona WRITERS + the account->persona migration (extracted from personas.py, audit #6 — behavior
byte-identical). Every mutator mirrors accounts.py exactly: a per-file flock serializes the
read-modify-write (no lost update from two concurrent Studio writers) and an atomic temp+os.replace
never leaves a torn file. The validators are the WRITE boundary — a typo'd lever raises BEFORE the lock
so the file never lands a record that won't reload. All names are re-exported from fanops.personas."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from fanops.config import Config
from fanops.hashtags import _norm
from fanops.controlio import load_raw_list, write_json_atomic   # shared atomic control-file IO
from fanops.personas import (CONTENT_FOCUS, SELECTION_SCOPE_LEVELS, HOOK_ANGLES, Personas, _slug)

_CORPUS_CAP = 40                # max curated tags per persona — keeps captions/budget bounded (cap, not a target)
_BAKED_FILE = "baked_personas.json"


def _baked_path() -> Path:
    return Path(__file__).resolve().parent / "data" / _BAKED_FILE


def _persona_dict(p) -> dict:
    """Serialize a Persona (or baked record) to the personas.json row shape."""
    return {"id": p.id, "name": p.name or "", "voice": p.voice or "",
            "hashtag_corpus": list(p.hashtag_corpus or []), "intake": dict(p.intake or {}),
            "content_focus": list(p.content_focus or []), "selection_scope": p.selection_scope,
            "hook_angle": p.hook_angle}


def baked_personas() -> list:
    """The shipped archetype presets (package seed data). Validated at load; empty when the file is absent."""
    from fanops.personas import Persona
    p = _baked_path()
    if not p.exists():
        return []
    import json
    raw = json.loads(p.read_text())
    out: list = []
    for x in raw.get("personas", []):
        if not isinstance(x, dict) or not x.get("id"): continue
        focus = _norm_focus(x.get("content_focus"))
        scope_v = _enum_or_none(x.get("selection_scope", ""), SELECTION_SCOPE_LEVELS, "selection_scope")
        angle_v = _enum_or_none(x.get("hook_angle", ""), HOOK_ANGLES, "hook_angle")
        corpus = [_norm(t) for t in (x.get("hashtag_corpus") or []) if _norm(t)]
        out.append(Persona(id=str(x["id"]), name=str(x.get("name") or ""), voice=str(x.get("voice") or ""),
                           content_focus=focus, selection_scope=scope_v, hook_angle=angle_v,
                           hashtag_corpus=corpus, intake=dict(x.get("intake") or {})))
    return out


def ensure_baked_personas(cfg: Config) -> list[str]:
    """Additive seed: write any baked archetype whose id is not yet in personas.json. Idempotent."""
    baked = baked_personas()
    if not baked:
        return []
    added: list[str] = []
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        existing = {d.get("id") for d in plist if isinstance(d, dict)}
        for per in baked:
            if per.id in existing: continue
            plist.append(_persona_dict(per)); existing.add(per.id); added.append(per.id)
        if added:
            write_json_atomic(p, raw)
    return added


def _enum_or_none(v, names, label) -> Optional[str]:
    """Normalize an optional enum lever to lowercase-or-None; raise on an unknown non-empty value (the write
    boundary — never persist a lever that won't reload / would be a silent typo)."""
    v = (v or "").strip().lower()
    if v and v not in names:
        raise ValueError(f"unknown {label}: {v!r}")
    return v or None


def _norm_focus(content_focus) -> list[str]:
    """Normalize + validate content_focus (the multi-select moment-kind lever): lowercase, deduped, each in
    CONTENT_FOCUS. A None/non-list -> []. An unknown kind raises (mirrors the enum levers)."""
    seq = content_focus if isinstance(content_focus, (list, tuple)) else []
    out: list[str] = []; seen: set[str] = set()
    for c in seq:
        s = str(c).strip().lower()
        if not s or s in seen: continue
        if s not in CONTENT_FOCUS:
            raise ValueError(f"unknown content_focus: {s!r}")
        seen.add(s); out.append(s)
    return out


def _load_raw(p) -> tuple[dict, list]:
    """personas.json as the RAW dict (absent -> empty) + its list. Mutating the raw dict (not
    Persona.model_dump) preserves unknown/future fields and sibling records exactly, like accounts.py."""
    return load_raw_list(p, "personas")


@contextmanager
def _personas_txn(cfg: Config):
    """Serialize a mutator's read-modify-write under cfg.personas_lock_path (reuses the proven ledger
    flock; lazy import avoids a module-load cycle). mkdir the control dir first so a first-ever write on
    a fresh root can open the lock file."""
    from fanops.ledger import _file_lock
    cfg.personas_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(cfg.personas_lock_path):
        yield


_UNSET = object()


def add_persona(cfg: Config, name: str, voice: str = "",
                intake: Optional[dict] = None, id: str = "", *, content_focus=None,
                selection_scope: str = "", hook_angle: str = "") -> str:
    """Create a NEW persona atomically. The id is the given slug or one derived from `name`; rejects a
    duplicate id and a blank name (never write a record that won't reload). Validates every lever-engine
    field against its vocabulary. Returns the id; raises ValueError on bad input. (M3: tag_lean retired —
    hashtag_corpus is the hashtag differentiator; the per-persona clip_profile/framing PINS retired — the
    cut LENGTH + FRAMING derive from content_focus.)"""
    nm = (name or "").strip()
    if not nm:
        raise ValueError("persona name is required")
    pid = _slug(id) or _slug(nm)
    if not pid:
        raise ValueError(f"could not derive a persona id from name {name!r}")
    focus = _norm_focus(content_focus)
    scope_v = _enum_or_none(selection_scope, SELECTION_SCOPE_LEVELS, "selection_scope")
    angle_v = _enum_or_none(hook_angle, HOOK_ANGLES, "hook_angle")
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        if any(isinstance(d, dict) and d.get("id") == pid for d in plist):
            raise ValueError(f"duplicate persona id {pid!r} (already exists)")
        plist.append({"id": pid, "name": nm, "voice": str(voice or ""),
                      "hashtag_corpus": [], "intake": dict(intake or {}), "content_focus": focus,
                      "selection_scope": scope_v, "hook_angle": angle_v})
        write_json_atomic(p, raw)
    return pid


def update_persona(cfg: Config, pid: str, *, name=_UNSET, voice=_UNSET, intake=_UNSET,
                   content_focus=_UNSET, selection_scope=_UNSET, hook_angle=_UNSET) -> str:
    """Edit a persona's fields atomically (the A2 edit form). Only the fields PASSED change; each lever
    clears on "". Validates every passed lever against its vocabulary BEFORE the lock (never write a typo).
    Unknown id -> KeyError. (M3: tag_lean, the clip_profile/framing pins, and the directive overrides retired.)"""
    _focus = _norm_focus(content_focus) if content_focus is not _UNSET else _UNSET
    _scope = _enum_or_none(selection_scope, SELECTION_SCOPE_LEVELS, "selection_scope") if selection_scope is not _UNSET else _UNSET
    _angle = _enum_or_none(hook_angle, HOOK_ANGLES, "hook_angle") if hook_angle is not _UNSET else _UNSET
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        found = False
        for d in plist:
            if isinstance(d, dict) and d.get("id") == pid:
                if name is not _UNSET:
                    _nm = str(name).strip()
                    if not _nm: raise ValueError("persona name cannot be blank")
                    d["name"] = _nm
                if voice is not _UNSET: d["voice"] = str(voice or "")
                if intake is not _UNSET: d["intake"] = dict(intake or {})
                if _focus is not _UNSET: d["content_focus"] = _focus
                if _scope is not _UNSET: d["selection_scope"] = _scope
                if _angle is not _UNSET: d["hook_angle"] = _angle
                found = True
        if not found:
            raise KeyError(pid)
        write_json_atomic(p, raw)
    return pid


def add_corpus_tag(cfg: Config, pid: str, tag: str) -> str:
    """Add ONE hashtag to a persona's curated corpus atomically — normalized (#prefix, lowercase),
    deduped, capped at _CORPUS_CAP. Empty tag -> ValueError. Unknown id -> KeyError."""
    h = _norm(tag)
    if not h:
        raise ValueError("empty hashtag")
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        found = False
        for d in plist:
            if isinstance(d, dict) and d.get("id") == pid:
                cur = d.get("hashtag_corpus") if isinstance(d.get("hashtag_corpus"), list) else []
                out: list[str] = []; seen: set[str] = set()
                for t in list(cur) + [h]:
                    n = _norm(t) if isinstance(t, str) else ""
                    if n and n not in seen: seen.add(n); out.append(n)
                # Refuse a NEW tag past the cap rather than SILENTLY dropping it (an existing tag just
                # reorders/dedupes -> never grows past the cap, so it stays a clean no-op).
                if len(out) > _CORPUS_CAP:
                    raise ValueError(f"corpus full ({_CORPUS_CAP} tags) — remove one before adding {h}")
                d["hashtag_corpus"] = out
                found = True
        if not found:
            raise KeyError(pid)
        write_json_atomic(p, raw)
    return pid


def remove_corpus_tag(cfg: Config, pid: str, tag: str) -> str:
    """Remove ONE hashtag from a persona's corpus atomically (normalization-insensitive). Unknown id ->
    KeyError; a tag not present is a no-op."""
    h = _norm(tag)
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        found = False
        for d in plist:
            if isinstance(d, dict) and d.get("id") == pid:
                cur = d.get("hashtag_corpus") if isinstance(d.get("hashtag_corpus"), list) else []
                d["hashtag_corpus"] = [t for t in cur if isinstance(t, str) and _norm(t) != h]
                found = True
        if not found:
            raise KeyError(pid)
        write_json_atomic(p, raw)
    return pid


def delete_persona(cfg: Config, pid: str) -> str:
    """Remove a persona atomically. Drops only the matching record; preserves siblings + unknown fields.
    Unknown id -> KeyError. (Accounts still linked keep the dangling id; load hydration falls open to
    their inline persona — never crashes.)"""
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        kept = [d for d in plist if not (isinstance(d, dict) and d.get("id") == pid)]
        if len(kept) == len(plist):
            raise KeyError(pid)
        raw["personas"] = kept
        write_json_atomic(p, raw)
    return pid



def link_personas_by_voice(cfg: Config) -> list[str]:
    """Link accounts whose inline persona voice EXACTLY matches a first-class Persona record (idempotent).
    Skips accounts that already carry persona_id. Returns the handles linked. Does NOT create personas."""
    from fanops.accounts import Accounts, link_persona
    reg = Personas.load(cfg)
    linked: list[str] = []
    for a in Accounts.load(cfg).accounts:
        if (a.persona_id or "").strip():
            continue
        voice = (a.persona or "").strip()
        if not voice:
            continue
        per = next((p for p in reg.all() if (p.voice or "").strip() == voice), None)
        if per is None:
            continue
        link_persona(cfg, a.handle, per.id)
        linked.append(a.handle)
    return linked

def migrate_from_accounts(cfg: Config) -> dict:
    """Lift each account's inline persona string into a first-class Persona and LINK it (set persona_id),
    so the brief-seeded personas become editable + connectable. IDEMPOTENT: an account already linked is
    skipped, and a persona id that already exists is not recreated; an account with no inline persona is
    skipped (nothing to lift). Two SEQUENTIAL transactions (create personas, then link accounts) — never
    a nested lock. Returns {created:[ids], linked:[handles]}."""
    from fanops.accounts import Accounts, link_persona
    voice_linked = link_personas_by_voice(cfg)                 # match brief-seeded inline voices to existing Personas
    accts = Accounts.load(cfg)
    existing = {p.id for p in Personas.load(cfg).all()}
    created: list[str] = []; linked: list[str] = []
    for a in accts.accounts:
        if getattr(a, "persona_id", None):
            continue
        voice = (a.persona or "").strip()
        if not voice:
            continue
        pid = _slug(a.handle)
        if not pid:
            continue                                 # a handle with no usable slug (e.g. "@@@") -> never a false empty link
        if pid not in existing:
            add_persona(cfg, name=a.handle, voice=voice, id=pid)   # M3: tag_lean retired; corpus is curated separately
            existing.add(pid); created.append(pid)
        link_persona(cfg, a.handle, pid)
        linked.append(a.handle)
    return {"created": created, "linked": linked, "voice_linked": voice_linked}
