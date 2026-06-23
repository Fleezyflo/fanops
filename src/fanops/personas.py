# src/fanops/personas.py
"""A1 — Personas as a FIRST-CLASS entity. Until now a "persona" was only a free-text Account.persona
string + tag_lean, seeded by hand from a brief doc — not editable, not reusable, not a thing you could
add an intake for. This makes a Persona a named record in 00_control/personas.json: a `voice` (the
string the pipeline reads), a `tag_lean`, a `hashtag_corpus` (the per-persona reach-vetted pool, B1),
and free-form `intake` metadata (genre/language/reference accounts, seeds B3's research). Accounts LINK
to a persona via Account.persona_id; the linked persona's voice/tag_lean HYDRATE the account in memory
at load (accounts._hydrate_from_personas), so every existing consumer (caption/moments/casting/
variant_transfer) stays byte-identical while an operator edit takes effect on the next load.

The writers mirror accounts.py exactly: a per-file flock serializes the read-modify-write (no lost
update from two concurrent Studio writers), and an atomic temp+os.replace never leaves a torn file. The
control-file boundary validates (a known tag_lean, a non-blank id) so a write never lands a record that
won't reload. Like accounts.json the file is hand-editable (indented); Personas.load raising on a
corrupt file is guarded by the fail-open hydration helper so account loading never crashes on it."""
from __future__ import annotations
import json
import os
import re
import tempfile
from contextlib import contextmanager
from typing import Optional
from pydantic import BaseModel, Field
from fanops.config import Config, FRAMING_NAMES
from fanops.errors import ControlFileError, reason as _reason
from fanops.hashtags import TAG_LEANS, _norm
from fanops.bands import PROFILE_NAMES

_CORPUS_CAP = 40                # max curated tags per persona — keeps captions/budget bounded (cap, not a target)

# The lever-engine vocabularies (the validated control surface — one lever per persona characteristic). Each
# is the WRITE boundary for its lever: add/update_persona refuses an unknown value (never write a typo that
# reloads as a silent no-op), and compose_persona_instruction renders the SET levers into the single
# instruction the casting/hook/caption prompts read. clip_profile/framing reuse the Account validators
# (bands.PROFILE_NAMES / config.FRAMING_NAMES) so a persona pins the SAME deterministic CUT an account can.
CONTENT_FOCUS = frozenset({"punchlines", "emotional", "hype", "storytelling", "visual", "bold-statement"})
ENERGY_LEVELS = frozenset({"low", "medium", "high"})
HOOK_ANGLES = frozenset({"curiosity", "challenge", "emotional", "result-first", "fomo"})
HOOK_TONES = frozenset({"aggressive", "restrained", "playful"})


class Persona(BaseModel):
    id: str                                       # stable slug (the link key on Account.persona_id)
    name: str = ""                                # operator-facing display name
    voice: str = ""                               # the persona string the pipeline reads (caption/hook/casting voice)
    tag_lean: Optional[str] = None                # persona TAG knob: tasteful|underground|bold (None -> no lean)
    hashtag_corpus: list[str] = Field(default_factory=list)   # B1: the per-persona reach-vetted pool
    intake: dict = Field(default_factory=dict)    # free-form intake (genre/language/reference accounts) — seeds B3 research
    # Lever engine: explicit per-characteristic DIRECTION that compose_persona_instruction renders into the
    # one instruction the casting/hook/caption prompts read. ADDITIVE — all empty on a legacy persona, so
    # compose returns the bare `voice` (byte-identical). Validated at the write boundary (add/update_persona).
    content_focus: list[str] = Field(default_factory=list)   # which moment KINDS to favor (casting): CONTENT_FOCUS
    energy: Optional[str] = None                  # clip energy: low|medium|high (ENERGY_LEVELS)
    hook_angle: Optional[str] = None              # on-screen hook strategy: curiosity|challenge|... (HOOK_ANGLES)
    hook_tone: Optional[str] = None               # on-screen hook tone: aggressive|restrained|playful (HOOK_TONES)
    clip_profile: Optional[str] = None            # per-account LENGTH tier (bands.PROFILE_NAMES) — hydrates onto the account
    framing: Optional[str] = None                 # per-account vertical CROP bias (config.FRAMING_NAMES) — hydrates onto the account


