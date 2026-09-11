# tests/test_postiz_contract.py — the Postiz wire SHAPE CONTRACT, pinned to committed fixtures.
#
# These tests are the answer to "stop guessing external shapes." Each committed fixture under
# tests/fixtures/wire/ records the VERBATIM Postiz request/response body and asserts against what
# Postiz ACTUALLY expects/returns — not a hand-written guess. Offline only (no live Postiz): if
# Postiz changes a shape, these tests go red — which is exactly the drift we currently discover
# only in production.
#
# Fixtures:
#   postiz_instagram_schedule.body.json — POST /public/v1/posts request body (IG schedule)
#   postiz_create_201.response.json    — POST /public/v1/posts 201 response body
import json
from pathlib import Path

from fanops.post.postiz import _extract_postiz_id, build_postiz_payload

_FIXTURES = Path(__file__).resolve().parent / "fixtures/wire"


def test_build_postiz_payload_matches_schedule_fixture():
    """CONTRACT: the IG schedule request body matches the committed wire fixture."""
    fixture = json.loads((_FIXTURES / "postiz_instagram_schedule.body.json").read_text())
    built = build_postiz_payload(
        integration_id="intg_1",
        platform="instagram",
        content="fire",
        media_urls=["https://uploads.postiz.com/x.mp4"],
        scheduled_time="2099-01-01T00:00:00Z",
        post_type="post",
    )
    assert built == fixture


def test_extract_postiz_id_parses_create_response_fixture():
    """CONTRACT: a real POST /public/v1/posts 201 body carries an id _extract_postiz_id recognizes."""
    body = json.loads((_FIXTURES / "postiz_create_201.response.json").read_text())
    assert _extract_postiz_id(body) == "cmr0postizcreate001"
