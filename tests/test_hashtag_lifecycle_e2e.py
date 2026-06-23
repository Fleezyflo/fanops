# tests/test_hashtag_lifecycle_e2e.py
# B4 — the WHOLE hashtag lifecycle pinned end-to-end (the gap the foundation audit flagged: no test
# covered the full loop). Persona owns a curated corpus -> account links to the persona (hydration) ->
# request_captions carries the corpus -> ingest leads the vetted line with it -> a posted+analyzed post
# carries the corpus tag -> its reach is measured -> tag_reach_means surfaces it -> the Personas page
# shows that reach next to the curated tag (the closed loop, visible). Slow UNIT (no marker).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, MomentState, ClipState, Platform,
                           CaptionSet, CaptionItem, Post, PostState)
from fanops.accounts import Accounts
from fanops import personas as core
from fanops.fanops_hashtags import tag_reach_means
from fanops.studio import views
from fanops.agentstep import response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions


def _clip(led):
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4", state=ClipState.rendered))


def test_hashtag_lifecycle_end_to_end(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)

    # 1 · a persona owns a curated corpus, and an account LINKS to it
    pid = core.add_persona(cfg, name="Curator", voice="champions craft", tag_lean="tasteful")
    core.add_corpus_tag(cfg, pid, "#detroitrap")               # a niche tag the frozen set has never heard of
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "platforms": ["instagram"], "status": "active", "persona_id": pid}]}))
    accts = Accounts.load(cfg)
    assert accts.accounts[0].hashtag_corpus == ["#detroitrap"]   # A1+B1: hydrated onto the account at load

    # 2 · the caption request carries the corpus; ingest LEADS the vetted line with it
    _clip(led)
    request_captions(led, cfg, "clip_1", [("@a", Platform.instagram)], accounts=accts)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="@a/instagram", caption="x", hashtags=["#hiphop"])]).model_dump_json())
    ingest_captions(led, cfg, "clip_1")
    tags = led.clips["clip_1"].meta_captions["@a/instagram"]["hashtags"]
    assert tags[0] == "#detroitrap"                            # B1: the curated corpus leads, even over #hiphop

    # 3 · a posted + analyzed post carrying the corpus tag earns reach
    led.add_post(Post(id="post_1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption=" ".join(tags), hashtags=tags,
                      state=PostState.analyzed, metrics={"reach": 8000}))
    means = tag_reach_means(led)
    assert means["#detroitrap"] == 8000.0                      # B4: the curated tag's measured reach (closed loop)

    # 4 · the Personas page surfaces that reach next to the curated tag
    card = next(c for c in views.personas_page(cfg, led=led).personas if c.id == pid)
    assert card.reach_means.get("#detroitrap") == 8000.0       # the operator SEES that the curated tag earned reach
