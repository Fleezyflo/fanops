# Shared instagrapi fakes for hashtag Layer A tests (no network).
from __future__ import annotations
try:
    from instagrapi.exceptions import ClientNotFoundError, RateLimitError
except ImportError:  # base install: collection still works; tests needing these skip via importorskip
    ClientNotFoundError = None  # type: ignore[misc, assignment]
    RateLimitError = None  # type: ignore[misc, assignment]


class _Media:
    """One Top row. `product_type` / `taken_at` are Instagram's own Reel markers (MOL-691): only a
    `clips` row dated inside 7 days feeds `current_top_reel_play_max_7d`."""
    def __init__(self, like_count=None, caption_text="", play_count=None,
                 product_type=None, taken_at=None):
        self.like_count = like_count; self.caption_text = caption_text
        self.play_count = play_count
        self.product_type = product_type; self.taken_at = taken_at


class _Hashtag:
    def __init__(self, id, media_count=None):
        self.id = id; self.media_count = media_count


def _client_not_found():
    if ClientNotFoundError is None:
        raise RuntimeError("instagrapi required for refuse_tags fakes")
    # ClientError setattr from last_json — give a code like production
    return ClientNotFoundError(message="not found", code=18, status="fail")


def _rate_limit():
    if RateLimitError is None:
        raise RuntimeError("instagrapi required for throttle_after fakes")
    return RateLimitError(**{"message": "", "error_type": "rate_limit_error", "status": "fail"})


class _FakeClient:
    """Fake instagrapi Client. `refuse_tags` raise ClientNotFoundError (tag 404 continue-fork);
    `throttle_after` raises RateLimitError after N medias_top calls."""
    def __init__(self, metric_by_tag=None, *, cooccur="", refuse=None, refuse_tags=None,
                 throttle_after=None, media_by_tag=None, media_count_by_tag=None):
        self.metric_by_tag = dict(metric_by_tag or {})
        self.media_by_tag = dict(media_by_tag or {})
        self.media_count_by_tag = dict(media_count_by_tag or {})
        self.cooccur = cooccur
        self.refuse = refuse
        self.refuse_tags = set(refuse_tags or ())
        self.throttle_after = throttle_after
        self.info_calls: list[str] = []
        self.media_calls: list[str] = []
        self.amounts: list[int] = []
        self._media_n = 0

    def _medias_for(self, name: str):
        tag = "#" + name
        if tag in self.media_by_tag:
            out = []
            for m in self.media_by_tag[tag]:
                if isinstance(m, _Media):
                    out.append(m)
                elif isinstance(m, dict):
                    out.append(_Media(
                        m.get("like_count"),
                        m.get("caption_text") or m.get("caption") or "",
                        m.get("play_count"),
                        m.get("product_type"), m.get("taken_at")))
                else:
                    out.append(m)
            return out
        if tag not in self.metric_by_tag:
            return []
        others = " ".join(sorted(t for t in self.metric_by_tag if t != tag))
        cap = f"{self.cooccur} {others}".strip()
        return [_Media(self.metric_by_tag[tag], cap), _Media(self.metric_by_tag[tag], cap)]

    def hashtag_info(self, name):
        self.info_calls.append(name)
        if self.refuse is not None:
            raise self.refuse if isinstance(self.refuse, BaseException) else _client_not_found()
        tag = "#" + name
        if tag in self.refuse_tags or name in self.refuse_tags:
            raise _client_not_found()
        mc = self.media_count_by_tag.get(tag, self.media_count_by_tag.get(name))
        return _Hashtag("id-" + name, media_count=mc)

    def hashtag_medias_top(self, name, amount=9):
        self.media_calls.append(name)
        self.amounts.append(amount)
        self._media_n += 1
        if self.throttle_after is not None and self._media_n > self.throttle_after:
            raise _rate_limit()
        if self.refuse is not None:
            raise self.refuse if isinstance(self.refuse, BaseException) else _client_not_found()
        tag = "#" + name
        if tag in self.refuse_tags or name in self.refuse_tags:
            raise _client_not_found()
        return self._medias_for(name)
