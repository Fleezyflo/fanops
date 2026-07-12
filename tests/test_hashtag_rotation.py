"""S06: deterministic per-account hashtag rotation — recency demotion in vet_hashtags, same-pass
accumulation in pipeline ingest, read-only tag exposure on Posted."""
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, Moment, Source, MomentState, ClipState, Platform,
                           CaptionSet, CaptionItem, Post, PostState)
from fanops.agentstep import response_path, latest_request_id
from fanops.caption import request_captions, ingest_captions
from fanops.hashtags import vet_hashtags
from fanops.studio.views_results import tag_exposure


def _clip(led, clip_id="clip_1", moment_id="mom_1"):
    if "src_1" not in led.sources:
        led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    if moment_id not in led.moments:
        led.add_moment(Moment(id=moment_id, parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", transcript_excerpt="they slept on me", state=MomentState.decided))
    led.add_clip(Clip(id=clip_id, parent_id=moment_id, path=f"/{clip_id}.mp4", state=ClipState.rendered))


def _ingest(cfg, led, clip_id, hashtags=None, surface="a/instagram"):
    led = request_captions(led, cfg, clip_id, [("a", Platform.instagram)])
    rid = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface=surface, caption="x", hashtags=hashtags or ["#hiphop"])]).model_dump_json())
    return ingest_captions(led, cfg, clip_id)


def test_recent_none_byte_identical_matrix():
    cases = [(["#hiphop", "#bars"], Platform.tiktok, "en", None),
             ([], Platform.instagram, "en", ["#lyrics", "#bars", "#newmusic"]),
             (["#undergroundhiphop", "#hiphop"], Platform.tiktok, "ar", ["#viral", "#rapmusic"])]
    for tags, plat, lang, corpus in cases:
        base = vet_hashtags(tags, plat, lang, corpus=corpus)
        assert vet_hashtags(tags, plat, lang, corpus=corpus, recent=None) == base
        assert vet_hashtags(tags, plat, lang, corpus=corpus, recent=[]) == base


def test_recency_demotes_within_corpus_tier():
    corpus = ["#alpha", "#beta", "#gamma", "#delta"]
    fresh = vet_hashtags(None, Platform.instagram, "en", corpus=corpus)
    rotated = vet_hashtags(None, Platform.instagram, "en", corpus=corpus, recent=["#alpha"])
    assert fresh[0] == "#alpha"
    assert rotated[0] != "#alpha"
    assert "#alpha" in rotated


