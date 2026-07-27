# src/fanops/persona_research.py
"""Layer B — a persona's hashtag corpus, DERIVED. Zero network: a pure function of the measurement cache
Layer A already bought (fanops_hashtags.refresh_store) and the persona's own description.

  terms(persona)  ->  anchors  ->  aligned pool  ->  top `corpus_target` by the platform metric

`persona_terms` is also what roots discovery in Layer A, so the two halves agree on what a persona IS by
construction rather than by convention. The corpus is never read back as an input anywhere.

The corpus is OUTPUT, not curated state: every tick recomputes it. There are no pins, no operator
add/remove, and no proposal flows to reconcile against — those made a derived value hand-tended, and left
the live system with corpora that were ~90% auto-filled tags carrying `reach: null`, i.e. promoted with no
measurement at all. Admission now needs a real, unexpired platform measurement, so a corpus is either
evidence-backed or honestly SHORT."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fanops.config import Config
from fanops.hashtags import METRIC_FIELD, _norm, _STOPWORDS, _WORD, load_measurements
from fanops.hashtag_hygiene import is_curatable

_EVIDENCE_MAX_AGE_DAYS = 90       # older than this is history, not evidence — a dead measurement cannot curate


def _registry(cfg: Config):
    """The persona registry, imported lazily. `fanops.personas` is the FACADE that re-exports this module,
    so a module-level import here only resolves when personas.py is imported first — importing this module
    directly (as tests and fanops_hashtags do) would hit a partially-initialized facade."""
    from fanops.personas import Personas
    return Personas.load(cfg)


def persona_terms(per) -> list[str]:
    """The discovery/alignment vocabulary for ONE persona, from its own words only: `intake.genre` first
    (the explicit niche declaration), then the voice — adjacent-word CONCATENATIONS before the single
    words, because that is how hashtags are actually written ("syrian rapper" -> #syrianrapper) — then the
    name, whole-name concatenation first.

    Pure, deterministic, and CORPUS-BLIND. Specificity leads so the narrowest, most on-niche searches
    happen before a throttle can end a pass. Reuses the tokenizer the clip-content signal uses
    (`hashtags._WORD` / `_STOPWORDS`), so "a token" means the same thing everywhere."""
    out: list[str] = []; seen: set[str] = set()

    def add(t: str) -> None:
        t = (t or "").strip().lower()
        if t and t not in seen and t not in _STOPWORDS:
            seen.add(t); out.append(t)

    intake = per.intake if isinstance(getattr(per, "intake", None), dict) else {}
    for t in _WORD.findall(str(intake.get("genre") or "").lower()):
        add(t)
    words = _WORD.findall(str(getattr(per, "voice", "") or "").lower())
    for a, b in zip(words, words[1:]):
        if a not in _STOPWORDS and b not in _STOPWORDS:
            add(a + b)
    for t in words:
        add(t)
    nm = _WORD.findall(str(getattr(per, "name", "") or "").lower())
    if len(nm) > 1:
        add("".join(nm))
    for t in nm:
        add(t)
    return out


def _is_evidence(rec: dict, *, now: datetime | None = None) -> bool:
    """True when a cache record is a real, unexpired PLATFORM measurement — the admission predicate.
    Demands the platform's own field (`METRIC_FIELD`, positive), a parseable `measured_at`, and freshness.
    A legacy record (the invented likes+comments sum under a `reach` key) carries no METRIC_FIELD and fails
    here exactly as it fails the cache reader: an unprovenanced number must never curate."""
    if not isinstance(rec, dict):
        return False
    try:
        if float(rec.get(METRIC_FIELD) or 0) <= 0:
            return False
        ts = datetime.fromisoformat(rec["measured_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - ts <= timedelta(days=_EVIDENCE_MAX_AGE_DAYS)


def _aligned_pool(per, cache: dict[str, dict], *, now=None) -> list[tuple[str, float, str]]:
    """This persona's candidate pool from the cache, as (tag, metric, from-anchor), ranked by the platform
    metric desc (ties by tag, so a derivation is reproducible).

    Alignment is a binary membership test, never a rank key: a tag qualifies if it IS one of the persona's
    anchors, or if the harvest recorded it co-occurring with one. Relevance therefore comes from the
    platform's own co-occurrence data rooted at the persona's description — not from a similarity score we
    invented. Versatility comes free: the posts winning in a niche right now carry the broad tags too."""
    anchors = {_norm("#" + t) for t in persona_terms(per)}
    anchors.discard("#")
    out: list[tuple[str, float, str]] = []
    for tag, rec in cache.items():
        if not _is_evidence(rec, now=now) or not is_curatable(tag):
            continue
        if tag in anchors:
            src = tag
        else:
            frm = rec.get("from") if isinstance(rec.get("from"), dict) else {}
            hits = [a for a in frm if a in anchors]
            if not hits:
                continue
            src = max(hits, key=lambda a: (frm.get(a) or 0, a))
        out.append((tag, float(rec[METRIC_FIELD]), src))
    out.sort(key=lambda r: (-r[1], r[0]))
    return out


def derive_corpus(cfg: Config, pid: str, *, now=None) -> dict:
    """Recompute ONE persona's corpus from the cache. Zero network. Returns {changed, added, removed}.

    The corpus becomes the top `cfg.corpus_target` of the aligned, measured, structurally-clean pool.
    Nothing pads it: with thin evidence the corpus is short, and with NO evidence (a cold cache, or the
    platform unreachable) the previous derived corpus stands untouched — an outage must not empty a
    working persona. Unknown id -> {changed: False}."""
    from fanops.persona_store import apply_auto_corpus
    per = _registry(cfg).get(pid)
    if per is None:
        return {"changed": False, "reason": "unknown_persona"}
    corpus = [_norm(t) for t in (per.hashtag_corpus or []) if isinstance(t, str) and _norm(t)]
    pool = _aligned_pool(per, load_measurements(cfg), now=now)
    if not pool:
        return {"changed": False}                          # outage / cold cache: hold, never empty
    stamp = (now.isoformat() if isinstance(now, datetime) else None) or datetime.now(timezone.utc).isoformat()
    chosen = pool[:max(1, cfg.corpus_target)]
    final = [t for t, _v, _s in chosen]
    if final == corpus:
        return {"changed": False}
    meta = {t: {METRIC_FIELD: v, "measured_at": stamp, "from": s} for t, v, s in chosen}
    apply_auto_corpus(cfg, pid, tags=final, meta=meta)
    return {"changed": True, "added": [t for t in final if t not in set(corpus)],
            "removed": [t for t in corpus if t not in set(final)]}


def derived_report(cfg: Config, pid: str, *, top_n: int = 8, now=None) -> dict:
    """A read-only projection of what a persona's niche looks like in the cache right now — the terms it
    searches on, how many measured tags align with it, and the strongest few. Zero network; feeds
    `fanops hashtags discover` and the Studio panel. Unknown id -> empty shape."""
    per = _registry(cfg).get(pid)
    if per is None:
        return {"terms": [], "measured": 0, "top": []}
    pool = _aligned_pool(per, load_measurements(cfg), now=now)
    return {"terms": persona_terms(per), "measured": len(pool),
            "top": [(t, v) for t, v, _s in pool[:top_n]]}


def _persona_row(cfg: Config, pid: str) -> dict | None:
    """The RAW personas.json row for one persona (so a reader can see fields the model doesn't carry,
    e.g. `hashtag_corpus_meta`). None when absent."""
    from fanops.persona_store import _load_raw
    _, plist = _load_raw(cfg.personas_path)
    return next((d for d in plist if isinstance(d, dict) and d.get("id") == pid), None)


def refresh_corpora_if_due(cfg: Config, *, max_age_s: int = 43200, now=None) -> dict:
    """The run-loop hook: re-derive every persona's corpus at most once per `max_age_s` (default 12h),
    throttled by .corpora_refresh.json's mtime. UNCONDITIONAL — there is no kill switch, because a
    routinely re-derived corpus IS the system's behaviour, not an opt-in (a `FANOPS_CORPUS_AUTO=0` line in
    the live .env once froze every persona at its seed tags for nine days). FAIL-OPEN: never raises."""
    import time
    from fanops.controlio import write_json_atomic
    from fanops.errors import ControlFileError, fail_open
    from fanops.log import get_logger
    marker = cfg.control / ".corpora_refresh.json"
    try:
        if marker.exists() and (time.time() - marker.stat().st_mtime) < max_age_s:
            return {"refreshed": False, "reason": "fresh"}
        try:
            personas = _registry(cfg).all()
        except ControlFileError as e:
            return {"refreshed": False, "aborted": "corrupt_personas", "reason": str(e)}
        changed = 0; added_n = 0; removed_n = 0
        for per in personas:
            r = {"changed": False}
            with fail_open(f"persona_research.derive_corpus.{per.id}"):
                r = derive_corpus(cfg, per.id, now=now)
            if r.get("changed"):
                changed += 1; added_n += len(r.get("added") or []); removed_n += len(r.get("removed") or [])
        write_json_atomic(marker, {"ts": datetime.now(timezone.utc).isoformat(), "personas": len(personas),
                                   "changed": changed, "added": added_n, "removed": removed_n})
        return {"refreshed": True, "personas": len(personas), "changed": changed,
                "added": added_n, "removed": removed_n}
    except Exception as exc:                               # noqa: BLE001 — the tick must never die here
        get_logger(cfg)("corpus", "-", "refresh_error", err=f"{type(exc).__name__}: {str(exc)[:120]}")
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}
