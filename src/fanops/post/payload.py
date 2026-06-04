"""Blotato request bodies. REST POST /v2/posts is NESTED (post.content/post.target);
official MCP blotato_create_post is FLAT. content.platform == target.targetType. scheduledTime
is a ROOT sibling of post. Per-platform target fields required or 422 (TikTok x7, YouTube
title+privacyStatus, Facebook pageId). REST shape CONFIRMED vs help.blotato.com 2026-05-31;
MCP arg shape is an INTEGRATION CHECKPOINT (confirm against the connected MCP)."""
from __future__ import annotations

def default_target_fields(platform: str, *, title: str | None = None,
                          page_id: str | None = None, media_type: str | None = None,
                          artist_name: str = "Moh Flow") -> dict:
    # artist_name is the YouTube title fallback when `title` is absent (audit h). It defaults to
    # "Moh Flow" so existing callers/tests are unchanged; the real publish path passes
    # cfg.artist_name (operator-overridable via FANOPS_ARTIST_NAME). An explicit `title` always
    # wins. This is the DISPLAY NAME, distinct from tagging.ARTIST_HANDLE (the @handle).
    if platform == "tiktok":
        return {"privacyLevel": "PUBLIC_TO_EVERYONE", "disabledComments": False,
                "disabledDuet": False, "disabledStitch": False, "isBrandedContent": False,
                "isYourBrand": False, "isAiGenerated": False}
    if platform == "youtube":
        return {"title": title or artist_name, "privacyStatus": "public",
                "shouldNotifySubscribers": False}
    if platform == "facebook":
        out: dict = {}
        if page_id: out["pageId"] = page_id
        if media_type: out["mediaType"] = media_type
        return out
    if platform == "instagram" and media_type:
        return {"mediaType": media_type}
    return {}

def build_blotato_payload(*, account_id: str, platform: str, text: str,
                          media_urls: list[str], scheduled_time: str | None,
                          media_type: str | None = None, use_next_free_slot: bool = False,
                          extra_target: dict | None = None) -> dict:
    target: dict = {"targetType": platform}
    if media_type and platform in ("instagram", "facebook"):
        target["mediaType"] = media_type
    if extra_target:
        target.update(extra_target)
    payload: dict = {"post": {"accountId": account_id,
                              "content": {"text": text, "mediaUrls": media_urls, "platform": platform},
                              "target": target}}
    if scheduled_time:
        payload["scheduledTime"] = scheduled_time
    elif use_next_free_slot:
        payload["useNextFreeSlot"] = True
    return payload

def build_blotato_mcp_args(*, account_id: str, platform: str, text: str,
                           media_urls: list[str], scheduled_time: str | None,
                           media_type: str | None = None, extra: dict | None = None) -> dict:
    args: dict = {"accountId": account_id, "platform": platform, "text": text,
                  "mediaUrls": media_urls}
    if scheduled_time: args["scheduledTime"] = scheduled_time
    if media_type: args["mediaType"] = media_type
    if extra: args.update(extra)
    return args
