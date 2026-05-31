import os
import pytest
from fanops.config import Config
from fanops.post.payload import build_blotato_payload, default_target_fields

pytestmark = pytest.mark.integration

def test_payload_matches_confirmed_rest_shape():
    # Locks the shape confirmed vs help.blotato.com so a regression is caught even offline.
    p = build_blotato_payload(account_id="98432", platform="tiktok", text="hi",
                              media_urls=["https://h/v.mp4"], scheduled_time="2026-06-02T18:00:00Z",
                              extra_target=default_target_fields("tiktok"))
    assert p["post"]["accountId"] == "98432"
    assert p["post"]["content"]["platform"] == "tiktok"
    assert p["post"]["target"]["targetType"] == "tiktok"
    assert p["post"]["target"]["privacyLevel"] == "PUBLIC_TO_EVERYONE"
    assert p["scheduledTime"] == "2026-06-02T18:00:00Z" and "scheduledTime" not in p["post"]

@pytest.mark.skipif(not os.getenv("BLOTATO_SMOKE_ACCOUNT_ID") or not os.getenv("BLOTATO_API_KEY"),
                    reason="set BLOTATO_API_KEY + BLOTATO_SMOKE_ACCOUNT_ID to hit the live sandbox")
def test_live_auth_and_schedule(tmp_path, monkeypatch):
    # Confirms the UNVERIFIED integration checkpoints against the real API, far in the future
    # so it can be deleted before it ever publishes. Run manually, never in CI by default.
    import requests
    key = os.environ["BLOTATO_API_KEY"]; acct = os.environ["BLOTATO_SMOKE_ACCOUNT_ID"]
    payload = build_blotato_payload(account_id=acct, platform="twitter",
                                    text="fanops smoke — delete me", media_urls=[],
                                    scheduled_time="2099-01-01T00:00:00Z")
    r = requests.post("https://backend.blotato.com/v2/posts",
                      headers={"blotato-api-key": key, "Content-Type": "application/json"},
                      json=payload, timeout=30)
    assert r.status_code in (200, 201), r.text
    body = r.json()
    # CONFIRM the real submission-id key here; update post/blotato_rest.py if it differs.
    assert any(k in body for k in ("postSubmissionId", "id", "submissionId")), body