class Personas:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.personas: list[Persona] = []

    @classmethod
    def load(cls, cfg: Config) -> "Personas":
        r = cls(cfg)
        p = cfg.personas_path
        if p.exists():
            try:
                raw = json.loads(p.read_text())
                r.personas = [Persona(**x) for x in raw.get("personas", []) if isinstance(x, dict)]
            except Exception as e:                 # a hand-edit typo: clear one-liner, not a raw traceback
                raise ControlFileError(f"{p.name} invalid: {_reason(e)}") from e
        return r

    def get(self, pid: Optional[str]) -> Optional[Persona]:
        return next((p for p in self.personas if p.id == pid), None) if pid else None

    def all(self) -> list[Persona]:
        return list(self.personas)


def _slug(s: str) -> str:
    """A stable id from a name/handle: lowercase, drop a leading '@', non-alphanumerics -> single '-'."""
    s = (s or "").strip().lower().lstrip("@")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def compose_persona_instruction(p) -> str:
    """The lever ENGINE's composer — render a persona/account's SET levers into the SINGLE instruction
    string the pipeline's casting/hook/caption prompts read (the value interpolated as each surface's
    `persona`). PURE + duck-typed (reads .voice OR .persona, .content_focus, .energy, .hook_angle,
    .hook_tone) so it serves a Persona OR a hydrated Account. THE FIREWALL: with NO levers set it returns
    the bare voice VERBATIM, so every existing persona's payload stays byte-identical; only-levers -> the
    composed body; both -> "body. voice". clip_profile/framing are NOT in the text (they drive the
    deterministic CUT, not the prompt)."""
    voice = (getattr(p, "voice", None) or getattr(p, "persona", None) or "").strip()
    parts: list[str] = []
    cf = [s for x in (getattr(p, "content_focus", None) or []) if (s := str(x).strip())]
    if cf: parts.append("favors moments: " + ", ".join(cf))
    for label, val in (("energy", getattr(p, "energy", None)), ("hook angle", getattr(p, "hook_angle", None)),
                       ("hook tone", getattr(p, "hook_tone", None))):
        v = (val or "").strip()
        if v: parts.append(f"{label} {v}")
    body = "; ".join(parts)
    if voice and body: return f"{body}. {voice}"
    return voice or body


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
    raw = json.loads(p.read_text()) if p.exists() else {"personas": []}
    plist = raw.get("personas") if isinstance(raw, dict) else None
    if not isinstance(plist, list):
        raise ControlFileError(f"{p.name} invalid: expected a top-level 'personas' list")
    return raw, plist


