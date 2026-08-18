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
from fanops.source_tags import source_tag_locks_path
from fanops.studio.views_results import tag_exposure


def _clip(led, clip_id="clip_1", moment_id="mom_1", transcript="they slept on me"):
    if "src_1" not in led.sources:
        led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    if moment_id not in led.moments:
        led.add_moment(Moment(id=moment_id, parent_id="src_1", content_token="0-7", start=0, end=7,
                              reason="r", transcript_excerpt=transcript, state=MomentState.decided))
    led.add_clip(Clip(id=clip_id, parent_id=moment_id, path=f"/{clip_id}.mp4", state=ClipState.rendered))


def _write_meas_tags(cfg, tags):
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        t: {"graph_id": f"g{t}", "like_count": 100, "media_count": 1000.0,
            "measured_at": "2026-07-01T00:00:00+00:00"} for t in tags
    }))


def _write_lock(cfg, sid, lock):
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        sid: {"pile": list(lock), "lock": list(lock), "researched_at": "2026-08-17T00:00:00Z"},
    }))


def _ingest(cfg, led, clip_id, hashtags=None, surface="a/instagram", *, hashtag_store=None):
    # MOL-511: fixtures may pin hashtag_store on the request surface (persona-scoped vet menu).
    led = request_captions(led, cfg, clip_id, [("a", Platform.instagram)])
    if hashtag_store is not None:
        from fanops.agentstep import request_path
        rp = request_path(cfg, "captions", clip_id)
        req = json.loads(rp.read_text())
        for s in req["surfaces"]:
            if s["surface"] == surface:
                s["hashtag_store"] = list(hashtag_store)
        rp.write_text(json.dumps(req))
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


