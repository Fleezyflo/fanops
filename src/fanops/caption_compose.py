"""Posted-caption compose helpers + source lock reads for the caption/ship path."""
from __future__ import annotations
import hashlib
import json
import re
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.hashtags import (RECORD_NUM_FIELDS, ship_from_lock, _dedupe_norm, _num, CAPTION_TAG_RE)
from fanops.models import Platform

# YouTube sends Post.caption raw. IG/TT append the intersection at send. All three store the sentence.
_STORED_CAPTION_PLATFORMS = frozenset({Platform.instagram, Platform.tiktok, Platform.youtube})


def _source_lock_record(cfg: Config, src) -> dict | None:
    """Sidecar row for this source, or None. Missing / corrupt sidecar → None."""
    from fanops.source_tags import load_source_tag_locks
    if src is None:
        return None
    sid = str(getattr(src, "id", "") or "")
    if not sid:
        return None
    rec = load_source_tag_locks(cfg).get(sid)
    return rec if isinstance(rec, dict) else None


def _source_lock_completed(cfg: Config, src) -> bool:
    """True when the source has a completed lock row (`researched_at` set). Empty `lock: []` is completed."""
    rec = _source_lock_record(cfg, src)
    if rec is None:
        return False
    at = rec.get("researched_at")
    return isinstance(at, str) and bool(at.strip())


def _source_lock_tags(cfg: Config, src) -> list[str]:
    """Caption menu = that source's lock. Missing sidecar / empty lock → []. Never the 80-pile."""
    rec = _source_lock_record(cfg, src)
    if rec is None:
        return []
    raw = rec.get("lock")
    if not isinstance(raw, list):
        return []
    return _dedupe_norm(t for t in raw if isinstance(t, str))


def _lock_fingerprint(lock: list[str]) -> str:
    return hashlib.sha256(json.dumps(lock, sort_keys=False).encode()).hexdigest()[:16]


def _tags_off_lock(cfg: Config, src, tags: list) -> bool:
    if not _source_lock_completed(cfg, src):
        return False
    allowed = set(_source_lock_tags(cfg, src))
    return any(t not in allowed for t in _dedupe_norm(tags or []))


def compose_posted_caption(sentence, tags) -> str:
    """Ship/display caption: sentence + lock tags. IG/TT send this; YouTube sends Post.caption raw.

    Strips `CAPTION_TAG_RE` matches and leftover `#` tokens from `sentence` when a tag line
    is appended, so a previously composed string is idempotent. If tags: sentence + newline
    + up to four tags. `tags == []` is a computed empty intersection: return the stripped
    sentence (in-sentence hashtags do not ship). `tags is None` means no completed lock:
    return the original sentence, hashes included.
    """
    original = (sentence or "").strip()
    text = sentence or ""
    text = CAPTION_TAG_RE.sub("", text)
    text = re.sub(r"#\S*", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    line: list[str] = []
    for raw in (tags or []):
        tok = str(raw).strip() if raw is not None else ""
        if tok:
            line.append(tok)
        if len(line) >= 4:
            break
    if line:
        return f"{text}\n{' '.join(line)}" if text else " ".join(line)
    if tags is None:
        return original
    return text


def _locked_store(sentence, picks, lock) -> tuple[str, list[str]] | None:
    """Completed-lock stored caption. None when the row already matches the intersection."""
    sentence = sentence or ""
    vetted = [t for t in (picks or []) if isinstance(t, str)]
    tags = ship_from_lock(vetted, lock, n=4)
    inline = bool(CAPTION_TAG_RE.search(sentence))
    if vetted == tags and not (not tags and inline):
        return None
    return compose_posted_caption(sentence, []), list(tags)


def replace_locked_captions(led, cfg: Config, clip, src) -> None:
    """Replace already-minted Post.caption (IG, TikTok, YouTube) and meta_captions for a completed lock.

    An empty vetted-tag intersection drops hashtags left inside the sentence. YouTube sends
    Post.caption raw, so posted_text_for is not this replacement. A missing lock file is not
    completed and is left as stored.
    """
    if clip is None or not _source_lock_completed(cfg, src):
        return
    lock = _source_lock_tags(cfg, src)
    meta = getattr(clip, "meta_captions", None)
    if isinstance(meta, dict):
        for entry in meta.values():
            if not isinstance(entry, dict):
                continue
            stored = _locked_store(entry.get("caption") or "", entry.get("hashtags") or [], lock)
            if stored is None:
                continue
            entry["caption"], entry["hashtags"] = stored
    if led is None:
        return
    cid = getattr(clip, "id", None)
    for post in led.posts.values():
        if getattr(post, "parent_id", None) != cid:
            continue
        if getattr(post, "platform", None) not in _STORED_CAPTION_PLATFORMS:
            continue
        stored = _locked_store(post.caption or "", post.hashtags or [], lock)
        if stored is None:
            continue
        post.caption, post.hashtags = stored


def posted_text_for(cfg: Config, led: Ledger, post) -> str:
    """IG/TT vendor content: compose_posted_caption(post.caption, picks ∩ source lock).

    A missing lock file (no completed row) ships the stored caption, hashes included.
    A completed lock whose intersection is empty does not. YouTube does not call this.
    Wire contract: src/fanops/post/CLAUDE.md.
    """
    sentence = (getattr(post, "caption", None) or "") if post is not None else ""
    src = None
    if led is not None and post is not None:
        clip = led.clips.get(getattr(post, "parent_id", None))
        moment = led.moments.get(clip.parent_id) if clip is not None else None
        src = led.sources.get(moment.parent_id) if moment is not None else None
    if not _source_lock_completed(cfg, src):
        return (sentence or "").strip()
    picks = getattr(post, "hashtags", None) if post is not None else None
    tags = ship_from_lock(picks, _source_lock_tags(cfg, src), n=4)
    return compose_posted_caption(sentence, tags)


def _hashtag_metrics_for(meas: dict, tags: list[str]) -> dict:
    """Forward RECORD_NUM_FIELDS for lock tags only. Empty/unmeasured tags omit a row."""
    out: dict = {}
    for t in tags:
        rec = meas.get(t)
        if not isinstance(rec, dict):
            continue
        row = {}
        for k in RECORD_NUM_FIELDS:
            v = _num(rec.get(k))
            if v is not None:
                row[k] = v
        if row:
            out[t] = row
    return out
