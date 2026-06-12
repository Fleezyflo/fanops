from fanops.post.payload import (build_blotato_payload, build_blotato_mcp_args,
                                 default_target_fields)

def test_nested_rest_minimal():
    p = build_blotato_payload(account_id="1", platform="twitter", text="hi",
                              media_urls=[], scheduled_time=None)
    assert p["post"]["accountId"] == "1"
    assert p["post"]["content"]["platform"] == p["post"]["target"]["targetType"] == "twitter"

def test_schedule_is_root_level():
    p = build_blotato_payload(account_id="1", platform="instagram", text="x",
                              media_urls=["https://h/v.mp4"], scheduled_time="2026-06-01T18:00:00Z")
    assert p["scheduledTime"] == "2026-06-01T18:00:00Z" and "scheduledTime" not in p["post"]

def test_target_fields_per_platform():
    tk = default_target_fields("tiktok")
    for k in ("privacyLevel", "disabledComments", "disabledDuet", "disabledStitch",
              "isBrandedContent", "isYourBrand", "isAiGenerated"):
        assert k in tk
    yt = default_target_fields("youtube", title="T")
    assert yt["title"] == "T" and "privacyStatus" in yt
    assert default_target_fields("twitter") == {}

def test_tiktok_payload_has_required_fields():
    p = build_blotato_payload(account_id="1", platform="tiktok", text="x",
                              media_urls=["https://h/v.mp4"], scheduled_time=None,
                              extra_target=default_target_fields("tiktok"))
    assert p["post"]["target"]["privacyLevel"]

def test_mcp_args_flat():
    a = build_blotato_mcp_args(account_id="1", platform="instagram", text="hi",
                               media_urls=["https://h/v.mp4"], scheduled_time="2026-06-02T18:00:00Z",
                               media_type="reel")
    assert a["accountId"] == "1" and a["mediaUrls"] == ["https://h/v.mp4"]
    assert a["mediaType"] == "reel"
    assert "post" not in a and "content" not in a

def test_youtube_title_fallback_from_config():
    # AUDIT (h): when no explicit title is given, the YouTube display-name fallback must come
    # from config (operator-overridable FANOPS_ARTIST_NAME), NOT a hardcoded artist name. An
    # operator who runs FanOps for a different artist passes artist_name down and the YouTube
    # title fallback follows.
    yt = default_target_fields("youtube", artist_name="Custom Artist")
    assert yt["title"] == "Custom Artist"

def test_youtube_title_default_unchanged():
    # The default is unchanged: with no override, the YouTube title fallback is still "Moh Flow"
    # (so existing callers/behavior are unaffected).
    yt = default_target_fields("youtube")
    assert yt["title"] == "Moh Flow"

def test_youtube_explicit_title_beats_artist_name():
    # An explicit title (a real caption-derived title) always wins over the artist-name fallback.
    yt = default_target_fields("youtube", title="Real Title", artist_name="Custom Artist")
    assert yt["title"] == "Real Title"

def test_facebook_target_fields_include_only_what_is_given():
    # Facebook requires pageId or Blotato 422s; mediaType is optional. Absent inputs must be
    # absent KEYS (not None values), so the API never sees explicit nulls.
    assert default_target_fields("facebook", page_id="pg_1", media_type="reels") == {
        "pageId": "pg_1", "mediaType": "reels"}
    assert default_target_fields("facebook", page_id="pg_1") == {"pageId": "pg_1"}
    assert default_target_fields("facebook") == {}

def test_instagram_target_fields_media_type_gated():
    # instagram returns a target dict ONLY when media_type is given; bare instagram falls through
    # to the empty default like any unconfigured platform.
    assert default_target_fields("instagram", media_type="reel") == {"mediaType": "reel"}
    assert default_target_fields("instagram") == {}

def test_payload_media_type_injected_for_ig_fb_only():
    ig = build_blotato_payload(account_id="1", platform="instagram", text="x",
                               media_urls=[], scheduled_time=None, media_type="reel")
    assert ig["post"]["target"]["mediaType"] == "reel"
    # other platforms never get mediaType even when callers pass one (TikTok would 422)
    tk = build_blotato_payload(account_id="1", platform="tiktok", text="x",
                               media_urls=[], scheduled_time=None, media_type="reel")
    assert "mediaType" not in tk["post"]["target"]

def test_use_next_free_slot_only_without_scheduled_time():
    # useNextFreeSlot is the fallback when no explicit time exists; an explicit scheduledTime
    # must win outright (never both keys in one payload — Blotato treats them as exclusive).
    free = build_blotato_payload(account_id="1", platform="instagram", text="x",
                                 media_urls=[], scheduled_time=None, use_next_free_slot=True)
    assert free["useNextFreeSlot"] is True and "scheduledTime" not in free
    timed = build_blotato_payload(account_id="1", platform="instagram", text="x",
                                  media_urls=[], scheduled_time="2026-06-01T18:00:00Z",
                                  use_next_free_slot=True)
    assert timed["scheduledTime"] == "2026-06-01T18:00:00Z" and "useNextFreeSlot" not in timed
