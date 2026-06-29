# tests/test_hashtag_lifecycle_e2e.py
# The WHOLE hashtag lifecycle pinned end-to-end, GRAPH-REACH model (operator 2026-06-27): a persona owns a
# curated corpus -> an account links to it (hydration) -> request_captions carries the corpus -> ingest LEADS
# the vetted line with it (provenance traces every shipped tag) -> the STORE is rebuilt from LIVE Meta Graph
# reach (harvest co-occurring -> measure -> rank), NOT from any post. SEVERANCE: an analyzed post's own reach
# plays ZERO role in the store ranking (a post's outcome attributes to the hook/clip/account, never the tag).
# Slow UNIT (no marker).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, MomentState, ClipState, Platform,
                           CaptionSet, CaptionItem, Post, PostState)
from fanops.accounts import Accounts
from fanops import personas as core
from fanops.fanops_hashtags import refresh_store
from fanops.hashtags import load_store
from fanops.agentstep import response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions


class _Resp:
    def __init__(self, status=200, body=None): self.status_code = status; self._body = body
    def json(self):
        if self._body is None: raise ValueError("no json")
        return self._body


def _graph_router(reach_by_tag, *, cooccur=""):
    def get(url, params=None, timeout=None):
        p = params or {}
        if "ig_hashtag_search" in url:
            return _Resp(200, {"data": [{"id": "id-" + p.get("q", "")}]})
        if url.endswith("/top_media"):
            tag = "#" + url.rsplit("/", 2)[-2].replace("id-", "")
            return _Resp(200, {"data": [{"caption": cooccur, "like_count": reach_by_tag.get(tag, 0),
                                         "comments_count": 0}]})
        return _Resp(404, None)
    return get


def _clip(led):
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))


def test_hashtag_lifecycle_end_to_end(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)

    # 1 · a persona owns a curated corpus, and an account LINKS to it (hydration)
    pid = core.add_persona(cfg, name="Curator", voice="champions craft")
    core.add_corpus_tag(cfg, pid, "#detroitrap")               # a niche tag the frozen set has never heard of
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))
    accts = Accounts.load(cfg)
    assert accts.accounts[0].hashtag_corpus == ["#detroitrap"]   # A1+B1: hydrated onto the account at load

    # 2 · the caption request carries the corpus; ingest LEADS the vetted line with it + traces provenance
    _clip(led)
    request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)], accounts=accts)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="x", hashtags=["#hiphop"])]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    tags = led.clips["clip_1"].meta_captions["@a/instagram"]["hashtags"]
    assert tags[0] == "#detroitrap"                            # B1: the curated corpus leads, even over #hiphop
    sources = led.clips["clip_1"].meta_captions["@a/instagram"]["tag_sources"]
    assert set(sources) == set(tags) and all(sources.values())
    assert sources["#detroitrap"] == "corpus"                  # every shipped tag traces to a real signal

    # 3 · the STORE is judged by LIVE Graph reach (harvest the corpus seed's co-occurring tags -> measure -> rank)
    monkeypatch.setenv("META_GRAPH_TOKEN", "tok"); monkeypatch.setenv("META_IG_USER_ID", "ig")
    get = _graph_router({"#freshwave": 990}, cooccur="#freshwave")   # #detroitrap co-occurs with a high-reach tag
    refresh_store(cfg, get=get)
    store = load_store(cfg)
    assert store[0] == "#freshwave"                            # ranked by LIVE Graph reach — a tag we never named

    # 4 · SEVERANCE: an analyzed post with high OWN reach does NOT promote its hashtag in the store
    led.add_post(Post(id="post_1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption=" ".join(tags), hashtags=tags,
                      state=PostState.analyzed, metrics={"reach": 8000}, public_url="dryrun://post_1"))
    refresh_store(cfg, get=get)                                # rebuild AFTER the analyzed post exists
    store2 = load_store(cfg)
    assert store2[0] == "#freshwave"                           # the post's 8000 own-reach changed NOTHING
    assert store2.index("#freshwave") < store2.index("#detroitrap")   # graph reach leads; own-post reach is irrelevant
