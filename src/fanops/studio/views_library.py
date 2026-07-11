"""Library pipeline-map read-models (UI-only): compact list strip + per-source detail map.
Composes artifacts, pipeline_status, agentstep, and lineage primitives — no pipeline writers."""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import MomentState, PostState, SourceState
from fanops.studio.views_common import lineage_maps

_log = logging.getLogger("fanops.studio.views_library")
_TRANSCRIPT_PAGE = 200
_GATE_KINDS = ("moments", "moment_hooks", "captions")
_STATE_RANK = {SourceState.catalogued: 0, SourceState.discovered: 0, SourceState.transcribed: 1,
               SourceState.signalled: 2, SourceState.moments_requested: 3, SourceState.picks_decided: 4,
               SourceState.moments_decided: 5, SourceState.moments_empty: 5, SourceState.error: -1,
               SourceState.retired: 99}

STAGES = (
    {"key": "catalogue", "label": "Catalogue"},
    {"key": "transcribe", "label": "Transcribe"},
    {"key": "signals", "label": "Signals"},
    {"key": "framing", "label": "Framing"},
    {"key": "keyframes", "label": "Keyframes"},
    {"key": "moments", "label": "Moments"},
    {"key": "hooks", "label": "Hooks"},
    {"key": "clip", "label": "Clip"},
    {"key": "captions", "label": "Captions"},
    {"key": "crosspost", "label": "Crosspost"},
    {"key": "published", "label": "Published"},
)


def _gate_page_index(cfg: Config) -> dict[str, str]:
    """Map gate key -> /gates#gate-{key} deep link (forward keys, never parsed from handles)."""
    from fanops.studio import views
    return {r["key"]: f"/gates#gate-{r['key']}" for r in views.gate_rows(cfg) if r.get("key")}


def _gates_for_source(cfg: Config, led: Ledger, source_id: str, *, pend: dict[str, set[str]]) -> dict[str, list[str]]:
    """Pending gate keys owned by source_id — prefix glob for dotted source ids (MOL-480)."""
    from fanops.agentstep import gate_keys_for
    moments = [k for k in gate_keys_for(cfg, "moments", f"{source_id}.") if k in pend["moments"]]
    hooks = [k for k in gate_keys_for(cfg, "moment_hooks", f"{source_id}.") if k in pend["moment_hooks"]]
    clip_ids = {c.id for m in led.moments.values() if m.parent_id == source_id
                for c in led.clips.values() if c.parent_id == m.id}
    captions = sorted(k for k in pend["captions"] if k in clip_ids)
    return {"moments": moments, "moment_hooks": hooks, "captions": captions}


def _source_stats(led: Ledger, source_id: str, moms: dict, clips_bm: dict, posts_bc: dict) -> dict:
    """≤6 stats for one source from pre-built lineage maps (O(1) per source on list)."""
    moments = moms.get(source_id, [])
    clips = [c for m in moments for c in clips_bm.get(m.id, [])]
    posts = [p for c in clips for p in posts_bc.get(c.id, [])]
    published = sum(1 for p in posts if p.state in (PostState.published, PostState.analyzed))
    return {"moments": len(moments), "clips": len(clips), "posts": len(posts), "published": published,
            "picked": sum(1 for m in moments if m.state == MomentState.picked),
            "decided": sum(1 for m in moments if m.state == MomentState.decided)}