def _write_atomic(p, raw: dict) -> None:
    """Persist via temp + os.replace (a unique mkstemp, same dir so replace stays atomic), so a crash
    mid-write never leaves a torn personas.json. Indented for the operator who still hand-edits."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh: fh.write(json.dumps(raw, indent=2) + "\n")
        os.replace(tmp, p)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


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


def add_persona(cfg: Config, name: str, voice: str = "", tag_lean: str = "",
                intake: Optional[dict] = None, id: str = "", *, content_focus=None,
                energy: str = "", hook_angle: str = "", hook_tone: str = "",
                clip_profile: str = "", framing: str = "") -> str:
    """Create a NEW persona atomically. The id is the given slug or one derived from `name`; rejects a
    duplicate id and a blank name (never write a record that won't reload). Validates tag_lean AND every
    lever-engine field against its vocabulary. Returns the id; raises ValueError on bad input."""
    nm = (name or "").strip()
    if not nm:
        raise ValueError("persona name is required")
    pid = _slug(id) or _slug(nm)
    if not pid:
        raise ValueError(f"could not derive a persona id from name {name!r}")
    lean = (tag_lean or "").strip().lower()
    if lean and lean not in TAG_LEANS:
        raise ValueError(f"unknown tag_lean: {tag_lean!r}")
    focus = _norm_focus(content_focus)
    energy_v = _enum_or_none(energy, ENERGY_LEVELS, "energy")
    angle_v = _enum_or_none(hook_angle, HOOK_ANGLES, "hook_angle")
    tone_v = _enum_or_none(hook_tone, HOOK_TONES, "hook_tone")
    prof_v = _enum_or_none(clip_profile, PROFILE_NAMES, "clip_profile")
    fr_v = _enum_or_none(framing, FRAMING_NAMES, "framing")
    p = cfg.personas_path
    with _personas_txn(cfg):
        raw, plist = _load_raw(p)
        if any(isinstance(d, dict) and d.get("id") == pid for d in plist):
            raise ValueError(f"duplicate persona id {pid!r} (already exists)")
        plist.append({"id": pid, "name": nm, "voice": str(voice or ""), "tag_lean": lean or None,
                      "hashtag_corpus": [], "intake": dict(intake or {}), "content_focus": focus,
                      "energy": energy_v, "hook_angle": angle_v, "hook_tone": tone_v,
                      "clip_profile": prof_v, "framing": fr_v})
        _write_atomic(p, raw)
    return pid


def update_persona(cfg: Config, pid: str, *, name=_UNSET, voice=_UNSET, tag_lean=_UNSET, intake=_UNSET,
                   content_focus=_UNSET, energy=_UNSET, hook_angle=_UNSET, hook_tone=_UNSET,
                   clip_profile=_UNSET, framing=_UNSET) -> str:
    """Edit a persona's fields atomically (the A2 edit form). Only the fields PASSED change; tag_lean=""
    CLEARS the lean (-> None) and likewise each lever clears on "". Validates tag_lean + every passed lever
    against its vocabulary BEFORE the lock (never write a typo). Unknown id -> KeyError."""
    if tag_lean is not _UNSET:
        _l = (tag_lean or "").strip().lower()
        if _l and _l not in TAG_LEANS:
            raise ValueError(f"unknown tag_lean: {tag_lean!r}")
    _focus = _norm_focus(content_focus) if content_focus is not _UNSET else _UNSET
    _energy = _enum_or_none(energy, ENERGY_LEVELS, "energy") if energy is not _UNSET else _UNSET
    _angle = _enum_or_none(hook_angle, HOOK_ANGLES, "hook_angle") if hook_angle is not _UNSET else _UNSET
    _tone = _enum_or_none(hook_tone, HOOK_TONES, "hook_tone") if hook_tone is not _UNSET else _UNSET
    _prof = _enum_or_none(clip_profile, PROFILE_NAMES, "clip_profile") if clip_profile is not _UNSET else _UNSET
    _fr = _enum_or_none(framing, FRAMING_NAMES, "framing") if framing is not _UNSET else _UNSET
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
                if tag_lean is not _UNSET: d["tag_lean"] = ((tag_lean or "").strip().lower() or None)
                if intake is not _UNSET: d["intake"] = dict(intake or {})
                if _focus is not _UNSET: d["content_focus"] = _focus
                if _energy is not _UNSET: d["energy"] = _energy
                if _angle is not _UNSET: d["hook_angle"] = _angle
                if _tone is not _UNSET: d["hook_tone"] = _tone
                if _prof is not _UNSET: d["clip_profile"] = _prof
                if _fr is not _UNSET: d["framing"] = _fr
                found = True
        if not found:
            raise KeyError(pid)
        _write_atomic(p, raw)
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
        _write_atomic(p, raw)
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
        _write_atomic(p, raw)
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
        _write_atomic(p, raw)
    return pid


def research_corpus(cfg: Config, pid: str, *, limit: int = 8) -> list[str]:
    """B3: propose the reach-best hashtags this persona doesn't yet carry — the bootstrap "research my
    corpus" step. Grounded in the reach-ranked store (own-reach + Graph trends, default-ON) PLUS the
    persona's lean flavor pool, minus its current corpus. INSTANT + budget-free: the store already encodes
    the Graph signal (refresh_store blends it), so no per-candidate Graph call is spent here. The persona's
    flavor leads, then the reach-ranked universe. Returns an ordered list of candidate tags (most-reach
    first) the operator accepts into the corpus. Unknown id -> KeyError."""
    from fanops.hashtags import vetted_menu, load_store, _LEANS   # _norm already imported at module scope
    per = Personas.load(cfg).get(pid)
    if per is None:
        raise KeyError(pid)
    have = {_norm(t) for t in per.hashtag_corpus if isinstance(t, str)}
    lean_pool = _LEANS.get((per.tag_lean or "").strip().lower(), [])     # flavor leads
    ranked = vetted_menu(load_store(cfg))                                # store (own-reach+trends) else frozen reach-order
    out: list[str] = []; seen: set[str] = set()
    for t in lean_pool + ranked:
        n = _norm(t)
        if n and n not in have and n not in seen:
            seen.add(n); out.append(n)
    return out[:limit]


def discover_corpus(cfg: Config, pid: str, *, limit: int = 8, measure_k: int = 0, get=None) -> list[dict]:
    """M3: LIVE per-persona discovery — the upgrade from research_corpus's re-rank-what-we-know to
    finding tags we have never named. Seeds the Graph co-occurrence harvest from the persona's category
    (its corpus + lean flavor pool + intake `genre`), DROPS what we already know (VETTED ∪ reach store ∪
    corpus), and returns evidence-carrying proposals [{"tag","count","host_engagement",...}] reach-relevant
    first. FAIL-OPEN: no creds / nothing fresh / any Graph error -> today's offline research_corpus re-rank,
    wrapped as evidence-less {"tag": ...} dicts so the caller has ONE shape. measure_k defaults 0 (the free
    co-occurrence COUNT is the operator's evidence; per-tag reach stays the explicit 'Check reach' action) —
    the global refresh passes measure_k>0 to gate the menu on measured reach. Unknown id -> KeyError."""
    from fanops.hashtags import load_store, _LEANS, VETTED
    from fanops.meta_graph import discover_candidates
    per = Personas.load(cfg).get(pid)
    if per is None:
        raise KeyError(pid)
    corpus = [_norm(t) for t in per.hashtag_corpus if isinstance(t, str)]
    genre_seeds = [_norm("#" + w) for w in (per.intake.get("genre") or "").split() if w.strip()]   # `or ""`: a hand-edited "genre": null must not seed "#none"
    seeds = list(dict.fromkeys(corpus + _LEANS.get((per.tag_lean or "").strip().lower(), []) + genre_seeds))
    store = load_store(cfg) or []
    known = set(VETTED) | set(store) | set(corpus)
    try:
        cands = discover_candidates(cfg, seeds, known=known, measure_k=measure_k, get=get)
    except Exception:                                    # any Graph/transport error -> offline fallback
        cands = []
    if cands:
        return cands[:limit]
    return [{"tag": t} for t in research_corpus(cfg, pid, limit=limit)]   # FAIL-OPEN to the offline re-rank


def migrate_from_accounts(cfg: Config) -> dict:
    """Lift each account's inline persona string into a first-class Persona and LINK it (set persona_id),
    so the brief-seeded personas become editable + connectable. IDEMPOTENT: an account already linked is
    skipped, and a persona id that already exists is not recreated; an account with no inline persona is
    skipped (nothing to lift). Two SEQUENTIAL transactions (create personas, then link accounts) — never
    a nested lock. Returns {created:[ids], linked:[handles]}."""
    from fanops.accounts import Accounts, link_persona
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
            add_persona(cfg, name=a.handle, voice=voice, tag_lean=(a.tag_lean or ""), id=pid)
            existing.add(pid); created.append(pid)
        link_persona(cfg, a.handle, pid)
        linked.append(a.handle)
    return {"created": created, "linked": linked}
