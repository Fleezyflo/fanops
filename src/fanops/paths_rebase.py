"""One-shot ledger + manifest media-path rebase after FANOPS_ROOT move."""
from __future__ import annotations
import json
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.post.media import resolve_media_path


def _manifest_kind(artifact: str, stage: str) -> str:
    norm = artifact.replace("\\", "/")
    if "02_sources" in norm:
        return "source"
    if "03_clips" in norm:
        return "render"
    if stage == "clip":
        return "clip"
    return "source"


def _rebase_value(cfg: Config, stored: str | None, kind: str) -> str | None:
    if not stored:
        return None
    resolved = resolve_media_path(cfg, stored, kind)
    if resolved and str(resolved) != stored:
        return str(resolved)
    return None


def _rebase_media_url(cfg: Config, url: str, kind: str) -> str | None:
    if not url or url.startswith(("http://", "https://")):
        return None
    stored = url[7:] if url.startswith("file://") else url
    new = _rebase_value(cfg, stored, kind)
    if not new:
        return None
    return f"file://{new}" if url.startswith("file://") else new


def _scan_ledger(cfg: Config, led: Ledger) -> dict[str, int]:
    counts = {"sources": 0, "clips": 0, "renders": 0, "posts": 0}
    for s in led.sources.values():
        if _rebase_value(cfg, s.source_path, "source"):
            counts["sources"] += 1
    for c in led.clips.values():
        if _rebase_value(cfg, c.path, "clip"):
            counts["clips"] += 1
    for r in led.renders.values():
        if _rebase_value(cfg, r.path, "render"):
            counts["renders"] += 1
    for p in led.posts.values():
        kind = "render" if p.render_id else "clip"
        for u in (p.media_urls or []):
            if _rebase_media_url(cfg, u, kind):
                counts["posts"] += 1
                break
    return counts


def _apply_ledger(cfg: Config, led: Ledger) -> dict[str, int]:
    counts = {"sources": 0, "clips": 0, "renders": 0, "posts": 0}
    for sid, s in list(led.sources.items()):
        new = _rebase_value(cfg, s.source_path, "source")
        if new:
            led.sources[sid] = s.model_copy(update={"source_path": new})
            counts["sources"] += 1
    for cid, c in list(led.clips.items()):
        new = _rebase_value(cfg, c.path, "clip")
        if new:
            led.clips[cid] = c.model_copy(update={"path": new})
            counts["clips"] += 1
    for rid, r in list(led.renders.items()):
        new = _rebase_value(cfg, r.path, "render")
        if new:
            led.renders[rid] = r.model_copy(update={"path": new})
            counts["renders"] += 1
    for pid, p in list(led.posts.items()):
        kind = "render" if p.render_id else "clip"
        urls = list(p.media_urls or [])
        changed = False
        for i, u in enumerate(urls):
            new = _rebase_media_url(cfg, u, kind)
            if new:
                urls[i] = new
                changed = True
        if changed:
            led.posts[pid] = p.model_copy(update={"media_urls": urls})
            counts["posts"] += 1
    return counts


def _scan_manifests(cfg: Config) -> int:
    n = 0
    root = cfg.agent_io / "manifests"
    if not root.exists():
        return 0
    for mp in root.glob("*.json"):
        try:
            d = json.loads(mp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        stages = d.get("stages") or {}
        for stage, info in stages.items():
            if not isinstance(info, dict):
                continue
            art = info.get("artifact")
            if not art or not Path(art).is_absolute():
                continue
            if _rebase_value(cfg, art, _manifest_kind(art, stage)):
                n += 1
    return n


def _apply_manifests(cfg: Config) -> int:
    n = 0
    root = cfg.agent_io / "manifests"
    if not root.exists():
        return 0
    for mp in root.glob("*.json"):
        try:
            d = json.loads(mp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        stages = d.get("stages") or {}
        dirty = False
        for stage, info in stages.items():
            if not isinstance(info, dict):
                continue
            art = info.get("artifact")
            if not art or not Path(art).is_absolute():
                continue
            new = _rebase_value(cfg, art, _manifest_kind(art, stage))
            if new:
                info["artifact"] = new
                dirty = True
                n += 1
        if dirty:
            from fanops.controlio import write_json_atomic
            write_json_atomic(mp, d)
    return n


def paths_rebase_report(cfg: Config, *, apply: bool) -> dict[str, int]:
    if apply:
        snap = Ledger.snapshot(cfg)
        print(f"snapshot: {snap}")
        with Ledger.transaction(cfg) as led:
            counts = _apply_ledger(cfg, led)
        counts["manifests"] = _apply_manifests(cfg)
    else:
        led = Ledger.load(cfg)
        counts = _scan_ledger(cfg, led)
        counts["manifests"] = _scan_manifests(cfg)
    counts["total"] = sum(counts.values())
    return counts


def cmd_paths_rebase(cfg: Config, args) -> int:
    counts = paths_rebase_report(cfg, apply=bool(args.apply))
    for k in ("sources", "clips", "renders", "posts", "manifests", "total"):
        print(f"{k}: {counts.get(k, 0)}")
    return 0
