"""Posted-caption compose helpers + source lock reads for the caption/ship path."""
from __future__ import annotations
import hashlib
import json
import re
from fanops.caption_ingest import is_tags_only_caption
from fanops.config import Config
from fanops.hashtags import (RECORD_NUM_FIELDS, artist_tag_norms, play_rank_key, ship_from_lock,
                             _dedupe_norm, _num, CAPTION_TAG_RE)
from fanops.ledger import Ledger
from fanops.tagging import ARTIST_HANDLE


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


def _source_lock_measurements(cfg: Config, src) -> dict:
    """Measurements on the sidecar row `_source_lock_tags` orders by. Missing row → {}."""
    rec = _source_lock_record(cfg, src)
    if not isinstance(rec, dict):
        return {}
    meas = rec.get("measurements")
    return meas if isinstance(meas, dict) else {}


def _source_lock_tags(cfg: Config, src) -> list[str]:
    """Caption menu: lock names minus artist norms, ordered by `play_rank_key`.

    Reads the sidecar row from `_source_lock_record`. A missing measurement
    record is the zero key `play_rank_key` already returns. Missing sidecar /
    empty lock → []. Never the 80-pile.
    """
    rec = _source_lock_record(cfg, src)
    if rec is None:
        return []
    raw = rec.get("lock")
    if not isinstance(raw, list):
        return []
    meas = rec.get("measurements") if isinstance(rec.get("measurements"), dict) else {}
    banned = artist_tag_norms(getattr(cfg, "artist_name", None))
    names = [t for t in _dedupe_norm(t for t in raw if isinstance(t, str)) if t not in banned]
    names.sort(key=lambda t: play_rank_key(t, meas.get(t)))
    return names


def _lock_fingerprint(lock: list[str]) -> str:
    return hashlib.sha256(json.dumps(lock, sort_keys=False).encode()).hexdigest()[:16]


def _tags_off_lock(cfg: Config, src, tags: list) -> bool:
    if not _source_lock_completed(cfg, src):
        return False
    allowed = set(_source_lock_tags(cfg, src))
    return any(t not in allowed for t in _dedupe_norm(tags or []))