def _stage_status(cfg: Config, led: Ledger, src, *, disk: dict[str, bool], gates: dict[str, list[str]],
                  stats: dict, include_at: bool = False) -> list[dict]:
    """Build the 11-cell strip; status from ledger/artifacts only — manifest timestamps optional on detail."""
    from fanops.artifacts import manifest_stage_times
    if src.origin_kind == "third_party" or src.state in (SourceState.retired, SourceState.discovered):
        return [{"key": s["key"], "label": s["label"], "status": "inert"} for s in STAGES]
    at = manifest_stage_times(cfg, src.id) if include_at else {}
    st = src.state; rank = _STATE_RANK.get(st, 0)
    err = st is SourceState.error
    def _cell(key, label, status, *, gate_key=None, gate_url=None, at_key=None):
        row = {"key": key, "label": label, "status": status}
        if gate_key: row["gate_key"] = gate_key
        if gate_url: row["gate_url"] = gate_url
        if include_at and at_key and at_key in at: row["at"] = at[at_key]
        return row
    gidx = _gate_page_index(cfg) if any(gates.values()) else {}
    mkey = gates["moments"][0] if gates["moments"] else None
    hkey = gates["moment_hooks"][0] if gates["moment_hooks"] else None
    ckey = gates["captions"][0] if gates["captions"] else None
    transcribed = bool(src.meta.get("transcribed")) or disk.get("transcribe") or rank >= 1
    signalled = rank >= 2 or disk.get("signals")
    framed = disk.get("framing")
    keyed = disk.get("keyframes")
    has_clips = stats["clips"] > 0
    has_posts = stats["posts"] > 0
    has_pub = stats["published"] > 0
    cells = [
        _cell("catalogue", "Catalogue", "done" if rank > 0 or transcribed else ("blocked" if err else "active")),
        _cell("transcribe", "Transcribe", "done" if transcribed else ("blocked" if err else "active"), at_key="transcribe"),
        _cell("signals", "Signals", "done" if signalled else ("todo" if not transcribed else "active"), at_key="signals"),
        _cell("framing", "Framing", "done" if framed else ("todo" if not signalled else "active"), at_key="framing"),
        _cell("keyframes", "Keyframes", "done" if keyed else ("todo" if not signalled else "active"), at_key="keyframes"),
        _cell("moments", "Moments", "pending" if mkey else ("done" if rank >= 5 or stats["moments"] else "active"),
              gate_key=mkey, gate_url=gidx.get(mkey) if mkey else None, at_key="moments"),
        _cell("hooks", "Hooks", "pending" if hkey else ("done" if rank >= 5 else ("active" if st is SourceState.picks_decided else "todo")),
              gate_key=hkey, gate_url=gidx.get(hkey) if hkey else None, at_key="moment_hooks"),
        _cell("clip", "Clip", "done" if has_clips else ("active" if rank >= 5 else "todo"), at_key="clip"),
        _cell("captions", "Captions", "pending" if ckey else ("done" if has_posts else ("active" if has_clips else "todo")),
              gate_key=ckey, gate_url=gidx.get(ckey) if ckey else None, at_key="captions"),
        _cell("crosspost", "Crosspost", "done" if has_posts else ("active" if has_clips else "todo")),
        _cell("published", "Published", "done" if has_pub else "todo"),
    ]
    return cells


def _stage_strip(cfg: Config, led: Ledger, row: dict, *, moms, clips_bm, posts_bc, pend, gate_idx) -> list[dict]:
    src = led.sources.get(row["id"])
    if src is None:
        return [{"key": s["key"], "label": s["label"], "status": "inert"} for s in STAGES]
    from fanops.artifacts import disk_stage_flags
    disk = disk_stage_flags(cfg, src.id, src.source_path or "")
    gates = _gates_for_source(cfg, led, src.id, pend=pend)
    stats = _source_stats(led, src.id, moms, clips_bm, posts_bc)
    return _stage_status(cfg, led, src, disk=disk, gates=gates, stats=stats, include_at=False)


def library_catalog(cfg: Config) -> dict:
    """Wrap asset_catalog: same fail-open shape + compact stage_strip per row (manifest-free on list)."""
    from fanops.studio.views import asset_catalog
    try:
        cat = asset_catalog(cfg)
        led = Ledger.load(cfg)
        moms, clips_bm, posts_bc = lineage_maps(led)
        from fanops.agentstep import pending
        pend = {k: set(pending(cfg, kind=k)) for k in _GATE_KINDS}
        gate_idx = _gate_page_index(cfg)
        for bucket in ("native", "third_party"):
            cat[bucket] = [{**r, "stage_strip": _stage_strip(cfg, led, r, moms=moms, clips_bm=clips_bm,
                                                             posts_bc=posts_bc, pend=pend, gate_idx=gate_idx)}
                           for r in cat[bucket]]
        return cat
    except Exception as exc:
        from fanops.log import get_logger
        get_logger(cfg)("library_catalog", "-", "error", err=str(exc)[:160])
        return {"native": [], "third_party": [],
                "backlog": {"actionable": 0, "blocked_on_gates": 0, "recoverable": 0, "inventory": 0}}