def test_consecutive_ingests_differ(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    corpus = ["#alpha", "#beta", "#gamma", "#delta", "#epsilon"]
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active",
         "hashtag_corpus": corpus}]}))
    accts = Accounts.load(cfg)
    _clip(led, "clip_1")
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=["#hiphop"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    tags1 = list(led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"])
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption=" ".join(tags1), hashtags=tags1, state=PostState.queued,
                      created_at="2026-07-01T12:00:00+00:00"))
    _clip(led, "clip_2", "mom_2")
    request_captions(led, cfg, "clip_2", [("a", Platform.instagram)], accounts=accts)
    rid2 = latest_request_id(cfg, "captions", "clip_2")
    response_path(cfg, "captions", "clip_2").write_text(CaptionSet(request_id=rid2, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=["#hiphop"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_2")
    tags2 = list(led.clips["clip_2"].meta_captions["a/instagram"]["hashtags"])
    assert tags1 != tags2


def test_pass_local_same_pass(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    corpus = ["#alpha", "#beta", "#gamma", "#delta", "#epsilon"]
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active",
         "hashtag_corpus": corpus}]}))
    accts = Accounts.load(cfg)
    _clip(led, "clip_1")
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=["#hiphop"])]).model_dump_json())
    pass_recent: dict[str, list[str]] = {}
    led = ingest_captions(led, cfg, "clip_1", pass_recent=pass_recent)
    tags1 = list(led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"])
    _clip(led, "clip_2", "mom_2")
    request_captions(led, cfg, "clip_2", [("a", Platform.instagram)], accounts=accts)
    rid2 = latest_request_id(cfg, "captions", "clip_2")
    response_path(cfg, "captions", "clip_2").write_text(CaptionSet(request_id=rid2, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=["#hiphop"])]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_2", pass_recent=pass_recent)
    tags2 = list(led.clips["clip_2"].meta_captions["a/instagram"]["hashtags"])
    assert tags1 != tags2
    assert pass_recent.get("a")


def test_ar_floor_survives_rotation():
    corpus = ["#viral", "#rapmusic", "#hiphop", "#bars"]
    out = vet_hashtags(["#viral", "#hiphop", "#rap", "#rapper"], Platform.tiktok, "ar",
                       corpus=corpus, recent=["#viral"])
    assert len(out) == 4 and any("arab" in t for t in out)


def test_discovery_floor_survives_rotation():
    from fanops.hashtags import _DISCOVERY, _DISCOVERY_DEFAULT
    disc = set(_DISCOVERY[Platform.instagram]) | set(_DISCOVERY_DEFAULT)
    corpus = ["#myscene", "#another", "#third"]
    out = vet_hashtags([], Platform.instagram, "en", corpus=corpus, recent=["#myscene"])
    assert any(t in disc for t in out)


def test_full_pool_coverage_walk():
    corpus = ["#alpha", "#beta", "#gamma", "#delta", "#epsilon"]
    recent: list[str] = []
    seen: set[str] = set()
    for _ in range(len(corpus) * 3):
        out = vet_hashtags(None, Platform.tiktok, "en", corpus=corpus, recent=recent)
        seen.update(out)
        recent = list(out)
    assert seen >= set(corpus)


def test_twelve_tag_corpus_three_passes_disjoint_leaning(tmp_path):
    """S12: a 12-tag corpus rotated across three consecutive vet/ingest passes yields disjoint-leaning lines."""
    from fanops.models import Platform, Post, PostState
    from fanops.accounts import Accounts
    corpus = [f"#tag{i:02d}" for i in range(12)]
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active", "hashtag_corpus": corpus}]}))
    accts = Accounts.load(cfg)
    lines: list[list[str]] = []
    for i in range(3):
        cid = f"clip_{i}"; mid = f"mom_{i}"
        _clip(led, cid, mid)
        request_captions(led, cfg, cid, [("a", Platform.instagram)], accounts=accts)
        rid = latest_request_id(cfg, "captions", cid)
        response_path(cfg, "captions", cid).write_text(CaptionSet(request_id=rid, items=[
            CaptionItem(surface="a/instagram", caption="x", hashtags=["#hiphop"])]).model_dump_json())
        led = ingest_captions(led, cfg, cid)
        tags = list(led.clips[cid].meta_captions["a/instagram"]["hashtags"])
        lines.append(tags)
        led.add_post(Post(id=f"p{i}", parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
                          caption=" ".join(tags), hashtags=tags, state=PostState.queued,
                          created_at=f"2026-07-0{i+1}T12:00:00+00:00"))
        if i:
            assert lines[i] != lines[i - 1]
    assert len({tuple(x) for x in lines}) >= 2


def test_tag_exposure_counts(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                      caption="#hiphop", hashtags=["#hiphop", "#rap"], state=PostState.queued,
                      created_at="2026-07-01T12:00:00+00:00"))
    led.add_post(Post(id="p2", parent_id="c2", account="a", account_id="1", platform=Platform.instagram,
                      caption="#hiphop", hashtags=["#hiphop", "#bars"], state=PostState.queued,
                      created_at="2026-07-02T12:00:00+00:00"))
    led.add_post(Post(id="p3", parent_id="c3", account="b", account_id="1", platform=Platform.instagram,
                      caption="#rap", hashtags=["#rap"], state=PostState.awaiting_approval,
                      created_at="2026-07-03T12:00:00+00:00"))
    led.add_post(Post(id="p4", parent_id="c4", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", hashtags=["#junk"], state=PostState.rejected,
                      created_at="2026-07-04T12:00:00+00:00"))
    exp = tag_exposure(led)
    assert exp["a"] == [("#hiphop", 2), ("#bars", 1), ("#rap", 1)]
    assert exp["b"] == [("#rap", 1)]
    assert "#junk" not in {t for t, _ in exp.get("a", [])}