def _hash_stripped(text: str) -> str:
    """Strip `CAPTION_TAG_RE` matches and leftover `#` tokens, then collapse whitespace."""
    text = CAPTION_TAG_RE.sub("", text or "")
    text = re.sub(r"#\S*", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def compose_posted_caption(sentence, tags, *, drop_inline_tags: bool = False) -> str:
    """Ship/display caption: sentence + lock tags. IG/TT send this; YouTube sends Post.caption raw.

    Strips `CAPTION_TAG_RE` matches and leftover `#` tokens from `sentence` so a previously
    composed string is idempotent. If tags: `sentence + "\\n" + " ".join(tags[:4])`.
    Empty/missing tags → original `sentence` (hashes included), not the hash-stripped remainder.
    `drop_inline_tags` returns that stripped remainder instead — a non-empty source lock with
    nothing left to append must not keep off-lock hashes.
    """
    text = _hash_stripped(sentence or "")
    line: list[str] = []
    for raw in (tags or []):
        tok = str(raw).strip() if raw is not None else ""
        if tok:
            line.append(tok)
        if len(line) >= 4:
            break
    if line:
        return f"{text}\n{' '.join(line)}" if text else " ".join(line)
    if drop_inline_tags:
        return text
    return (sentence or "").strip()


def split_posted_caption(text) -> tuple[str, list[str]]:
    """Split a composed send string into `(sentence, tag_tokens)`.

    The sentence is `_hash_stripped` — the same strip `compose_posted_caption` uses.
    Tag tokens are `CAPTION_TAG_RE` matches, then leftover `#…` tokens that regex did not take.
    """
    raw = text or ""
    tokens = list(CAPTION_TAG_RE.findall(raw))
    rest = CAPTION_TAG_RE.sub("", raw)
    for tok in re.findall(r"#\S*", rest):
        body = tok[1:] if tok.startswith("#") else tok
        if body:
            tokens.append(tok)
    return _hash_stripped(raw), tokens


def _platform_value(post) -> str:
    plat = getattr(post, "platform", None)
    return str(getattr(plat, "value", None) or plat or "")


def _entry_on(clip, key: str, legacy: str) -> dict | None:
    meta = getattr(clip, "meta_captions", None) or {}
    if not isinstance(meta, dict):
        return None
    rec = meta.get(key)
    if not isinstance(rec, dict):
        rec = meta.get(legacy)
    return rec if isinstance(rec, dict) else None


def _surface_keys(post) -> tuple[str, str]:
    account = str(getattr(post, "account", "") or "")
    plat = _platform_value(post)
    return f"{account}/{plat}", f"@{account}/{plat}"


def _minted_on(entry, post) -> bool:
    """True when this entry's sentence and tags are the ones stored on the post."""
    if not isinstance(entry, dict) or post is None:
        return False
    stored_tags = list(entry.get("hashtags") or [])
    post_tags = list(getattr(post, "hashtags", None) or [])
    if stored_tags != post_tags:
        return False
    stored = (entry.get("caption") or "").strip()
    posted = (getattr(post, "caption", None) or "").strip()
    if stored == posted:
        return True
    if stored and posted == f"{stored}\n{ARTIST_HANDLE}":
        return True
    return bool(post_tags) and is_tags_only_caption(posted)


def _caption_clip(led: Ledger, post, clip):
    """Clip that holds this post's account/platform entry minted onto the post.

    The post's parent wins when it holds that entry. Otherwise the same-moment
    clip whose stored sentence and tags match the post.
    """
    if led is None or post is None or clip is None:
        return None
    key, legacy = _surface_keys(post)

    def entry_of(c):
        return _entry_on(c, key, legacy)

    own = entry_of(clip)
    if _minted_on(own, post):
        return clip
    moment_id = getattr(clip, "parent_id", None)
    siblings = []
    for c in led.clips.values():
        if getattr(c, "id", None) == getattr(clip, "id", None):
            continue
        if getattr(c, "parent_id", None) != moment_id:
            continue
        rec = entry_of(c)
        if rec is not None:
            siblings.append((c, rec))
    matched = [(c, rec) for c, rec in siblings if _minted_on(rec, post)]
    if matched:
        for c, rec in matched:
            stored = (rec.get("caption") or "").strip()
            if stored and not is_tags_only_caption(stored):
                return c
        return matched[0][0]
    if own is not None:
        return clip
    if len(siblings) == 1:
        return siblings[0][0]
    return None


def _surface_entry(led: Ledger, post, clip) -> dict | None:
    """Account/platform entry minted onto this post, from the clip `_caption_clip` binds."""
    bound = _caption_clip(led, post, clip)
    if bound is None or post is None:
        return None
    key, legacy = _surface_keys(post)
    return _entry_on(bound, key, legacy)


def _lineage_bits(cfg: Config, led: Ledger, post):
    """`(sentence, clip, source, lock tags)` for this post. Missing lineage → empty lock."""
    sentence = (getattr(post, "caption", None) or "") if post is not None else ""
    clip = src = None
    if led is not None and post is not None:
        clip = led.clips.get(getattr(post, "parent_id", None))
        moment = led.moments.get(clip.parent_id) if clip is not None else None
        src = led.sources.get(moment.parent_id) if moment is not None else None
    return sentence, clip, src, _source_lock_tags(cfg, src)


def _bound_entry(led: Ledger, post, clip, lock) -> dict | None:
    """Surface entry `_posted_ship` binds when a lock is present, else None."""
    if not lock or clip is None or led is None or post is None:
        return None
    found = _surface_entry(led, post, clip)
    return found if isinstance(found, dict) else None


def _posted_ship(cfg: Config, led: Ledger, post) -> tuple[str, list[str], dict | None]:
    """IG/TT send decision: composed caption, the tags that compose appends, and the entry.

    Picks start as post.hashtags. When that caption is only a hashtag line and the
    clip's surface entry has a real sentence whose tags intersect the lock, the
    entry is the clip-fit allocation and replaces the stale post line. A real
    sentence on the post is kept; the entry's tags are used only when post.hashtags
    miss the lock entirely.

    Empty/missing lock → original post.caption (hashes included) and no appended tags.
    Non-empty lock and empty intersection → inline hashes stripped, nothing appended.
    YouTube does not call this. Wire contract: src/fanops/post/CLAUDE.md.
    """
    sentence, clip, src, lock = _lineage_bits(cfg, led, post)
    meas = _source_lock_measurements(cfg, src)
    picks = getattr(post, "hashtags", None) if post is not None else None
    entry = _bound_entry(led, post, clip, lock)
    if entry is not None:
        meta_picks = entry.get("hashtags") or []
        meta_sentence = (entry.get("caption") or "").strip()
        meta_hit = ship_from_lock(meta_picks, lock, artist_name=cfg.artist_name, measurements=meas)
        stale_line = (meta_hit and is_tags_only_caption(sentence) and meta_sentence
                      and not is_tags_only_caption(meta_sentence))
        if stale_line:
            picks = meta_picks
            sentence = meta_sentence
        elif meta_hit and not ship_from_lock(picks, lock, artist_name=cfg.artist_name,
                                              measurements=meas):
            picks = meta_picks
    tags = ship_from_lock(picks, lock, n=4, artist_name=cfg.artist_name, measurements=meas)
    if not lock:
        return (sentence or "").strip(), [], None
    if not tags:
        return compose_posted_caption(sentence, tags, drop_inline_tags=True), [], entry
    return compose_posted_caption(sentence, tags), tags, entry


def _youtube_tags(hashtags) -> list[dict]:
    # YouTube tags are bare keywords — strip a leading '#', dedupe, drop empties. Postiz's
    # @IsYoutubeTagsLength caps the total label length (~500); stop before it so a long hashtag list
    # can't 422 the whole post. Best-effort SEO, never load-bearing. THE tag shaper:
    # build_postiz_payload and send_caption both call this.
    out, used = [], 0
    for h in (hashtags or []):
        t = (h or "").lstrip("#").strip()
        if not t or any(t == o["label"] for o in out): continue
        if used + len(t) > 480: break
        out.append({"value": t, "label": t}); used += len(t)
    return out


def send_caption(cfg: Config, led: Ledger, post) -> tuple[str, list[str], dict | None]:
    """The one send decision: `(text, tags, entry)`.

    Instagram and TikTok return `_posted_ship`. YouTube returns `Post.caption` unchanged
    (no compose, no hash strip) and the labels `_youtube_tags` puts in Postiz `settings.tags`.
    The entry is the surface entry `_posted_ship` binds when a lock is present, else None.
    """
    if _platform_value(post) == "youtube":
        text = getattr(post, "caption", None) if post is not None else ""
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        labels = [row["label"] for row in _youtube_tags(
            getattr(post, "hashtags", None) if post is not None else None)]
        _sentence, clip, _src, lock = _lineage_bits(cfg, led, post)
        return text, labels, _bound_entry(led, post, clip, lock)
    return _posted_ship(cfg, led, post)


def posted_text_for(cfg: Config, led: Ledger, post) -> str:
    """IG/TT vendor content. The string half of `_posted_ship`."""
    text, _tags, _entry = _posted_ship(cfg, led, post)
    return text


def caption_shown(cfg: Config, led: Ledger, post) -> str:
    """Posted row and reconcile search: `Post.sent_content` when set, else the send text."""
    sent = getattr(post, "sent_content", None)
    if isinstance(sent, str):
        return sent
    text, _tags, _entry = send_caption(cfg, led, post)
    return text


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
