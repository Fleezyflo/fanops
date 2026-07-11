"""Media-URL resolution + per-clip/per-render upload caching. ensure_clip_media uploads ONCE PER
CLIP and caches the URL on the Clip (FIX F44 — v1 re-uploaded per post); ensure_render_media does the
same per per-account render. dryrun returns file:// so the pipeline runs offline. The actual file->
hosted-URL upload is dispatched per backend via get_media_uploader (Postiz upload / Zernio upload /
dryrun file://)."""
from __future__ import annotations
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger

def resolve_media_path(cfg: Config, stored: str | None, kind: str) -> Path | None:
    """Resolve a ledger-stored media path after FANOPS_ROOT move. kind ∈ clip|render|source."""
    if not stored:
        return None
    base = cfg.base.resolve()
    p = Path(stored)
    if p.exists():
        return p.resolve()
    name = p.name
    if not name:
        return None
    if kind == "render":
        direct = cfg.clips / name
        if direct.exists():
            r = direct.resolve()
            return r if r.is_relative_to(base) else None
        hits = [h for h in cfg.clips.rglob(name) if h.is_file() and h.resolve().is_relative_to(base)]
        return min(hits, key=lambda h: len(h.relative_to(cfg.clips).parts)).resolve() if hits else None
    for root in (cfg.clips, cfg.sources):
        cand = root / name
        if cand.exists():
            r = cand.resolve()
            if r.is_relative_to(base):
                return r
    return None

def dryrun_media_url(path: Path) -> str:
    return f"file://{Path(path).resolve()}"

def _media_cache_hit(url: str, backend: str) -> bool:
    """True when a cached clip/render media_url is safe to reuse for THIS publish backend."""
    if not url:
        return False
    if backend == "dryrun":
        return url.startswith("file://")
    if url.startswith("file://"):
        return False
    if backend == "zernio":
        if "|" in url:
            return False
        low = url.lower()
        if not low.startswith("https://"):
            return False
        if "localhost" in low or "127.0.0.1" in low:
            return False
        return True
    if backend == "postiz":
        return "|" in url
    return url.startswith("http")

def _uploader_kwargs(backend: str, account_id: str | None) -> dict:
    """Only postiz/zernio uploaders require account_id — never pass it to the dryrun uploader."""
    if backend in ("postiz", "zernio") and (account_id or "").strip():
        return {"account_id": account_id}
    return {}


def ensure_render_media(led: Ledger, cfg: Config, render_id: str, local_path: str, backend: str, **kw) -> str:
    """Upload a per-account render's file ONCE; cache the public URL on the Render and reuse it (FIX-F44
    parity for variants — CULM-2; approval re-points media_urls to file://<render> every cycle, so without a
    per-render cache each approve->publish re-uploaded). A missing render (race/GC) falls back to a direct
    upload (no cache home), never crashes the publish. The cache is PERSISTED by run.py's finalize txn."""
    r = led.get_render(render_id) if render_id else None
    if r is not None and r.media_url and _media_cache_hit(r.media_url, backend):
        return r.media_url
    from fanops.post import get_media_uploader          # lazy: avoid the post/__init__ <-> media import cycle
    aid = kw.get("account_id")
    path = resolve_media_path(cfg, local_path, "render") or Path(local_path)
    url = get_media_uploader(cfg, backend)(cfg, path, **_uploader_kwargs(backend, aid))
    if r is not None: r.media_url = url                 # persisted in run.py's finalize txn (mirrors clip_media)
    return url

def ensure_clip_media(led: Ledger, cfg: Config, clip_id: str, backend: str | None = None, *, account_id: str | None = None) -> str:
    """Upload the clip's file once; cache the public URL on the Clip and reuse it."""
    clip = led.clips[clip_id]
    b = backend or cfg.poster_backend
    if clip.media_url and _media_cache_hit(clip.media_url, b):
        return clip.media_url
    # Backend-dispatched (dryrun -> file://, postiz -> Postiz upload, zernio -> Zernio upload).
    # Lazy import avoids a post/__init__ <-> media import cycle.
    from fanops.post import get_media_uploader
    kw = _uploader_kwargs(b, account_id)
    path = resolve_media_path(cfg, clip.path, "clip") or Path(clip.path)
    url = get_media_uploader(cfg, b)(cfg, path, **kw)
    clip.media_url = url
    return url
