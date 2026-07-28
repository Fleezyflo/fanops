# Shared instagrapi fakes for hashtag Layer A tests (no network).
from __future__ import annotations
from fanops.ig_hashtag_scrape import ScrapeRefused, ScrapeThrottled


class _Media:
    def __init__(self, like_count=None, caption_text=""):
        self.like_count = like_count; self.caption_text = caption_text


class _Hashtag:
    def __init__(self, id): self.id = id


class _FakeClient:
    """Fake instagrapi Client. `metric_by_tag` maps '#tag' -> like_count (simple) OR a list of
    `_Media` / dicts with like_count/caption(_text). `cooccur` is the default caption when using
    scalar metrics. `refuse` / `refuse_tags` raise ScrapeRefused; `throttle_after` raises
    ScrapeThrottled after N medias_top calls."""
    def __init__(self, metric_by_tag=None, *, cooccur="", refuse=None, refuse_tags=None,
                 throttle_after=None, media_by_tag=None):
        self.metric_by_tag = dict(metric_by_tag or {})
        self.media_by_tag = dict(media_by_tag or {})
        self.cooccur = cooccur
        self.refuse = refuse
        self.refuse_tags = set(refuse_tags or ())
        self.throttle_after = throttle_after
        self.info_calls: list[str] = []
        self.media_calls: list[str] = []
        self._media_n = 0

    def _medias_for(self, name: str):
        tag = "#" + name
        if tag in self.media_by_tag:
            out = []
            for m in self.media_by_tag[tag]:
                if isinstance(m, _Media):
                    out.append(m)
                elif isinstance(m, dict):
                    out.append(_Media(m.get("like_count"), m.get("caption_text") or m.get("caption") or ""))
                else:
                    out.append(m)
            return out
        if tag not in self.metric_by_tag:
            return []
        return [_Media(self.metric_by_tag[tag], self.cooccur)]

    def hashtag_info(self, name):
        self.info_calls.append(name)
        if self.refuse is not None:
            raise self.refuse if isinstance(self.refuse, BaseException) else ScrapeRefused(str(self.refuse))
        tag = "#" + name
        if tag in self.refuse_tags or name in self.refuse_tags:
            raise ScrapeRefused("refused", code=18)
        return _Hashtag("id-" + name)

    def hashtag_medias_top(self, name, amount=9):
        self.media_calls.append(name)
        self._media_n += 1
        if self.throttle_after is not None and self._media_n > self.throttle_after:
            raise ScrapeThrottled("please_wait")
        if self.refuse is not None:
            raise self.refuse if isinstance(self.refuse, BaseException) else ScrapeRefused(str(self.refuse))
        tag = "#" + name
        if tag in self.refuse_tags or name in self.refuse_tags:
            raise ScrapeRefused("refused", code=18)
        return self._medias_for(name)