@dataclass
class TranscriptPage:
    segments: list
    offset: int
    total: int
    next_offset: Optional[int]
    source: str   # "sidecar" | "ledger" | "none"
    word_count: int


def _transcript_page(cfg: Config, led: Ledger, src, offset: int) -> TranscriptPage:
    """200-seg pages; words[] count-only; ledger fallback labelled."""
    from fanops.artifacts import transcript_sidecar_path
    segs, source = [], "none"
    side = transcript_sidecar_path(cfg, src.source_path or "")
    if side.exists():
        try:
            data = json.loads(side.read_text())
            if isinstance(data.get("segments"), list):
                segs, source = data["segments"], "sidecar"
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("transcript sidecar unreadable (fail-open)", exc_info=exc)
    if not segs and src.transcript:
        segs, source = [s if isinstance(s, dict) else {"start": s.start, "end": s.end, "text": s.text}
                        for s in src.transcript], "ledger"
    total = len(segs)
    off = max(0, min(offset, total))
    page = segs[off:off + _TRANSCRIPT_PAGE]
    nxt = off + _TRANSCRIPT_PAGE if off + _TRANSCRIPT_PAGE < total else None
    wc = sum(len((s.get("words") or [])) for s in page)
    return TranscriptPage(segments=page, offset=off, total=total, next_offset=nxt, source=source, word_count=wc)


def source_pipeline_map(cfg: Config, source_id: str, *, offset: int = 0) -> Optional[dict]:
    """Detail map for /library/<source_id>; None when unknown."""
    led = Ledger.load(cfg)
    src = led.sources.get(source_id)
    if src is None:
        return None
    moms, clips_bm, posts_bc = lineage_maps(led)
    from fanops.agentstep import pending
    from fanops.artifacts import disk_stage_flags, manifest_stage_times
    pend = {k: set(pending(cfg, kind=k)) for k in _GATE_KINDS}
    gates = _gates_for_source(cfg, led, source_id, pend=pend)
    stats = _source_stats(led, source_id, moms, clips_bm, posts_bc)
    disk = disk_stage_flags(cfg, source_id, src.source_path or "")
    strip = _stage_status(cfg, led, src, disk=disk, gates=gates, stats=stats, include_at=True)
    tx = _transcript_page(cfg, led, src, offset)
    kf_dir = cfg.agent_io / "keyframes" / source_id
    keyframes = []
    if kf_dir.exists():
        for p in sorted(kf_dir.rglob("*.jpg"))[:24]:
            rel = p.relative_to(kf_dir)
            whash = rel.parts[0] if len(rel.parts) > 1 else ""
            name = rel.name
            keyframes.append({"name": name, "whash": whash if len(whash) == 64 else "",
                              "url": f"/keyframe/{source_id}/{whash}/{name}" if len(whash) == 64
                              else f"/keyframe/{source_id}/{name}"})
    media_url = f"/source-media/{source_id}" if src.source_path else None
    from fanops.pipeline_status import source_backlog
    bl = source_backlog(led, cfg)
    by_id = {r.id: r for r in bl.rows}
    brow = by_id.get(source_id)
    return {"source_id": source_id, "name": Path(src.source_path).name if src.source_path else source_id,
            "origin_kind": src.origin_kind, "state": src.state.value, "stats": stats, "stage_strip": strip,
            "transcript": tx, "keyframes": keyframes, "media_url": media_url,
            "manifest_at": manifest_stage_times(cfg, source_id), "wait_line": brow.wait_line if brow else None,
            "artifacts": brow.artifacts if brow else None}
