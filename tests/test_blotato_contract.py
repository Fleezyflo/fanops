# tests/test_blotato_contract.py — L3 (audit): the Blotato POST /v2/posts WIRE CONTRACT, proven in the FAST
# unit job against a LOCAL http server driving the REAL BlotatoRestPoster over the REAL requests stack — no
# live account, no creds, no toolchain. This is the money path (posting to the artist's real fan accounts).
# The mock-only suite stays green if Blotato renames / nests / drops the submission-id field; these tests
# catch that: the documented envelope -> submitted (+ the right request on the wire); an unexpected envelope
# -> needs_reconcile (never `failed` => re-queueable => double-post, never a crash). The single live-network
# test stays an opt-in smoke in tests/integration/test_blotato_smoke.py.
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState


class _BlotatoStub(BaseHTTPRequestHandler):
    """Serves ONE configured (status, json-body) and records the received request so a test can assert the
    REAL request contract (path, api-key header, JSON body), not just the response handling."""
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode() if n else ""
        self.server.received.append({"path": self.path, "headers": dict(self.headers), "body": raw})
        status, body = self.server.reply
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
    def log_message(self, *a): pass            # silence the default per-request stderr access log


@pytest.fixture
def blotato_server(monkeypatch):
    """A throwaway localhost Blotato endpoint bound to the REAL poster (BASE_URL monkeypatched). Configure the
    reply via `srv.reply = (status, body)`; inspect `srv.received`."""
    srv = HTTPServer(("127.0.0.1", 0), _BlotatoStub)
    srv.received = []
    srv.reply = (200, {})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("BLOTATO_API_KEY", "test-key-123")
    monkeypatch.setattr("fanops.post.blotato_rest.BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    try:
        yield srv
    finally:
        srv.shutdown(); srv.server_close()


def _led_with_post(cfg):
    led = Ledger.load(cfg)
    led.add_post(Post(id="p1", parent_id="clip_1", account="@a", account_id="98432",
                      platform=Platform.tiktok, caption="ship it",
                      media_urls=["https://cdn.example/v.mp4"], scheduled_time="2099-01-01T00:00:00Z",
                      state=PostState.submitting, public_url="dryrun://p1"))
    return led


def test_documented_envelope_yields_submitted_and_sends_the_right_request(tmp_path, blotato_server):
    from fanops.post.blotato_rest import BlotatoRestPoster
    cfg = Config(root=tmp_path)
    blotato_server.reply = (200, {"postSubmissionId": "blotato_abc123"})
    led = BlotatoRestPoster(cfg).publish(_led_with_post(cfg), "p1")
    p = led.posts["p1"]
    assert p.state is PostState.submitted and p.submission_id == "blotato_abc123"   # id extracted over real HTTP
    # the REAL request the poster put on the wire — catches a REQUEST-contract drift, not just response handling
    req = blotato_server.received[-1]
    assert req["path"].endswith("/posts")
    assert req["headers"].get("blotato-api-key") == "test-key-123"
    sent = json.loads(req["body"])
    assert sent["post"]["accountId"] == "98432" and sent["post"]["content"]["platform"] == "tiktok"
    assert sent["post"]["content"]["mediaUrls"] == ["https://cdn.example/v.mp4"]


def test_nested_data_alias_is_extracted(tmp_path, blotato_server):
    # the documented nested alias ({"data": {"submissionId": ...}}) must still resolve to submitted.
    from fanops.post.blotato_rest import BlotatoRestPoster
    cfg = Config(root=tmp_path)
    blotato_server.reply = (201, {"data": {"submissionId": "nested_xyz"}})
    led = BlotatoRestPoster(cfg).publish(_led_with_post(cfg), "p1")
    assert led.posts["p1"].state is PostState.submitted and led.posts["p1"].submission_id == "nested_xyz"


def test_unexpected_envelope_parks_needs_reconcile_never_failed(tmp_path, blotato_server):
    # THE failure the mock-only suite misses: Blotato returns 2xx but renames/drops the id field. The poster
    # must PARK it (it MAY be live) — never `failed` (re-queueable -> a double-post to a real account), never a crash.
    from fanops.post.blotato_rest import BlotatoRestPoster
    cfg = Config(root=tmp_path)
    blotato_server.reply = (200, {"renamed_id_field": "surprise"})
    led = BlotatoRestPoster(cfg).publish(_led_with_post(cfg), "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile


def test_5xx_parks_needs_reconcile_and_captures_a_body_id(tmp_path, blotato_server):
    # a 5xx after the body was sent is ambiguous -> needs_reconcile (no re-POST); if the error body carries a
    # real id, capture it so reconcile can poll GET /v2/posts/:id (AUDIT H4) — verified over real HTTP.
    from fanops.post.blotato_rest import BlotatoRestPoster
    cfg = Config(root=tmp_path)
    blotato_server.reply = (503, {"postSubmissionId": "late_id_777"})
    led = BlotatoRestPoster(cfg).publish(_led_with_post(cfg), "p1")
    p = led.posts["p1"]
    assert p.state is PostState.needs_reconcile and p.submission_id == "late_id_777"


def test_401_raises_loudly_without_leaking_the_key(tmp_path, blotato_server):
    from fanops.post.blotato_rest import BlotatoRestPoster
    from fanops.errors import BlotatoAuthError
    cfg = Config(root=tmp_path)
    blotato_server.reply = (401, {"error": "rejected key test-key-123"})
    with pytest.raises(BlotatoAuthError) as ei:
        BlotatoRestPoster(cfg).publish(_led_with_post(cfg), "p1")
    assert "test-key-123" not in str(ei.value)         # the presented key is never echoed in the raised error