def test_consecutive_ingests_same_picks_same_lock(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    corpus = ["#alpha", "#beta", "#gamma", "#delta", "#epsilon"]
    _write_meas_tags(cfg, corpus)
    _write_lock(cfg, "src_1", corpus)
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active",
         "hashtag_corpus": ["#personaonly"]}]}))
    accts = Accounts.load(cfg)
    _clip(led, "clip_1", transcript="alpha beta gamma delta epsilon")
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=list(corpus))]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_1")
    tags1 = list(led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"])
    led.add_post(Post(id="p1", parent_id="clip_1", account="a", account_id="1", platform=Platform.instagram,
                      caption=" ".join(tags1), hashtags=tags1, state=PostState.queued,
                      created_at="2026-07-01T12:00:00+00:00"))
    _clip(led, "clip_2", "mom_2", transcript="alpha beta gamma delta epsilon")
    request_captions(led, cfg, "clip_2", [("a", Platform.instagram)], accounts=accts)
    rid2 = latest_request_id(cfg, "captions", "clip_2")
    response_path(cfg, "captions", "clip_2").write_text(CaptionSet(request_id=rid2, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=list(corpus))]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_2")
    tags2 = list(led.clips["clip_2"].meta_captions["a/instagram"]["hashtags"])
    assert tags1 == tags2
    assert tags1 == corpus[:4]


def test_pass_local_same_pass(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    corpus = ["#alpha", "#beta", "#gamma", "#delta", "#epsilon"]
    _write_meas_tags(cfg, corpus)
    _write_lock(cfg, "src_1", corpus)
    from fanops.accounts import Accounts
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active"}]}))
    accts = Accounts.load(cfg)
    _clip(led, "clip_1", transcript="alpha beta gamma delta epsilon")
    request_captions(led, cfg, "clip_1", [("a", Platform.instagram)], accounts=accts)
    rid = latest_request_id(cfg, "captions", "clip_1")
    response_path(cfg, "captions", "clip_1").write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=list(corpus))]).model_dump_json())
    pass_recent: dict[str, list[str]] = {}
    led = ingest_captions(led, cfg, "clip_1", pass_recent=pass_recent)
    tags1 = list(led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"])
    _clip(led, "clip_2", "mom_2", transcript="alpha beta gamma delta epsilon")
    request_captions(led, cfg, "clip_2", [("a", Platform.instagram)], accounts=accts)
    rid2 = latest_request_id(cfg, "captions", "clip_2")
    response_path(cfg, "captions", "clip_2").write_text(CaptionSet(request_id=rid2, items=[
        CaptionItem(surface="a/instagram", caption="x", hashtags=list(corpus))]).model_dump_json())
    led = ingest_captions(led, cfg, "clip_2", pass_recent=pass_recent)
    tags2 = list(led.clips["clip_2"].meta_captions["a/instagram"]["hashtags"])
    assert tags1 == tags2
    assert tags1 == corpus[:4]
    assert pass_recent.get("a") == corpus[:4] + corpus[:4]


def test_ar_floor_survives_rotation():
    corpus = ["#viral", "#rapmusic", "#hiphop", "#bars"]
    out = vet_hashtags(["#viral", "#hiphop", "#rap", "#rapper"], Platform.tiktok, "ar",
                       corpus=corpus, recent=["#viral"])
    assert len(out) == 4 and any("arab" in t for t in out)


def test_rotation_does_not_pad_with_discovery():
    # discovery floor deleted: rotation stays inside the corpus; no #reels/#fyp pad.
    corpus = ["#myscene", "#another", "#third"]
    out = vet_hashtags([], Platform.instagram, "en", corpus=corpus, recent=["#myscene"])
    assert set(out) <= set(corpus)
    assert not any(t in ("#reels", "#fyp", "#foryou", "#viral") for t in out)


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
    """S12: source-measured transcript tags rotated across three ingest passes yield disjoint-leaning lines."""
    from fanops.models import Platform, Post, PostState
    from fanops.accounts import Accounts
    corpus = [f"#tag{i:02d}" for i in range(6)]          # content_tag_candidates caps at 6
    transcript = " ".join(f"tag{i:02d}" for i in range(6))
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    _write_meas_tags(cfg, corpus)
    _write_lock(cfg, "src_1", corpus)
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "a", "platforms": ["instagram"], "status": "active"}]}))
    accts = Accounts.load(cfg)
    lines: list[list[str]] = []
    for i in range(3):
        cid = f"clip_{i}"; mid = f"mom_{i}"
        _clip(led, cid, mid, transcript=transcript)
        request_captions(led, cfg, cid, [("a", Platform.instagram)], accounts=accts)
        rid = latest_request_id(cfg, "captions", cid)
        response_path(cfg, "captions", cid).write_text(CaptionSet(request_id=rid, items=[
            CaptionItem(surface="a/instagram", caption="x", hashtags=list(corpus))]).model_dump_json())
        led = ingest_captions(led, cfg, cid)
        tags = list(led.clips[cid].meta_captions["a/instagram"]["hashtags"])
        lines.append(tags)
        led.add_post(Post(id=f"p{i}", parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
                          caption=" ".join(tags), hashtags=tags, state=PostState.queued,
                          created_at=f"2026-07-0{i+1}T12:00:00+00:00"))
        assert tags == corpus[:4]
    assert len({tuple(x) for x in lines}) == 1



def test_ingest_rotation_uses_surface_hashtag_store(tmp_path):
    """Request hashtag_store is not membership. Sidecar lock is. A 141-tag request
    plus a foreign request store cannot ship off-lock tags."""
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#alpha": {"graph_id": "1", "like_count": 900, "measured_at": "2026-07-01T00:00:00+00:00"},
        "#foreign": {"graph_id": "2", "like_count": 9999, "measured_at": "2026-07-01T00:00:00+00:00"},
    }))
    _write_lock(cfg, "src_1", ["#alpha"])
    _clip(led, "clip_1")
    led = _ingest(cfg, led, "clip_1", hashtags=["#alpha", "#foreign"],
                  hashtag_store=["#foreign"] + [f"#req{i}" for i in range(140)])
    tags = led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"]
    assert tags == ["#alpha"]
    assert "#foreign" not in tags


def test_ingest_empty_surface_store_short_line(tmp_path):
    """MOL-511: empty hashtag_store on the request surface -> short discovery line (not global pad)."""
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.hashtags_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.hashtags_path.write_text(json.dumps({
        "#hiphop": {"graph_id": "1", "like_count": 900, "measured_at": "2026-07-01T00:00:00+00:00"},
    }))
    _clip(led, "clip_1")
    led = _ingest(cfg, led, "clip_1", hashtags=["#hiphop", "#rap"], hashtag_store=[])
    assert led.clips["clip_1"].meta_captions["a/instagram"]["hashtags"] == []


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
