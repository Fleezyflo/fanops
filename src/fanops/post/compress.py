"""Fail-open video shrink for upload caps (Zernio TikTok 413)."""
from __future__ import annotations
import shutil
import subprocess
import tempfile
from pathlib import Path
from fanops.config import Config
from fanops.log import get_logger


def maybe_shrink_for_cap(cfg: Config, path: Path, cap: int, *, label: str = "upload") -> Path:
    """Return `path` if within cap, else a re-encoded file under cap. Fail-open to `path` on error.

    RC-10: the per-call scratch dir lives in a try/finally so it NEVER outlives the call (it used to
    leak a `fanops-shrink-*` dir into 04_agent_io on every over-cap attempt). The winning encode is
    promoted OUT of the scratch — an atomic same-fs `replace` to a DETERMINISTIC 04_agent_io path, so a
    retry OVERWRITES rather than proliferating — before the scratch is removed."""
    try:
        size = path.stat().st_size
    except OSError:
        return path
    if size <= cap:
        return path
    log = get_logger(cfg)
    shrink_root = cfg.base / "04_agent_io"
    shrink_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="fanops-shrink-", dir=str(shrink_root)))
    try:
        for crf in (28, 32, 36, 40):
            out = tmp / f"{path.stem}.crf{crf}.mp4"
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
                   "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
                   "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=600)
            except Exception as exc:
                log(label, path.stem, "shrink_failed", err=str(exc)[:120])
                return path
            if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
                continue
            if out.stat().st_size <= cap:
                dest = shrink_root / out.name                # promote the winner OUT of the per-call scratch
                out.replace(dest)                            # atomic same-fs move; deterministic name (retry overwrites)
                log(label, path.stem, "shrink_ok", was=size, now=dest.stat().st_size, crf=crf)
                return dest
        log(label, path.stem, "shrink_still_oversize", was=size, cap=cap)
        return path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)               # the scratch NEVER outlives the call (RC-10)


def media_path_for_post(cfg: Config, led, post) -> Path | None:
    """Resolve the on-disk media file a post would upload (render > file media_urls > clip)."""
    from fanops.post.media import resolve_media_path
    if post.render_id:
        r = led.renders.get(post.render_id)
        if r and r.path:
            p = resolve_media_path(cfg, r.path, "render")
            if p:
                return p
    for u in (post.media_urls or []):
        if u.startswith("file://"):
            stored = u[7:]
        elif u.startswith("http"):
            continue
        else:
            stored = u
        kind = "render" if post.render_id else "clip"
        p = resolve_media_path(cfg, stored, kind)
        if p:
            return p
    clip = led.clips.get(post.parent_id) if post.parent_id else None
    if clip and clip.path:
        p = resolve_media_path(cfg, clip.path, "clip")
        if p:
            return p
    return None


def publish_backend_for_post(cfg, post) -> str:
    from fanops.accounts import Accounts
    try:
        return Accounts.load(cfg).effective_provider(post.account, post.platform) or cfg.poster_backend or "dryrun"
    except Exception as e:
        safe = cfg.poster_backend or "dryrun"   # #10: SAFE fallback unchanged — but breadcrumb when the resolve fails so the fallback isn't silent
        get_logger(cfg)("publish", getattr(post, "id", "-"), "backend_fallback", backend=safe, err=str(e)[:120])
        return safe


def upload_cap_bytes(cfg, post, backend: str) -> int | None:
    from fanops.models import Platform
    if post.platform is Platform.tiktok and backend == "zernio":
        return cfg.zernio_max_upload_bytes
    return None


def apply_shrink_to_post(cfg, led, post, *, backend: str | None = None) -> bool:
    """Shrink local media under the publish cap; persist path on render + file:// media_urls. True if within cap."""
    backend = backend or publish_backend_for_post(cfg, post)
    cap = upload_cap_bytes(cfg, post, backend)
    if cap is None:
        return True
    path = media_path_for_post(cfg, led, post)
    if path is None:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= cap:
        return True
    shrunk = maybe_shrink_for_cap(cfg, path, cap, label="shrink_post")
    try:
        new_size = shrunk.stat().st_size
    except OSError:
        return False
    if new_size > cap:
        return False
    if shrunk != path:
        if post.render_id:
            r = led.renders.get(post.render_id)
            if r is not None:
                led.renders[post.render_id] = r.model_copy(update={"path": str(shrunk)})
        post.media_urls = [f"file://{shrunk.resolve()}"]
    return True


def persist_post_shrink(cfg, snapshot_led, post_id: str) -> None:
    """Persist in-memory shrink (render.path + file:// media_urls) from a lock-free snapshot."""
    from fanops.ledger import Ledger
    post = snapshot_led.posts.get(post_id)
    if post is None:
        return
    with Ledger.transaction(cfg) as led:
        p = led.posts.get(post_id)
        if p is None:
            return
        if post.render_id:
            rs = snapshot_led.renders.get(post.render_id)
            r = led.renders.get(post.render_id)
            if rs and r and rs.path and rs.path != r.path:
                led.renders[post.render_id] = r.model_copy(update={"path": rs.path})
                if post.media_urls:
                    p.media_urls = list(post.media_urls)
