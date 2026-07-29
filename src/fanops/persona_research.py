# src/fanops/persona_research.py
"""Layer B — a persona's hashtag corpus, DERIVED. Zero network: a pure function of the measurement cache
Layer A already bought (fanops_hashtags.refresh_store) and the persona's own UI surface.

  terms  ->  anchors  ->  relatedness candidates  ->  top `corpus_target` by metric (MOL-665)

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
from fanops.hashtags import (RECORD_NUM_FIELDS, TOP_SAMPLE_N, _norm, _num, has_evidence,
                             load_measurements, size_rank_key, tag_size)
from fanops.hashtag_hygiene import is_curatable

_EVIDENCE_MAX_AGE_DAYS = 90       # older than this is history, not evidence — a dead measurement cannot curate
TOP_GRID_N = TOP_SAMPLE_N         # density denominator IS the real Top sample — one constant, no drift
# Relatedness → candidate; numbers rank members (MOL-665). Magnets are classified, never vetoed.
_NORMAL_HITS = 2                  # non-anchor relatedness: inbound_hits >= 2 OR n_roots >= 2
# `is_magnet` / `_MAGNET_BODIES` are GONE with the soft lane (MOL-692). A generic magnet (#fyp, #love,
# #viral) is category-scale by definition, so `is_category` already forces it through the multi-root bar —
# the separate classifier guarded nothing that CATEGORY_MEDIA_FLOOR does not.
# A CATEGORY tag (#bars, #remix, #hiphopculture) carries platform-scale post volume: it co-occurs with any
# niche by sheer size, so the normal relatedness bar is cheap for it to clear. Floor = the live cache's p90
# media_count (measured 2026-07-29: median 77k, p90 5.25M). Volume is Instagram's own `media_count`, so this
# is a measured property, not a curated ban list — absent media_count fails OPEN (never a category).
CATEGORY_MEDIA_FLOOR = 5_000_000.0
# Non-niche candidates need a real volume floor (MOL-714). Unmeasured / sub-1k tags padded live
# corpora alphabetically; niche seats stay unconditional and are exempt.
MIN_MEDIA_FLOOR = 1_000.0


def inbound_hits(rec: dict, anchors: set[str]) -> int:
    frm = rec.get("from") if isinstance(rec.get("from"), dict) else {}
    return int(sum(frm.get(a) or 0 for a in anchors if (frm.get(a) or 0) > 0))


def n_roots(rec: dict, anchors: set[str]) -> int:
    frm = rec.get("from") if isinstance(rec.get("from"), dict) else {}
    return sum(1 for a in anchors if (frm.get(a) or 0) > 0)


def density(rec: dict, anchors: set[str]) -> float:
    return inbound_hits(rec, anchors) / float(TOP_GRID_N)


def is_category(rec: dict) -> bool:
    """True when Instagram's own `media_count` puts this tag at platform-category scale (MOL-685).
    Absent / unparseable volume -> False: an unmeasured tag is never demoted on a guess."""
    v = rec.get("media_count") if isinstance(rec, dict) else None
    return isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) >= CATEGORY_MEDIA_FLOOR


def _is_candidate(tag: str, rec: dict, relatedness_anchors: set[str], *,
                  niche_anchors: set[str] | None = None) -> bool:
    """Relatedness gate (MOL-665/MOL-714). Unconditional seat = operator niche ONLY. Every non-niche
    tag needs media_count ≥ MIN_MEDIA_FLOOR and measured relatedness against `relatedness_anchors`
    (niche ∪ unique-to-persona LLM vocab). Shared / sibling-owned LLM terms are Layer A search-only
    and must not appear in relatedness_anchors. A CATEGORY-scale tag additionally needs multi-root
    relatedness (MOL-685). When `niche_anchors` is omitted it equals `relatedness_anchors` (unit-test
    compat for the relatedness arithmetic)."""
    niche = niche_anchors if niche_anchors is not None else relatedness_anchors
    if tag in niche:
        return True                                         # unconditional niche seat
    mc = rec.get("media_count") if isinstance(rec, dict) else None
    if not (isinstance(mc, (int, float)) and not isinstance(mc, bool) and float(mc) >= MIN_MEDIA_FLOOR):
        return False                                        # unmeasured / sub-floor non-niche refused
    hits = inbound_hits(rec, relatedness_anchors)
    roots = n_roots(rec, relatedness_anchors)
    if hits <= 0:
        return False
    if is_category(rec):
        return hits >= _NORMAL_HITS and roots >= 2      # volume alone must not buy a seat
    return hits >= _NORMAL_HITS or roots >= 2


def _registry(cfg: Config):
    """The persona registry, imported lazily. `fanops.personas` is the FACADE that re-exports this module,
    so a module-level import here only resolves when personas.py is imported first — importing this module
    directly (as tests and fanops_hashtags do) would hit a partially-initialized facade."""
    from fanops.personas import Personas
    return Personas.load(cfg)


def _seed_token(raw) -> str | None:
    """Normalize one candidate to a tag body, or None if empty / not structurally curatable."""
    if not isinstance(raw, str):
        return None
    t = raw.strip().lstrip("#").lower().replace("-", "").replace(" ", "")
    if not t or not is_curatable("#" + t):
        return None
    return t


def niche_terms(per) -> list[str]:
    """Operator-declared niche bodies (Layer B unconditional seats + relatedness). Order preserved."""
    out: list[str] = []; seen: set[str] = set()
    for n in getattr(per, "niche", None) or []:
        t = _seed_token(n)
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def persona_terms(per, cfg: Config | None = None) -> list[str]:
    """Layer A search roots: operator `niche` first, then durable LLM vocab when `cfg` is given (MOL-644).

    Stays WIDE on purpose — Layer A still discovers and measures generated roots, including ones that
    are Layer B search-only (shared / sibling-owned). Layer B relatedness uses `relatedness_terms`.
    Voice / content_focus / hook_angle / intensity stay on the persona for caption+hook directives —
    they are NOT Instagram search roots (MOL-637)."""
    out: list[str] = list(niche_terms(per)); seen: set[str] = set(out)
    if cfg is not None:
        from fanops.hashtag_vocab import vocab_terms_for
        for t in vocab_terms_for(cfg, getattr(per, "id", "") or ""):
            if t and t not in seen:
                seen.add(t); out.append(t)
    return out


def relatedness_terms(per, cfg: Config | None = None) -> list[str]:
    """Layer B relatedness roots (MOL-714): declared niche ∪ LLM vocab unique to this persona.

    A vocab term is unique iff it appears in this persona's durable vocab and NOT in any other
    posting persona's niche or vocab. Shared LLM terms and sibling-declared terms are Layer A
    search-only — they may still be scraped via wide `persona_terms`, but cannot attribute a tag
    into any corpus. Operator niche always wins a seat in this set. Without `cfg`, niche only."""
    out: list[str] = list(niche_terms(per)); seen: set[str] = set(out)
    if cfg is None:
        return out
    from fanops.hashtag_vocab import vocab_terms_for
    try:
        from fanops.fanops_hashtags import _posting_personas
        siblings = _posting_personas(cfg)
    except Exception:                                        # noqa: BLE001 — fail closed to niche-only extras
        return out
    pid = getattr(per, "id", "") or ""
    owned: set[str] = set()
    for sibling in siblings:
        sid = getattr(sibling, "id", "") or ""
        if not sid or sid == pid:
            continue
        for t in niche_terms(sibling):
            owned.add(t)
        for t in vocab_terms_for(cfg, sid):
            if t:
                owned.add(t)
    for t in vocab_terms_for(cfg, pid):
        if t and t not in seen and t not in owned:
            seen.add(t); out.append(t)
    return out


def _is_evidence(rec: dict, *, now: datetime | None = None) -> bool:
    """True when a cache record is a real, unexpired PLATFORM measurement — the admission predicate.
    Demands at least one positive Instagram number (`has_evidence`: scale, current Reels popularity, or a
    legacy Top-grid median), a parseable `measured_at`, and freshness. A size-only row must qualify or
    size-first ranking could never see the biggest tags. Legacy invented `reach` sums carry none of the
    three and fail here."""
    if not isinstance(rec, dict):
        return False
    try:
        if not has_evidence(rec):
            return False
        ts = datetime.fromisoformat(rec["measured_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - ts <= timedelta(days=_EVIDENCE_MAX_AGE_DAYS)


def _aligned_pool(per, cache: dict[str, dict], *, now=None, cfg: Config | None = None) -> list[tuple[str, float, str]]:
    """Candidates for corpus membership, ordered SIZE-FIRST (MOL-692 / MOL-714).

    Relatedness (`from` strength against niche ∪ unique-to-persona LLM vocab) makes a candidate;
    SIZE orders the candidates and `derive_corpus` cuts at `corpus_target`. Unconditional seat =
    operator niche only. Non-niche needs media_count ≥ MIN_MEDIA_FLOOR plus relatedness. Shared /
    sibling-owned LLM terms never attribute. CATEGORY-scale non-niche needs multi-root (MOL-685).
    Outbound co-tags still must not appear in `from` (MOL-643).

    The emitted float is the PRIMARY rank number (`media_count`, 0.0 when Instagram never served volume)
    — a verbatim platform field, and total so `fanops hashtags discover` can never receive None."""
    niche = {_norm("#" + t) for t in niche_terms(per)}; niche.discard("#")
    related = {_norm("#" + t) for t in relatedness_terms(per, cfg)}; related.discard("#")
    out: list[tuple[str, float, str]] = []
    for tag, rec in cache.items():
        if not _is_evidence(rec, now=now) or not is_curatable(tag):
            continue
        if not _is_candidate(tag, rec, related, niche_anchors=niche):
            continue
        if tag in niche:
            src = tag
        else:
            frm = rec.get("from") if isinstance(rec.get("from"), dict) else {}
            live = [a for a in frm if a in related]
            src = max(live, key=lambda a: (frm.get(a) or 0, a)) if live else tag
        out.append((tag, float(tag_size(rec) or 0.0), src))
    out.sort(key=lambda r: size_rank_key(r[0], cache[r[0]]))
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
    cache = load_measurements(cfg)
    pool = _aligned_pool(per, cache, now=now, cfg=cfg)
    if not pool:
        return {"changed": False}                          # outage / cold cache: hold, never empty
    stamp = (now.isoformat() if isinstance(now, datetime) else None) or datetime.now(timezone.utc).isoformat()
    chosen = pool[:max(1, cfg.corpus_target)]
    final = [t for t, _v, _s in chosen]
    if final == corpus:
        return {"changed": False}
    meta: dict = {}
    for t, _v, s in chosen:
        row: dict = {"measured_at": stamp, "from": s}
        rec = cache.get(t) or {}
        for k in RECORD_NUM_FIELDS:
            fv = _num(rec.get(k))
            if fv is not None:
                row[k] = fv
        meta[t] = row

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
    pool = _aligned_pool(per, load_measurements(cfg), now=now, cfg=cfg)
    return {"terms": persona_terms(per, cfg), "measured": len(pool),
            "top": [(t, v) for t, v, _s in pool[:top_n]]}


def _persona_row(cfg: Config, pid: str) -> dict | None:
    """The RAW personas.json row for one persona (so a reader can see fields the model doesn't carry,
    e.g. `hashtag_corpus_meta`). None when absent."""
    from fanops.persona_store import _load_raw
    _, plist = _load_raw(cfg.personas_path)
    return next((d for d in plist if isinstance(d, dict) and d.get("id") == pid), None)


def _inputs_fingerprint(cfg: Config) -> str:
    """Digest of the two control files a derive READS: personas.json + hashtags.json (MOL-694).

    sha256 over their bytes, length-prefixed so content moving between the two files cannot collide, and
    stable across processes (`hash()` is per-interpreter salted). A missing file contributes b"" — absent
    is a state, not an error. Mirrors `hashtag_vocab._input_fingerprint`, one layer up: there the digest
    covers one persona's prompt inputs, here it covers the whole derive's inputs."""
    import hashlib
    h = hashlib.sha256()
    for p in (cfg.personas_path, cfg.hashtags_path):
        try:
            blob = p.read_bytes() if p.exists() else b""
        except OSError:
            blob = b""
        h.update(len(blob).to_bytes(8, "big")); h.update(blob)
    return h.hexdigest()[:16]


def _marker_fingerprint(marker) -> str:
    """The `inputs_fp` stamped by the last complete refresh, or "" (absent / pre-MOL-694 / unreadable)."""
    import json
    try:
        raw = json.loads(marker.read_text())
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(raw, dict):
        return ""
    fp = raw.get("inputs_fp")
    return fp if isinstance(fp, str) else ""


def refresh_corpora_if_due(cfg: Config, *, now=None) -> dict:
    """The run-loop safety net: re-derive every persona's corpus when its INPUTS changed — never on a
    clock (MOL-694). `.corpora_refresh.json` carries an `inputs_fp` digest of personas.json +
    hashtags.json; a matching digest is `reason=unchanged` and costs zero derives, while a changed one
    derives IMMEDIATELY (no age to wait out — an operator niche edit lands on the very next tick even
    with a cold Layer A). The old 12h mtime throttle re-derived every persona twice a day for nothing and
    still made an edited niche wait up to twelve hours.

    KEPT, not deleted: Layer A only derives at the end of a pass that measured something, so this is what
    catches an eviction-only write, an operator edit, or a pass that died before its derive.

    UNCONDITIONAL — there is no kill switch, because a routinely re-derived corpus IS the system's
    behaviour, not an opt-in (a `FANOPS_CORPUS_AUTO=0` line in the live .env once froze every persona at
    its seed tags for nine days). FAIL-OPEN: never raises."""
    from fanops.controlio import write_json_atomic
    from fanops.errors import ControlFileError, fail_open
    from fanops.log import get_logger
    marker = cfg.control / ".corpora_refresh.json"
    try:
        fp = _inputs_fingerprint(cfg)
        if _marker_fingerprint(marker) == fp:               # "" (no marker / pre-MOL-694) can never match
            return {"refreshed": False, "reason": "unchanged"}
        try:
            personas = _registry(cfg).all()
        except ControlFileError as e:
            return {"refreshed": False, "aborted": "corrupt_personas", "reason": str(e)}
        changed = 0; added_n = 0; removed_n = 0; failed = 0
        for per in personas:
            r = None
            with fail_open(f"persona_research.derive_corpus.{per.id}"):
                r = derive_corpus(cfg, per.id, now=now)
            if r is None:
                failed += 1; continue                      # swallowed miss: stay DUE, stamp no fingerprint
            if r.get("changed"):
                changed += 1; added_n += len(r.get("added") or []); removed_n += len(r.get("removed") or [])
        blob = {"ts": datetime.now(timezone.utc).isoformat(), "personas": len(personas),
                "changed": changed, "added": added_n, "removed": removed_n}
        if not failed:
            # Recomputed AFTER the round: a derive WRITES personas.json, so a pre-derive digest could
            # never match on the next tick and the net would derive forever.
            blob["inputs_fp"] = _inputs_fingerprint(cfg)
        write_json_atomic(marker, blob)
        return {"refreshed": True, "personas": len(personas), "changed": changed,
                "added": added_n, "removed": removed_n, "failed": failed}
    except Exception as exc:                               # noqa: BLE001 — the tick must never die here
        get_logger(cfg)("corpus", "-", "refresh_error", err=f"{type(exc).__name__}: {str(exc)[:120]}")
        return {"refreshed": False, "reason": f"error: {str(exc)[:120]}"}
