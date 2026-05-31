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
