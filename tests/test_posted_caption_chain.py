"""The posted caption is one sentence plus at most four tags from the source lock.

Transcript + scrape client finishes the lock; caption request, ingest, mint, Studio
review, dryrun, Postiz content, and Zernio content all carry that same line.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from fanops.accounts import Accounts
from fanops.agentstep import latest_request_id, request_path, response_path
from fanops.caption import ingest_captions, request_captions
from fanops.caption_compose import posted_text_for
from fanops.config import Config
from fanops.crosspost import crosspost_clips
from fanops.hashtags import play_rank_key
from fanops.ig_hashtag_scrape import ScrapeUnavailable
from fanops.ledger import Ledger
from fanops.models import (CaptionItem, CaptionSet, Clip, ClipState, Moment, MomentState,
                           Platform, Post, PostState, Source)
from fanops.recaption import _seed_clip_id
from fanops.source_tags import load_source_tag_locks, lock_ready_sources, shortlist_source_tags
from fanops.source_tags import source_tag_locks_path
from fanops.studio.views_review import _surface
from fanops.tagging import should_tag
from hashtag_scrape_fakes import _FakeClient, _Media

NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)
SENTENCE = "They slept on the pocket."


class _R:
    def __init__(self, code, body):
        self.status_code = code
        self._b = body
        self.text = ""

    def json(self):
        return self._b


class _Hit:
    def __init__(self, name):
        self.name = name


class _Client(_FakeClient):
    def __init__(self, search, **kw):
        super().__init__(**kw)
        self._search = dict(search)

    def search_hashtags(self, query):
        return list(self._search.get(query, []))


def _untagged_clip_id():
    for i in range(40):
        cid = f"seed{i}"
        if not should_tag(cid, "a"):
            return cid
    raise AssertionError("no clip id avoids the artist mention")


def _transcript(cfg, source_path: str, text: str) -> None:
    p = cfg.agent_io / "transcripts" / f"{Path(source_path).stem}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": text}]}))


def _accounts(cfg):
    cfg.accounts_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.accounts_path.write_text(json.dumps({"accounts": [
        {"handle": "@a", "account_id": "98432", "platforms": ["instagram"], "status": "active"},
    ]}))


def _lineage(cfg, *, sid="src_1", clip_id="seed0", clip_state=ClipState.rendered):
    src_path = str(cfg.root / "session.mp4")
    led = Ledger.load(cfg)
    led.add_source(Source(id=sid, source_path=src_path, language="en"))
    led.add_moment(Moment(id="mom_1", parent_id=sid, content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped, transcript_excerpt=SENTENCE))
    led.add_clip(Clip(id=clip_id, parent_id="mom_1", path=str(cfg.root / "clip.mp4"),
                      state=clip_state))
    led.save()
    _transcript(cfg, src_path, SENTENCE)
    return led, sid, clip_id


def _vendor_text(cfg, led, post, monkeypatch, mocker):
    from fanops.post.dryrun import DryRunPoster
    from fanops.post.postiz import PostizIntegration, PostizPoster
    from fanops.post.zernio import ZernioPoster

    DryRunPoster(cfg).publish(led, post.id)
    dry = json.loads((cfg.scheduled / f"{post.id}.json").read_text())["text"]
    post.media_urls = ["https://uploads.postiz.com/x.mp4"]
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    mocker.patch("fanops.post.postiz.postiz_list_integrations",
                 return_value=[PostizIntegration(post.account_id, "a", "instagram")])
    mocker.patch("fanops.post.postiz.PostizPoster._existing_submission_for_payload",
                 return_value=(None, None))
    sent: list[tuple[str, dict]] = []

    def _post(url, **kw):
        sent.append((url, kw.get("json") or {}))
        return _R(201, {"id": "ok", "_id": "ok"})

    # postiz and zernio share the requests module; one patch covers both POSTs.
    mocker.patch("fanops.post.postiz.requests.post", side_effect=_post)
    PostizPoster(cfg).publish(led, post.id)
    ZernioPoster(cfg)._create(led.posts[post.id], led)
    postiz = next(body["posts"][0]["value"][0]["content"]
                  for url, body in sent if "postiz" in url)
    zernio = next(body["content"] for url, body in sent if "zernio" in url)
    return dry, postiz, zernio


def test_lock_to_vendor_is_one_sentence_and_those_tags(tmp_path, mocker, monkeypatch):
    cfg = Config(root=tmp_path)
    _accounts(cfg)
    clip_id = _untagged_clip_id()
    led, sid, clip_id = _lineage(cfg, clip_id=clip_id)
    names = [f"tag{i:02d}" for i in range(14)]
    miss, zero = "tag02", "tag05"
    client = _Client(
        {n: [_Hit(n)] for n in names if n != miss},
        media_by_tag={
            f"#{n}": [_Media(1, "", play_count=(0 if n == zero else i + 1))]
            for i, n in enumerate(names)
        },
    )

    def _claude(prompt, schema, **_k):
        assert SENTENCE in prompt
        return {"keep": list(names)}, "m", False

    mocker.patch("fanops.llm.claude_json_meta", _claude)
    lock_ready_sources(cfg, client=client,
                       research_fn=lambda s, e: shortlist_source_tags(s, e, names))
    from fanops.hashtags import lock_from_pile, play_rank_key
    rec = load_source_tag_locks(cfg)[sid]
    meas = {
        f"#{n}": {"play_count": (0 if n == zero else i + 1)}
        for i, n in enumerate(names[:12]) if n != miss
    }
    admitted = lock_from_pile([f"#{n}" for n in names[:12]], meas)
    assert isinstance(rec.get("researched_at"), str) and rec["researched_at"].strip()
    assert rec["lock"] == admitted
    assert len(rec["lock"]) <= 12
    assert f"#{miss}" not in rec["lock"] and f"#{zero}" not in rec["lock"]
    assert "#tag12" not in rec["lock"] and "#tag13" not in rec["lock"]

    led = request_captions(led, cfg, clip_id, [("a", Platform.instagram)])
    payload = json.loads(request_path(cfg, "captions", clip_id).read_text())
    assert payload["surfaces"][0]["hashtag_store"] == admitted

    picks = [admitted[0], "#offlock", admitted[2], admitted[1], admitted[3], admitted[4]]
    capped = [admitted[0], admitted[2], admitted[1], admitted[3]]
    shipped = sorted(capped, key=lambda t: play_rank_key(t, meas.get(t)))
    rid = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption=SENTENCE, language="en", hashtags=picks),
    ]).model_dump_json())
    led = ingest_captions(led, cfg, clip_id)
    mc = led.clips[clip_id].meta_captions["a/instagram"]
    assert mc["caption"] == SENTENCE
    assert mc["hashtags"] == shipped
    assert mc["tag_sources"] == {t: "lock" for t in shipped}
    assert "#offlock" not in mc["hashtags"] and admitted[4] not in mc["hashtags"]

    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    post = next(p for p in led.posts.values() if p.platform is Platform.instagram)
    want = SENTENCE + "\n" + " ".join(shipped)
    assert post.caption == SENTENCE
    assert post.hashtags == shipped
    surface = _surface(post, persona=None, now=NOW, cfg=cfg, led=led)
    assert surface.caption == SENTENCE
    assert surface.approve_caption == want
    assert surface.hashtags == shipped
    assert surface.tag_sources == {t: "lock" for t in shipped}
    lock_meas = load_source_tag_locks(cfg)[sid]["measurements"]
    assert [row.tag for row in surface.shipped_tags] == shipped
    for row in surface.shipped_tags:
        assert row.play_count == lock_meas[row.tag]["play_count"]
    dry, postiz, zernio = _vendor_text(cfg, led, post, monkeypatch, mocker)
    assert dry == postiz == zernio == want
    assert "#offlock" not in want and admitted[4] not in want


def test_finished_empty_lock_ships_sentence_without_tags(tmp_path, mocker, monkeypatch):
    cfg = Config(root=tmp_path)
    _accounts(cfg)
    led, sid, clip_id = _lineage(cfg, clip_id=_untagged_clip_id())
    client = _Client({"quiet": [_Hit("quiet")]},
                     media_by_tag={"#quiet": [_Media(1, "", play_count=0)]})
    mocker.patch("fanops.llm.claude_json_meta",
                 lambda prompt, schema, **_k: ({"keep": ["quiet"]}, "m", False))
    lock_ready_sources(cfg, client=client,
                       research_fn=lambda s, e: shortlist_source_tags(s, e, ["quiet"]))
    rec = load_source_tag_locks(cfg)[sid]
    assert rec["lock"] == []
    assert isinstance(rec.get("researched_at"), str) and rec["researched_at"].strip()

    led = request_captions(led, cfg, clip_id, [("a", Platform.instagram)])
    assert request_path(cfg, "captions", clip_id).exists()
    req = json.loads(request_path(cfg, "captions", clip_id).read_text())
    assert "hashtag_store" not in req["surfaces"][0]
    rid = latest_request_id(cfg, "captions", clip_id)
    response_path(cfg, "captions", clip_id).write_text(CaptionSet(request_id=rid, items=[
        CaptionItem(surface="a/instagram", caption=SENTENCE, language="en",
                    hashtags=["#offlock", "#quiet"]),
    ]).model_dump_json())
    led = ingest_captions(led, cfg, clip_id)
    mc = led.clips[clip_id].meta_captions["a/instagram"]
    assert mc["caption"] == SENTENCE and mc["hashtags"] == [] and mc["tag_sources"] == {}
    led = crosspost_clips(led, cfg, Accounts.load(cfg), base_time="2026-06-02T18:00:00Z")
    post = next(iter(led.posts.values()))
    assert post.caption == SENTENCE and post.hashtags == []
    surface = _surface(post, persona=None, now=NOW, cfg=cfg, led=led)
    assert surface.caption == SENTENCE and surface.approve_caption == SENTENCE
    assert surface.hashtags == [] and surface.tag_sources == {}
    dry, postiz, zernio = _vendor_text(cfg, led, post, monkeypatch, mocker)
    assert dry == postiz == zernio == SENTENCE
    assert "#" not in SENTENCE


def test_missing_researched_at_opens_empty_caption_menu(tmp_path):
    cfg = Config(root=tmp_path)
    led, sid, clip_id = _lineage(cfg)

    def _closed(_cfg, user=None, **_k):
        raise ScrapeUnavailable("no scrape profile session")

    lock_ready_sources(cfg, open_client_fn=_closed,
                       research_fn=lambda s, e: shortlist_source_tags(s, e, []))
    assert sid not in load_source_tag_locks(cfg)
    led = request_captions(led, cfg, clip_id, [("a", Platform.instagram)])
    assert request_path(cfg, "captions", clip_id).exists()
    assert led.clips[clip_id].state is ClipState.captions_requested
    req = json.loads(request_path(cfg, "captions", clip_id).read_text())
    assert "hashtag_store" not in req["surfaces"][0]


def test_longer_aspect_tag_list_does_not_replace_seed_sentence(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    seed_tags = ["#keep", "#also"]
    ranked = sorted(seed_tags, key=lambda t: play_rank_key(t, None))
    longer = seed_tags + ["#extra", "#more"]
    lock = longer
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "src_1": {"pile": lock, "lock": lock, "researched_at": "2026-08-17T00:00:00Z"},
    }))
    led.add_source(Source(id="src_1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-7", start=0, end=7,
                          reason="r", state=MomentState.clipped))
    seed_sentence = "Seed sentence stays."
    led.add_clip(Clip(
        id="clip_seed", parent_id="mom_1", path="/seed.mp4", state=ClipState.queued,
        meta_captions={"a/instagram": {
            "caption": seed_sentence, "hashtags": seed_tags,
            "tag_sources": {t: "lock" for t in seed_tags},
        }}))
    wrong = "Wrong sentence from the longer list."
    led.add_clip(Clip(
        id="clip_aspect", parent_id="mom_1", path="/aspect.mp4", state=ClipState.queued,
        meta_captions={
            "a/instagram": {"caption": wrong, "hashtags": longer,
                            "tag_sources": {t: "aspect" for t in longer}},
            "a/tiktok": {"caption": wrong, "hashtags": longer, "tag_sources": {}},
        }))
    stale = " ".join(seed_tags)
    led.add_post(Post(id="p_aspect", parent_id="clip_aspect", account="a", account_id="1",
                      platform=Platform.instagram, caption=stale, hashtags=list(seed_tags)))
    led.add_post(Post(id="p_seed", parent_id="clip_seed", account="a", account_id="1",
                      platform=Platform.instagram, caption=stale, hashtags=list(seed_tags)))
    want = seed_sentence + "\n" + " ".join(ranked)
    for pid in ("p_aspect", "p_seed"):
        post = led.posts[pid]
        assert _seed_clip_id(led, post) == "clip_seed"
        assert posted_text_for(cfg, led, post) == want
        surface = _surface(post, persona=None, now=NOW, cfg=cfg, led=led)
        assert surface.caption == seed_sentence
        assert surface.approve_caption == want
        assert surface.hashtags == ranked
        assert surface.tag_sources == {t: "lock" for t in seed_tags}
        assert wrong not in surface.caption and wrong not in surface.approve_caption


def _ig_offlock(cfg, *, pid, state, public_url=None, hook=None, seed=False):
    """IG post whose stored hashtags still include an off-lock tag. Lock is `#keep` only."""
    led, sid, clip_id = _lineage(cfg)
    _lock(cfg, sid, ["#keep"])
    stored = ["#keep", "#offlock"]
    if hook:
        led.moments["mom_1"].hook = hook
    if seed:
        led.clips[clip_id].meta_captions = {"a/instagram": {
            "caption": SENTENCE, "hashtags": list(stored),
            "tag_sources": {t: "lock" for t in stored},
            "hook": "H", "axis": "ax", "rationale": "why",
        }}
    post = Post(id=pid, parent_id=clip_id, account="a", account_id="98432",
                platform=Platform.instagram, caption=SENTENCE, hashtags=list(stored),
                state=state, post_type="post", public_url=public_url,
                media_urls=["https://uploads.postiz.com/x.mp4"],
                scheduled_time="2026-06-02T18:00:00Z")
    led.add_post(post)
    led.save()
    return led, clip_id, post


def _postiz_leg(monkeypatch, mocker, *, posts_body):
    from fanops.post.postiz import PostizIntegration
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    sent = {}

    def _post(url, **kw):
        sent["json"] = kw.get("json") or {}
        return _R(201, {"id": "postiz_1"})

    post_mock = mocker.patch("fanops.post.postiz.requests.post", side_effect=_post)
    mocker.patch("fanops.post.postiz.postiz_list_integrations",
                 return_value=[PostizIntegration("98432", "a", "instagram")])
    mocker.patch("fanops.post.metrics.postiz_read.requests.get",
                 return_value=_R(200, posts_body))
    return sent, post_mock


def _lock(cfg, sid, tags):
    p = source_tag_locks_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        sid: {"pile": list(tags), "lock": list(tags), "researched_at": "2026-08-17T00:00:00Z"},
    }))


def test_youtube_review_matches_payload_and_preview(tmp_path, monkeypatch, mocker):
    from fanops.caption_compose import _youtube_tags
    from fanops.post.dryrun import write_preview
    from fanops.post.postiz import PostizIntegration, PostizPoster, build_postiz_payload
    cfg = Config(root=tmp_path)
    led, sid, clip_id = _lineage(cfg, clip_id="ytclip")
    tags = ["#wave", "#alt"]
    _lock(cfg, sid, tags)
    caption = "They slept on the pocket. #raw"
    led.clips[clip_id].meta_captions = {"a/youtube": {
        "caption": caption, "hashtags": list(tags),
        "tag_sources": {t: "lock" for t in tags},
    }}
    post = Post(id="p_yt", parent_id=clip_id, account="a", account_id="yt_intg",
                platform=Platform.youtube, caption=caption, hashtags=list(tags),
                media_urls=["m1|http://x/m.mp4"])
    led.add_post(post)
    labels = [row["label"] for row in _youtube_tags(tags)]
    surface = _surface(post, persona=None, now=NOW, cfg=cfg, led=led)
    assert surface.caption == caption and surface.approve_caption == caption
    assert "#raw" in surface.caption and "\n#wave" not in surface.caption
    assert surface.hashtags == labels
    assert surface.tag_sources == {label: "lock" for label in labels}
    payload = build_postiz_payload(
        integration_id="yt_intg", platform="youtube", content=surface.caption,
        media_urls=post.media_urls, scheduled_time=None, post_type="post",
        title="hook", hashtags=post.hashtags)
    body = payload["posts"][0]
    assert body["value"][0]["content"] == caption
    assert [row["label"] for row in body["settings"]["tags"]] == labels == surface.hashtags
    before = (post.state, post.submission_id, post.public_url)
    write_preview(cfg, post, led)
    side = json.loads((cfg.scheduled / f"{post.id}.json").read_text())
    assert side["text"] == caption and side["tags"] == labels
    assert "state" not in side and "submission_id" not in side and "public_url" not in side
    assert (post.state, post.submission_id, post.public_url) == before
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    mocker.patch("fanops.post.postiz.postiz_list_integrations",
                 return_value=[PostizIntegration("yt_intg", "yt", "youtube")])
    mocker.patch("fanops.post.postiz.PostizPoster._existing_submission_for_payload",
                 return_value=(None, None))
    sent = {}

    def _post(url, **kw):
        sent["body"] = kw.get("json") or {}
        return _R(201, {"id": "ok"})

    mocker.patch("fanops.post.postiz.requests.post", side_effect=_post)
    PostizPoster(cfg).publish(led, post.id)
    wire = sent["body"]["posts"][0]
    assert wire["value"][0]["content"] == caption
    assert [row["label"] for row in wire["settings"]["tags"]] == labels


def test_ig_schedule_queue_and_dryrun_match_posted_text(tmp_path):
    from fanops.post.dryrun import DryRunPoster
    from fanops.studio.views_run import publish_queue
    from fanops.studio.views_schedule import schedule_rows
    cfg = Config(root=tmp_path)
    _accounts(cfg)
    led, sid, clip_id = _lineage(cfg)
    _lock(cfg, sid, ["#keep", "#also"])
    post = Post(id="p_ig", parent_id=clip_id, account="a", account_id="1",
                platform=Platform.instagram, caption="hello there",
                hashtags=["#keep", "#invented"], state=PostState.queued,
                scheduled_time="2099-01-01T00:00:00Z", media_urls=["https://h/v.mp4"])
    led.add_post(post)
    led.save()
    want = posted_text_for(cfg, led, post)
    assert want == "hello there\n#keep"
    assert [r for r in schedule_rows(led, cfg, now=NOW) if r.post_id == post.id][0].caption == want
    queued = [r for r in publish_queue(cfg, now=NOW) if r["post_id"] == post.id]
    assert queued[0]["caption"] == want
    DryRunPoster(cfg).publish(led, post.id)
    body = json.loads((cfg.scheduled / f"{post.id}.json").read_text())
    assert body["text"] == want and "tags" not in body
    assert led.posts[post.id].state is PostState.queued
    assert led.posts[post.id].submission_id is None
    assert led.posts[post.id].public_url is None


def test_edit_composed_box_stores_sentence_and_intersection(tmp_path):
    from fanops.studio.actions import edit_caption
    cfg = Config(root=tmp_path)
    led, sid, clip_id = _lineage(cfg)
    keep = "#keep"
    _lock(cfg, sid, [keep, "#also"])
    led.clips[clip_id].meta_captions = {"a/instagram": {
        "caption": SENTENCE, "hashtags": [keep], "hashtags_raw": ["#rawpick"],
        "tag_sources": {keep: "lock"},
        "hook": "H", "axis": "ax", "rationale": "why", "fallback": True,
    }}
    post = Post(id="p_edit", parent_id=clip_id, account="a", account_id="1",
                platform=Platform.instagram, caption=SENTENCE, hashtags=[keep],
                state=PostState.awaiting_approval)
    led.add_post(post)
    led.save()
    res = edit_caption(cfg, post.id, "A new line about the pocket. #keep #offlock", now=NOW)
    assert res.ok
    saved = Ledger.load(cfg)
    p = saved.posts[post.id]
    assert p.caption == "A new line about the pocket."
    assert p.hashtags == [keep]
    mc = saved.clips[clip_id].meta_captions["a/instagram"]
    assert mc["caption"] == p.caption and mc["hashtags"] == [keep]
    assert mc["tag_sources"] == {keep: "lock"}
    assert mc["hook"] == "H" and mc["axis"] == "ax" and mc["rationale"] == "why"
    assert mc["fallback"] is True and mc["hashtags_raw"] == ["#rawpick"]
    surface = _surface(p, persona=None, now=NOW, cfg=cfg, led=saved)
    assert surface.caption == "A new line about the pocket."
    assert surface.approve_caption == "A new line about the pocket.\n#keep"
    assert surface.approve_caption.count("#keep") == 1 and "#offlock" not in surface.approve_caption
    assert "#keep" not in surface.caption
    assert surface.hashtags == [keep]


def test_youtube_edit_keeps_hashtags_for_settings_tags(tmp_path):
    from fanops.caption_compose import _youtube_tags
    from fanops.studio.actions import edit_caption
    cfg = Config(root=tmp_path)
    led, sid, clip_id = _lineage(cfg, clip_id="ytedit")
    stored = ["#wave", "#offlock"]
    _lock(cfg, sid, ["#wave"])
    post = Post(id="p_yt_edit", parent_id=clip_id, account="a", account_id="1",
                platform=Platform.youtube, caption="old description", hashtags=list(stored),
                state=PostState.awaiting_approval)
    led.add_post(post)
    led.save()
    textarea = "Brand new description #nope"
    assert edit_caption(cfg, post.id, textarea, now=NOW).ok
    saved = Ledger.load(cfg)
    p = saved.posts[post.id]
    assert p.caption == textarea and p.hashtags == stored
    surface = _surface(p, persona=None, now=NOW, cfg=cfg, led=saved)
    labels = [row["label"] for row in _youtube_tags(stored)]
    assert surface.caption == textarea and surface.approve_caption == textarea and surface.hashtags == labels


def test_regenerate_writes_seed_sources_and_chips(tmp_path):
    from fanops.studio.actions import regenerate_caption
    cfg = Config(root=tmp_path)
    led, sid, clip_id = _lineage(cfg)
    _lock(cfg, sid, ["#keep", "#also"])
    led.clips[clip_id].meta_captions = {"a/instagram": {
        "caption": SENTENCE, "hashtags": ["#keep"],
        "tag_sources": {"#keep": "lock"},
        "hook": "H", "axis": "ax", "rationale": "why", "fallback": True,
    }}
    post = Post(id="p_regen", parent_id=clip_id, account="a", account_id="1",
                platform=Platform.instagram, caption=SENTENCE, hashtags=["#keep"],
                state=PostState.awaiting_approval)
    led.add_post(post)
    led.save()

    def model(prompt, schema):
        return {"items": [{"surface": "a/instagram", "caption": "Regenerated sentence.",
                           "hashtags": ["#also", "#off", "#keep"], "language": "en"}]}

    res = regenerate_caption(cfg, post.id, "", model=model, now=NOW)
    assert res.ok
    saved = Ledger.load(cfg)
    p = saved.posts[post.id]
    shipped = ["#also", "#keep"]
    assert p.caption == "Regenerated sentence." and p.hashtags == shipped
    mc = saved.clips[clip_id].meta_captions["a/instagram"]
    assert mc["caption"] == p.caption and mc["hashtags"] == shipped
    assert mc["tag_sources"] == {t: "lock" for t in shipped}
    assert mc["hook"] == "H" and mc["axis"] == "ax" and mc["rationale"] == "why" and mc["fallback"] is True
    surface = _surface(p, persona=None, now=NOW, cfg=cfg, led=saved)
    assert surface.hashtags == shipped
    assert surface.tag_sources == {t: "lock" for t in shipped}
    assert surface.caption == "Regenerated sentence."
    assert surface.approve_caption == "Regenerated sentence.\n#also #keep"


def test_youtube_regenerate_writes_shipped_tags(tmp_path):
    from fanops.caption_compose import _youtube_tags
    from fanops.studio.actions import regenerate_caption
    cfg = Config(root=tmp_path)
    led, sid, clip_id = _lineage(cfg, clip_id="ytregen")
    _lock(cfg, sid, ["#keep", "#also"])
    post = Post(id="p_yt_regen", parent_id=clip_id, account="a", account_id="1",
                platform=Platform.youtube, caption="old description", hashtags=["#stale"],
                state=PostState.awaiting_approval)
    led.add_post(post)
    led.save()

    def model(prompt, schema):
        return {"items": [{"surface": "a/youtube", "caption": "Fresh description.",
                           "hashtags": ["#also", "#off", "#keep"], "language": "en"}]}

    assert regenerate_caption(cfg, post.id, "", model=model, now=NOW).ok
    saved = Ledger.load(cfg)
    p = saved.posts[post.id]
    shipped = ["#also", "#keep"]
    assert p.caption == "Fresh description." and p.hashtags == shipped
    labels = [row["label"] for row in _youtube_tags(shipped)]
    surface = _surface(p, persona=None, now=NOW, cfg=cfg, led=saved)
    assert surface.caption == p.caption and surface.approve_caption == p.caption and surface.hashtags == labels
    assert saved.clips[clip_id].meta_captions["a/youtube"]["tag_sources"] == {t: "lock" for t in shipped}


def test_rotation_ignores_off_lock_tag(tmp_path):
    from fanops.studio.views_hashtags import rotation_health
    from fanops.studio.views_schedule import tag_exposure
    cfg = Config(root=tmp_path)
    led, sid, clip_id = _lineage(cfg)
    _lock(cfg, sid, ["#keep"])
    led.add_post(Post(id="p_old", parent_id=clip_id, account="a", account_id="1",
                      platform=Platform.instagram, caption="A", hashtags=["#keep", "#offlock"],
                      state=PostState.queued, created_at="2026-08-01T00:00:00Z"))
    led.add_post(Post(id="p_new", parent_id=clip_id, account="a", account_id="1",
                      platform=Platform.instagram, caption="B", hashtags=["#keep", "#offlock"],
                      state=PostState.queued, created_at="2026-08-02T00:00:00Z"))
    rows = rotation_health(led, cfg)
    row = next(r for r in rows if r.account == "a")
    assert row.lines == [["#keep"], ["#keep"]]
    assert all("#offlock" not in line for line in row.lines)
    counted = dict(tag_exposure(led, cfg)["a"])
    assert counted == {"#keep": 2}


def test_publish_one_archives_the_send(tmp_path, monkeypatch, mocker):
    from fanops.caption_compose import send_caption
    from fanops.post.run import _publish_one
    from fanops.studio.views_posted import posted_library
    cfg = Config(root=tmp_path)
    led, _clip_id, post = _ig_offlock(
        cfg, pid="p_pub", state=PostState.queued, public_url="https://instagram.com/reel/abc/")
    text, send_tags, _entry = send_caption(cfg, led, post)
    assert send_tags == ["#keep"] and "#offlock" not in text and text != post.caption
    sent, post_mock = _postiz_leg(monkeypatch, mocker, posts_body={"posts": []})
    final = _publish_one(cfg, post.id, "postiz")
    assert final == "published"
    post_mock.assert_called_once()
    wire = sent["json"]["posts"][0]["value"][0]["content"]
    assert wire == text
    loaded = Ledger.load(cfg)
    saved = loaded.posts[post.id]
    assert saved.caption == SENTENCE and "#offlock" in saved.hashtags
    files = list(cfg.published.glob(f"*/{post.id}.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["caption"] == wire and rec["hashtags"] == send_tags
    assert posted_library(loaded, cfg)[0].caption == wire


def test_publish_one_adopted_duplicate_keeps_the_submission(tmp_path, monkeypatch, mocker):
    from fanops.caption_compose import send_caption
    from fanops.post.run import _publish_one
    cfg = Config(root=tmp_path)
    led, _clip_id, post = _ig_offlock(cfg, pid="p_adopt", state=PostState.queued)
    text, _tags, _entry = send_caption(cfg, led, post)
    _sent, post_mock = _postiz_leg(monkeypatch, mocker, posts_body={"posts": [{
        "id": "postiz_existing", "state": "QUEUE",
        "integration": {"id": "98432"}, "content": text,
    }]})
    final = _publish_one(cfg, post.id, "postiz")
    post_mock.assert_not_called()
    saved = Ledger.load(cfg).posts[post.id]
    assert final == "needs_reconcile"
    assert saved.submission_id == "postiz_existing"


def test_unchanged_approve_keeps_off_lock_tag(tmp_path):
    from fanops.caption_compose import send_caption
    from fanops.studio.actions_approve import approve_with_edits
    cfg = Config(root=tmp_path)
    hook = "the pocket hook"
    led, clip_id, post = _ig_offlock(
        cfg, pid="p_ok", state=PostState.awaiting_approval, hook=hook, seed=True)
    stored = list(post.hashtags)
    text, send_tags, _entry = send_caption(cfg, led, post)
    surface = _surface(post, persona=None, now=NOW, cfg=cfg, led=led)
    assert send_tags == ["#keep"] and text != post.caption and "#offlock" in stored
    assert surface.caption == SENTENCE and surface.approve_caption == text
    res = approve_with_edits(cfg, post.id, caption=surface.caption, hook=hook, now=NOW)
    assert res.ok and res.detail["approved"] == 1
    saved = Ledger.load(cfg)
    p = saved.posts[post.id]
    assert p.state is PostState.queued
    assert p.caption == SENTENCE and p.hashtags == stored and p.edited_at is None
    mc = saved.clips[clip_id].meta_captions["a/instagram"]
    assert mc["caption"] == SENTENCE and mc["hashtags"] == stored
    assert mc["hook"] == "H" and mc["axis"] == "ax" and mc["rationale"] == "why"


def test_approve_changed_sentence_keeps_tags_out_of_caption(tmp_path):
    from fanops.studio.actions_approve import approve_with_edits
    cfg = Config(root=tmp_path)
    hook = "the pocket hook"
    _led, _clip_id, post = _ig_offlock(
        cfg, pid="p_edit_sentence", state=PostState.awaiting_approval, hook=hook, seed=True)
    res = approve_with_edits(
        cfg, post.id, caption="A new line about the pocket.", hook=hook, now=NOW)
    assert res.ok and res.detail["approved"] == 1
    p = Ledger.load(cfg).posts[post.id]
    assert p.state is PostState.queued
    assert p.caption == "A new line about the pocket."
    assert "\n" not in p.caption and "#keep" not in p.caption
    assert p.hashtags == ["#keep"]
