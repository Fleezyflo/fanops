"""Upload a local file to Blotato -> public URL (presign -> presignedUrl/publicUrl; PUT binary).
ensure_clip_media uploads ONCE PER CLIP and caches the URL on the Clip (FIX F44 — v1 re-uploaded
per post). dryrun returns file:// so the pipeline runs offline. The presign contract (the
presignedUrl + publicUrl response keys) was VERIFIED against the live Blotato
`create_presigned_upload_url` MCP tool schema 2026-06-02 (AUDIT D5) — no longer an unverified
checkpoint. (The POST URL path itself is the only remaining assumption.)"""
from __future__ import annotations
import mimetypes
from pathlib import Path
import requests
from fanops.config import Config
from fanops.errors import BlotatoAuthError
from fanops.ledger import Ledger
from fanops.post.blotato_base import BASE_URL

# Reject a runaway upload BEFORE we touch the network (AUDIT (e)). Clips are short vertical
# by design, so 500 MB is generous headroom yet catches a mis-pointed path at a full library /
# a corrupt multi-GB file before it wastes a presign + a long stalled PUT.
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024

# Size-aware PUT timeout: a flat 120s kills a large-but-valid upload mid-stream and makes a tiny
# one wait pointlessly long on a hang. Scale base + per-MB allowance, clamped (AUDIT (e)).
_PUT_TIMEOUT_BASE_S = 60.0          # floor — even a 1-byte file gets this
_PUT_TIMEOUT_PER_MB_S = 2.0         # ~2s/MB ≈ a slow ~4 Mbps uplink with margin
_PUT_TIMEOUT_MAX_S = 600.0          # ceiling — never wait more than 10 min on one PUT

def _put_timeout_for(size_bytes: int) -> float:
    """Per-MB-scaled PUT timeout, floored at the base and clamped at the max."""
    size_mb = max(0, size_bytes) / (1024 * 1024)
    return min(_PUT_TIMEOUT_MAX_S, _PUT_TIMEOUT_BASE_S + size_mb * _PUT_TIMEOUT_PER_MB_S)

def dryrun_media_url(path: Path) -> str:
    return f"file://{Path(path).resolve()}"

def upload_media(cfg: Config, path: Path) -> str:
    key = cfg.blotato_api_key
    if not key:
        raise BlotatoAuthError("BLOTATO_API_KEY missing — cannot upload media.")
    size = Path(path).stat().st_size
    if size > _MAX_UPLOAD_BYTES:
        # Plain RuntimeError (NOT BlotatoAuthError): this is a bad input, not an auth halt —
        # fail THIS upload loudly before any network, don't halt the whole queue by type.
        raise RuntimeError(
            f"Media file too large to upload: {size} bytes "
            f"(> cap {_MAX_UPLOAD_BYTES} bytes) — {path}")
    headers = {"blotato-api-key": key, "Content-Type": "application/json"}
    resp = requests.post(f"{BASE_URL}/media/uploads", headers=headers,
                         json={"filename": Path(path).name}, timeout=30)
    if resp.status_code == 401:
        # A 401 on the media presign is the SAME fatal auth condition as a 401 on the post —
        # halt the whole queue by type (AUDIT H8), don't mark one post failed and grind on.
        raise BlotatoAuthError(f"Blotato 401 on media presign — check BLOTATO_API_KEY: {(resp.text or '')[:200]}")
    if resp.status_code >= 300:
        raise RuntimeError(f"Blotato presign failed ({resp.status_code}): {(resp.text or '')[:300]}")
    presign = resp.json()
    if "presignedUrl" not in presign or "publicUrl" not in presign:
        raise RuntimeError(f"Blotato presign response missing presignedUrl/publicUrl; got keys {sorted(presign)}")
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        put = requests.put(presign["presignedUrl"], data=fh,
                           headers={"Content-Type": ctype}, timeout=_put_timeout_for(size))
    if put.status_code >= 300:
        raise RuntimeError(f"Blotato media PUT failed ({put.status_code}): {(put.text or '')[:300]}")
    return presign["publicUrl"]

def ensure_clip_media(led: Ledger, cfg: Config, clip_id: str) -> str:
    """Upload the clip's file once; cache the public URL on the Clip and reuse it."""
    clip = led.clips[clip_id]
    if clip.media_url:
        return clip.media_url
    path = Path(clip.path)
    url = dryrun_media_url(path) if cfg.poster_backend == "dryrun" else upload_media(cfg, path)
    clip.media_url = url
    return url
