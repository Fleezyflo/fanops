"""Upload a local file to Blotato -> public URL (POST /media/uploads -> presignedUrl/
publicUrl; PUT binary). ensure_clip_media uploads ONCE PER CLIP and caches the URL on the
Clip (FIX F44 — v1 re-uploaded per post). dryrun returns file:// so the pipeline runs
offline. The /media/uploads contract is an INTEGRATION CHECKPOINT."""
from __future__ import annotations
import mimetypes
from pathlib import Path
import requests
from fanops.config import Config
from fanops.ledger import Ledger

BASE_URL = "https://backend.blotato.com/v2"

def dryrun_media_url(path: Path) -> str:
    return f"file://{Path(path).resolve()}"

def upload_media(cfg: Config, path: Path) -> str:
    key = cfg.blotato_api_key
    if not key:
        raise RuntimeError("BLOTATO_API_KEY missing — cannot upload media.")
    headers = {"blotato-api-key": key, "Content-Type": "application/json"}
    resp = requests.post(f"{BASE_URL}/media/uploads", headers=headers,
                         json={"filename": Path(path).name}, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Blotato presign failed ({resp.status_code}): {(resp.text or '')[:300]}")
    presign = resp.json()
    if "presignedUrl" not in presign or "publicUrl" not in presign:
        raise RuntimeError(f"Blotato presign response missing presignedUrl/publicUrl; got keys {sorted(presign)}")
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        put = requests.put(presign["presignedUrl"], data=fh,
                           headers={"Content-Type": ctype}, timeout=120)
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
