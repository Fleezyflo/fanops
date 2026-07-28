# Shared instagrapi fakes for hashtag Layer A tests (no network).
from __future__ import annotations
from fanops.ig_hashtag_scrape import ScrapeRefused, ScrapeThrottled


class _Media:
    def __init__(self, like_count=None, caption_text="", play_count=None):
        self.like_count = like_count; self.caption_text = caption_text
        self.play_count = play_count


class _Hashtag:
    def __init__(self, id, media_count=None):
        self.id = id; self.media_count = media_count


class _FakeClient:
    """Fake instagrapi Client. `metric_by_tag` maps '#tag' -> like_count (simple) OR a list of
    `_Media` / dicts with like_count/play_count/caption(_text). `cooccur` is the default caption when
    using scalar metrics. `media_count_by_tag` seeds hashtag_info.media_count. `refuse` /
    `refuse_tags` raise ScrapeRefused; `throttle_after` raises ScrapeThrottled after N medias_top calls."""
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
                        m.get("play_count")))
                else:
                    out.append(m)
            return out
        if tag not in self.metric_by_tag:
            return []
        # Other keys in this fake's metric map ride the caption so measuring a harvested cotag can
        # inbound-see the niche anchors (production: niche words appear on the candidate's Top).
        others = " ".join(sorted(t for t in self.metric_by_tag if t != tag))
        cap = f"{self.cooccur} {others}".strip()
        # Two Top rows so inbound `from` hits can clear MOL-665 relatedness (hits>=2).
        return [_Media(self.metric_by_tag[tag], cap), _Media(self.metric_by_tag[tag], cap)]

    def hashtag_info(self, name):
        self.info_calls.append(name)
        if self.refuse is not None:
            raise self.refuse if isinstance(self.refuse, BaseException) else ScrapeRefused(str(self.refuse))
        tag = "#" + name
        if tag in self.refuse_tags or name in self.refuse_tags:
            raise ScrapeRefused("refused", code=18)
        mc = self.media_count_by_tag.get(tag, self.media_count_by_tag.get(name))
        return _Hashtag("id-" + name, media_count=mc)

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
