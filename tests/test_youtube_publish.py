# tests/test_youtube_publish.py — YouTube Shorts onboarding via Postiz.
# Three contracts: (1) youtube renders 9:16 Shorts geometry (not 16:9 long-form); (2) the Postiz
# YouTube post carries a real settings block — title (the per-account hook, fallback artist_name) + the
# required privacy `type` + hashtags->tags — because Postiz's YoutubeSettingsDto REQUIRES title+type and
# has NO post_type; (3) non-youtube payloads stay byte-identical (the firewall). The post `content`
# (caption) becomes the YouTube DESCRIPTION; the title is the burned hook.
import fanops.config as cfgmod
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Platform, Fmt, PLATFORM_ASPECT, PLATFORM_MAX_SECONDS, Post
from fanops.post.postiz import build_postiz_payload, PostizPoster


def _yt_post(**kw):
    base = dict(id="p1", parent_id="clip_1", account="a", account_id="yt_intg",
                platform=Platform.youtube, caption="full description here",
                media_urls=["m1|http://x/m.mp4"])
    base.update(kw)
    return Post(**base)

def _mock_post_ok(mocker):
    cap = {}
    class R:
        status_code = 200
        def json(self): return {"id": "sub1"}
    def fake(url, headers=None, json=None, timeout=None):
        cap["json"] = json; return R()
    mocker.patch("fanops.post.postiz.requests.post", side_effect=fake)
    return cap


# ---- (1) geometry: YouTube is a 9:16 Short, not 16:9 long-form ----
def test_youtube_renders_vertical_short():
    assert PLATFORM_ASPECT[Platform.youtube] is Fmt.r9x16          # 9:16 -> YouTube auto-classifies as a Short
    assert PLATFORM_MAX_SECONDS[Platform.youtube] == 180           # Shorts ceiling (was 60 long-form)

# ---- backend gate: a provider-less youtube channel may bridge to the postiz global ----
def test_postiz_backend_admits_youtube():
    assert "youtube" in cfgmod._BACKEND_PLATFORMS["postiz"]
    assert "instagram" in cfgmod._BACKEND_PLATFORMS["postiz"]      # unchanged

# ---- (2) payload: youtube settings carry title/type/tags, NO post_type ----
def test_youtube_payload_settings_shape():
    p = build_postiz_payload(integration_id="yt_intg", platform="youtube", content="desc",
                             media_urls=["m1|http://x/m.mp4"], scheduled_time=None,
                             title="they slept on me", hashtags=["#alt", "wave", "#alt"])
    s = p["posts"][0]["settings"]
    assert s["__type"] == "youtube"
    assert s["title"] == "they slept on me"
    assert s["type"] == "public"
    assert s["selfDeclaredMadeForKids"] == "no"
    assert "post_type" not in s                                    # youtube DTO has none
    assert s["tags"] == [{"value": "alt", "label": "alt"}, {"value": "wave", "label": "wave"}]   # '#' stripped, deduped
    assert p["posts"][0]["value"][0]["content"] == "desc"         # caption -> DESCRIPTION

def test_youtube_title_clamped_to_100():
    long = "x" * 250
    p = build_postiz_payload(integration_id="yt_intg", platform="youtube", content="d",
                             media_urls=[], scheduled_time=None, title=long, hashtags=None)
    assert len(p["posts"][0]["settings"]["title"]) == 100

# ---- (3) firewall: non-youtube payload byte-identical (stub settings, no title leak) ----
def test_instagram_payload_byte_identical():
    p = build_postiz_payload(integration_id="ig1", platform="instagram", content="c",
                             media_urls=["m1|http://x/m.mp4"], scheduled_time="2026-06-02T18:00:00Z",
                             title="should be ignored", hashtags=["#x"])
    assert p["posts"][0]["settings"] == {"__type": "instagram", "post_type": "post"}


# ---- publish: title sourced from the per-account hook, fallback to artist_name ----
def test_publish_youtube_title_from_variant_hook(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007/api"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_yt_post(hashtags=["#wave", "alt"], variant_hook="they slept on me"))
    cap = _mock_post_ok(mocker)
    PostizPoster(cfg).publish(led, "p1")
    s = cap["json"]["posts"][0]["settings"]
    assert s["__type"] == "youtube" and s["title"] == "they slept on me" and s["type"] == "public"
    assert cap["json"]["posts"][0]["value"][0]["content"] == "full description here"   # caption -> description
    assert s["tags"] == [{"value": "wave", "label": "wave"}, {"value": "alt", "label": "alt"}]

def test_publish_youtube_title_falls_back_to_artist(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007/api"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("FANOPS_ARTIST_NAME", "Moh Flow")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_yt_post(variant_hook=None))                      # no hook -> floor
    cap = _mock_post_ok(mocker)
    PostizPoster(cfg).publish(led, "p1")
    assert cap["json"]["posts"][0]["settings"]["title"] == "Moh Flow"

def test_publish_youtube_title_floor_when_artist_too_short(tmp_path, monkeypatch, mocker):
    # CRITICAL guard: a 1-char FANOPS_ARTIST_NAME + no hook must NOT emit a title Postiz's @MinLength(2)
    # would 422 (a silent post-death). build_postiz_payload floors any sub-2-char title.
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007/api"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("FANOPS_ARTIST_NAME", "X")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_yt_post(variant_hook=None))
    cap = _mock_post_ok(mocker)
    PostizPoster(cfg).publish(led, "p1")
    assert len(cap["json"]["posts"][0]["settings"]["title"]) >= 2   # never sub-2 -> never a 422

def test_youtube_payload_floors_empty_title():
    # a direct caller passing no title for youtube still gets a VALID (>=2) title, never {"title": ""}
    p = build_postiz_payload(integration_id="yt", platform="youtube", content="d",
                             media_urls=[], scheduled_time=None, title=None, hashtags=None)
    assert len(p["posts"][0]["settings"]["title"]) >= 2

def test_publish_instagram_still_stub(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("POSTIZ_URL", "http://localhost:4007/api"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_post(_yt_post(id="p2", platform=Platform.instagram, account_id="ig1", variant_hook="ignored"))
    cap = _mock_post_ok(mocker)
    PostizPoster(cfg).publish(led, "p2")
    assert cap["json"]["posts"][0]["settings"] == {"__type": "instagram", "post_type": "post"}
