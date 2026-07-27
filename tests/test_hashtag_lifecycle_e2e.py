# tests/test_hashtag_lifecycle_e2e.py
# The WHOLE hashtag lifecycle pinned end-to-end, PLATFORM-TRUTH model: Layer A measures the persona's
# niche off the Graph (description -> anchors -> ONE top_media per tag -> like_count + co-occurring tags)
# -> Layer B derives the corpus from that cache with ZERO network -> the account hydrates it ->
# request_captions carries it -> ingest LEADS the vetted line with it and traces every shipped tag.
# SEVERANCE: an analyzed post's own reach plays ZERO role anywhere in this chain (a post's outcome
# attributes to the hook/clip/account, never to the hashtag).
# Slow UNIT (`@pytest.mark.slow` — deselect locally with `-m "not integration and not slow"`).
import json
import pytest
pytestmark = pytest.mark.slow
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, MomentState, ClipState, Platform,
                           CaptionSet, CaptionItem, Post, PostState)
from fanops.accounts import Accounts
from fanops import personas as core
from fanops.fanops_hashtags import refresh_store
from fanops.hashtags import METRIC_FIELD, load_measurements, ranked_tags
from fanops.persona_research import derive_corpus
from fanops.agentstep import response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _graph_router(metric_by_tag, *, cooccur=""):
    def get(url, params=None, timeout=None):
        p = params or {}
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            tag = "#" + url.rsplit("/", 2)[-2].replace("id-", "")
            if tag not in metric_by_tag:
                return _Resp(404, None)
            return _Resp(200, {"data": [{"caption": cooccur, "like_count": metric_by_tag[tag],
                                         "comments_count": 0}]})
        return _Resp(404, None)
    return get


def _clip(led):
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))


def test_hashtag_lifecycle_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)

    # 1 · a persona declares its niche, and an ACTIVE account carries it
    pid = core.add_persona(cfg, name="Hiphop", voice="any register", niche=["hiphop"], id="curator")
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))

    # 2 · Layer A: the anchor's top media yields its VERBATIM like_count + a tag nobody named
    get = _graph_router({"#hiphop": 500, "#detroitrap": 990}, cooccur="#detroitrap")
    refresh_store(cfg, get=get)
    cache = load_measurements(cfg)
    assert cache["#detroitrap"][METRIC_FIELD] == 990                 # discovered by co-occurrence, measured
    assert cache["#detroitrap"]["from"] == {"#hiphop": 1}            # attributed to the anchor that found it
    assert ranked_tags(cache)[0] == "#detroitrap"                    # the menu is platform-metric ranked

    # 3 · Layer B: the corpus is DERIVED from that cache, zero network, and hydrates onto the account
    assert derive_corpus(cfg, pid)["changed"] is True
    assert core.Personas.load(cfg).get(pid).hashtag_corpus[0] == "#detroitrap"
    accts = Accounts.load(cfg)
    assert "#detroitrap" in accts.accounts[0].hashtag_corpus         # hydrated onto the account at load

    # 4 · the caption request carries the corpus; ingest LEADS the vetted line with it + traces provenance
    _clip(led)
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=["#invented"])]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    tags = led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"]
    assert tags[0] == "#detroitrap"                            # the derived corpus leads the line
    assert "#invented" not in tags and len(tags) <= 4          # a tag nobody measured cannot ship
    sources = led.clips["clip_1"].meta_captions["a/instagram"]["tag_sources"]
    assert set(sources) == set(tags) and all(sources.values())
    assert sources["#detroitrap"] == "corpus"                  # every shipped tag traces to a real signal

    # 5 · SEVERANCE: an analyzed post with high OWN reach changes NOTHING about the cache or the corpus
    led.add_post(Post(id="post_1", parent_id="clip_1", account="a", account_id="1",
                      platform=Platform.instagram, caption=" ".join(tags), hashtags=tags,
                      state=PostState.analyzed, metrics={"reach": 8000}, public_url="dryrun://post_1"))
    led.save()
    refresh_store(cfg, get=get)                                # re-measure AFTER the analyzed post exists
    after = load_measurements(cfg)
    assert after["#detroitrap"][METRIC_FIELD] == 990           # the post's 8000 own-reach moved nothing
    assert after["#hiphop"][METRIC_FIELD] == 500
    assert derive_corpus(cfg, pid)["changed"] is False         # ...and the corpus is unmoved too
