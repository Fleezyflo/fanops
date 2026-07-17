# tests/test_zernio_presign.py — Wave 1A: the OFFICIAL Zernio media upload (presign + signed PUT).
#
# REPLACES tests/test_zernio_media.py, which pinned the reverse-engineered two-step /media/upload-token
# + POST /media/upload contract ("DISCOVERED LIVE 2026-06-29") — an END-USER-FLOW endpoint Zernio never
# published a contract for, which began answering 405 and burned four TikTok posts on 2026-07-16. Those
# tests were regression guards FOR the broken path, so they die with it; the two that outlived the
# contract they were written against (uploader dispatch, oversize preflight) are carried over below.
#
# Contract (OpenAPI 3.1.0 `Zernio API v1.0.4`, paths./v1/media/presign, retrieved 2026-07-16):
#   1) POST {base}/media/presign {"filename","contentType","size"} -> {uploadUrl, publicUrl, key, expiresIn}
#   2) PUT <uploadUrl> raw bytes, matching Content-Type, and NO Authorization header (the url is signed)
#   3) publicUrl (from step 1) goes into mediaItems[] — the PUT body is never parsed for it
# Full reconciliation: docs/reconciliation/09_ZERNIO_OFFICIAL_CONTRACT_RECONCILIATION.md
#
# All offline (mocked requests) — no live Zernio call. Numbers map 1:1 to report 09 §9. FOUR §9 rows are
# PRE-EXISTING coverage and are deliberately NOT duplicated here (the report named three of them wrongly):
#   41 -> test_needs_reconcile_post_is_never_republished  tests/test_channel_provider.py:192
#   42 -> test_publish_5xx_parks_needs_reconcile          tests/test_zernio.py:100
#   43 -> test_publish_due_ignores_awaiting_approval      tests/test_post_approval.py:116
#   47 -> test_publish_now_rejects_awaiting_approval      tests/test_studio_approval.py:49
import json
import logging
from types import SimpleNamespace
import pytest
import requests as _rq
from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.ledger import Ledger
from fanops.models import Post, Clip, PostState, ClipState, Platform
from fanops.post import get_media_uploader
from fanops.post.run import _publish_one, _is_transient_publish_error
from fanops.post.zernio import zernio_upload_media, build_zernio_payload, _evidence

_ACC = "acc_tiktok_1"
# A realistic presigned storage URL. Each credential value is a distinct sentinel so a test can prove
# WHICH one leaked, not merely that "something" did.
_SIG = "DEADBEEFCAFE1234567890ABCDEF"
_CRED = "AKIAEXAMPLE%2F20260716%2Fus-east-1%2Fs3%2Faws4_request"
_TOK = "FwoGZXIvYXdzEXAMPLETOKEN"
_UPLOAD_URL = ("https://storage.zernio.com/temp/1752_abc_v.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256"
               f"&X-Amz-Credential={_CRED}&X-Amz-Date=20260716T000000Z&X-Amz-Expires=900"
               f"&X-Amz-Security-Token={_TOK}&X-Amz-Signature={_SIG}")
_PUBLIC_URL = "https://media.zernio.com/m/1752_abc_v.mp4"
_SECRETS = (_SIG, _TOK, "AKIAEXAMPLE", _UPLOAD_URL)


class _R:
    """A response double. `body` may be an Exception -> json() raises it (the non-JSON case)."""
    def __init__(s, code, body=None, text="", headers=None):
        s.status_code = code; s._b = body; s.text = text; s.headers = headers or {}
    def json(s):
        if isinstance(s._b, Exception): raise s._b
        return {} if s._b is None else s._b


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("ZERNIO_API_URL", raising=False)
    return Config(root=tmp_path)


def _live_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LIVE", "1")
    return _cfg(tmp_path, monkeypatch)


def _mp4(tmp_path, name="v.mp4"):
    f = tmp_path / name; f.write_bytes(b"VIDEO"); return f


def _presign_ok(**over):
    b = {"uploadUrl": _UPLOAD_URL, "publicUrl": _PUBLIC_URL, "key": "temp/1752_abc_v.mp4", "expiresIn": 900}
    b.update(over)
    return _R(200, b)


def _mock_presign(mocker, resp=None, exc=None):
    """Patch requests.post — the symbol BOTH /media/presign and /posts go through, so a test can prove
    /posts was never reached by inspecting the recorded urls."""
    kw = {"side_effect": exc} if exc is not None else {"return_value": resp if resp is not None else _presign_ok()}
    return mocker.patch("fanops.post.zernio.requests.post", **kw)


def _mock_put(mocker, resp=None, exc=None):
    kw = {"side_effect": exc} if exc is not None else {"return_value": resp if resp is not None else _R(200, {})}
    return mocker.patch("fanops.post.zernio.requests.put", **kw)


def _urls(mock):
    return [c.args[0] if c.args else c.kwargs.get("url", "") for c in mock.call_args_list]


def _transport_exc(cls, url=_UPLOAD_URL):
    """A REALISTIC requests transport error: the signed url appears in BOTH str(exc) and exc.request.url —
    the two vectors that would otherwise reach run.py's redact(), which knows only the two API keys."""
    exc = cls(f"HTTPSConnectionPool(host='storage.zernio.com', port=443): Max retries exceeded with url: "
              f"{url} (Caused by NewConnectionError('<urllib3.connection.HTTPSConnection>: Failed to "
              f"establish a new connection: [Errno 61] Connection refused'))")
    exc.request = SimpleNamespace(url=url, method="PUT", headers={})
    return exc


def _accounts(tmp_path):
    p = Config(root=tmp_path).accounts_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"accounts": [{"handle": "@tk", "platforms": ["tiktok"], "status": "active",
                                           "backends": {"tiktok": "zernio"}, "integrations": {"tiktok": "z1"}}]}))


def _queued(cfg, pid="p1", cid="c1", *, public_url="dryrun://p1"):
    """A queued TikTok post with NO media_urls -> _ensure_media resolves it via ensure_clip_media, which
    dispatches to the REAL zernio_upload_media (the code under test). The 5-byte clip is under the cap, so
    maybe_shrink_for_cap returns it untouched and no ffmpeg runs."""
    f = cfg.clips / f"{cid}.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"VIDEO")
    with Ledger.transaction(cfg) as led:
        led.add_clip(Clip(id=cid, parent_id="mom_1", path=str(f), state=ClipState.queued))
        # created_at: required to publish (report 11 §8.4) — the per-incarnation discriminator in the
        # x-request-id. Only the tests that get PAST the upload reach that refusal (the rest stub presign to
        # fail first), but the fixture must match what production mints: every mint site stamps it.
        led.add_post(Post(id=pid, parent_id=cid, account="tk", account_id="z1", platform=Platform.tiktok,
                          caption="c", scheduled_time="2020-01-01T00:00:00Z", state=PostState.queued,
                          created_at="2026-07-16T13:31:00Z", public_url=public_url))
    return f


def _leak_probe(tmp_path, monkeypatch, mocker, caplog, exc_cls):
    """Drive EVERY sink an operator can read, for one signed-PUT transport failure: the exception
    zernio_upload_media raises, the ledger error_reason a FULL _publish_one writes, the house run.log, and
    the stdlib log stream. Returns (raised, post, blob) with blob = every sink concatenated."""
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); f = _queued(cfg)
    _mock_presign(mocker); _mock_put(mocker, exc=_transport_exc(exc_cls))
    with pytest.raises(_rq.exceptions.RequestException) as ei:
        zernio_upload_media(cfg, f, account_id="z1")
    raised = ei.value
    with caplog.at_level(logging.DEBUG):
        _publish_one(cfg, "p1", "zernio")
    post = Ledger.load(cfg).posts["p1"]
    runlog = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    blob = "\n".join([str(raised), repr(raised), post.error_reason or "", runlog, caplog.text])
    return raised, post, blob


# ---------------------------------------------------------------- §9.1 Success path (1-6)

def test_presign_then_put_returns_public_url(tmp_path, monkeypatch, mocker):
    # 1 — the whole contract in one pass: presign once, PUT to the url IT returned, return ITS publicUrl.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    p = _mock_presign(mocker); u = _mock_put(mocker)
    assert zernio_upload_media(cfg, f, account_id=_ACC) == _PUBLIC_URL
    assert p.call_count == 1
    assert u.call_count == 1
    assert u.call_args_list[0].args[0] == _UPLOAD_URL     # PUT goes where presign said, not to a hardcoded path


def test_presign_url_is_base_plus_media_presign(tmp_path, monkeypatch, mocker):
    # 2 — pins the doubled-v1 trap: the base ALREADY ends in /v1, so the docs' "/v1/media/presign"
    # shorthand must NOT be appended literally (report 09 §3.2).
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    p = _mock_presign(mocker); _mock_put(mocker)
    zernio_upload_media(cfg, f, account_id=_ACC)
    assert p.call_args_list[0].args[0] == "https://zernio.com/api/v1/media/presign"


def test_public_url_flows_into_media_items(tmp_path, monkeypatch, mocker):
    # 3 — the returned publicUrl is what the post payload references.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker); _mock_put(mocker)
    url = zernio_upload_media(cfg, f, account_id=_ACC)
    payload = build_zernio_payload(account_id=_ACC, platform="tiktok", content="fire",
                                   media_urls=[url], scheduled_time=None)
    assert payload["mediaItems"] == [{"type": "video", "url": _PUBLIC_URL}]


def test_account_id_not_sent_to_presign(tmp_path, monkeypatch, mocker):
    # 4 — presign is account-agnostic (unlike the per-account token mint it replaces); accountId is not a
    # presign field, and sending an undocumented key invites a 400.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    p = _mock_presign(mocker); _mock_put(mocker)
    zernio_upload_media(cfg, f, account_id=_ACC)
    assert "accountId" not in p.call_args_list[0].kwargs["json"]


def test_size_sent_and_is_post_shrink_bytes(tmp_path, monkeypatch, mocker):
    # 5 — `size` is a documented optional presign field for pre-validation (max 5GB). It must describe the
    # bytes we ACTUALLY PUT: maybe_shrink_for_cap may rewrite the file, so the pre-shrink size would
    # pre-validate a file we never send — worse than omitting it (report 09 §8.3).
    cfg = _cfg(tmp_path, monkeypatch)
    big = tmp_path / "v.mp4"; big.write_bytes(b"V" * 5000)
    small = tmp_path / "v.shrunk.mp4"; small.write_bytes(b"V" * 900)
    mocker.patch("fanops.post.zernio.maybe_shrink_for_cap", return_value=small)
    p = _mock_presign(mocker); _mock_put(mocker)
    zernio_upload_media(cfg, big, account_id=_ACC)
    sent = p.call_args_list[0].kwargs["json"]
    assert isinstance(sent["size"], int)
    assert sent["size"] == small.stat().st_size == 900
    assert sent["size"] != big.stat().st_size            # NOT the pre-shrink size
    assert sent["filename"] == "v.shrunk.mp4"            # and the name follows the bytes too


def test_content_type_is_enum_member(tmp_path, monkeypatch, mocker):
    # 6 — contentType is a presign enum; video/mp4 is a member.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    p = _mock_presign(mocker); _mock_put(mocker)
    zernio_upload_media(cfg, f, account_id=_ACC)
    assert p.call_args_list[0].kwargs["json"]["contentType"] == "video/mp4"


# ---------------------------------------------------------------- §9.2 PUT header rules (7-10)

def test_put_carries_no_authorization_header(tmp_path, monkeypatch, mocker):
    # 7 — SECURITY. The url is pre-signed ("no auth header needed"); presenting the Bearer key would hand
    # the operator's Zernio credential to third-party storage.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker); u = _mock_put(mocker)
    zernio_upload_media(cfg, f, account_id=_ACC)
    hdrs = u.call_args_list[0].kwargs["headers"]
    assert not any(k.lower() == "authorization" for k in hdrs)
    assert "sk_test" not in json.dumps(hdrs)


def test_put_content_type_matches_presign(tmp_path, monkeypatch, mocker):
    # 8 — a signature is computed over the declared Content-Type; a mismatch fails the signature.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    p = _mock_presign(mocker); u = _mock_put(mocker)
    zernio_upload_media(cfg, f, account_id=_ACC)
    assert u.call_args_list[0].kwargs["headers"]["Content-Type"] == p.call_args_list[0].kwargs["json"]["contentType"]


def test_put_body_is_raw_bytes_not_multipart(tmp_path, monkeypatch, mocker):
    # 9 — raw bytes, not the legacy multipart envelope. A multipart body would upload the MIME wrapper as
    # if it were the video.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker); u = _mock_put(mocker)
    zernio_upload_media(cfg, f, account_id=_ACC)
    kw = u.call_args_list[0].kwargs
    assert "files" not in kw
    assert hasattr(kw["data"], "read")                   # a file object streamed as the body


def test_put_method_is_put_not_post(tmp_path, monkeypatch, mocker):
    # 10 — THE 405 REGRESSION, pinned directly. A 405 is a routing verdict on (method, path) alone; the
    # dead contract POSTed to /media/upload. Nothing may POST to any /media/upload* path again.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    p = _mock_presign(mocker); u = _mock_put(mocker)
    zernio_upload_media(cfg, f, account_id=_ACC)
    assert u.call_count == 1
    assert not any("/media/upload" in url for url in _urls(p))
    assert not any("/media/upload-token" in url for url in _urls(p))


# ---------------------------------------------------------------- §9.3 Signed-url redaction — RESPONSE (11-14)

def test_signed_url_signature_never_in_response_error(tmp_path, monkeypatch, mocker):
    # 11 — storage error pages routinely echo the request url. redact() knows only the API key, so the
    # signature needs its own scrubber.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker)
    _mock_put(mocker, _R(403, {}, text=f"<Error><Message>signature mismatch for {_UPLOAD_URL}</Message></Error>"))
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    msg = str(ei.value)
    assert _SIG not in msg and _TOK not in msg
    assert "<redacted>" in msg
    assert "X-Amz-Signature=" in msg                     # the NAME survives as a breadcrumb; the VALUE does not


def test_signed_url_never_in_ledger_error_reason_from_response(tmp_path, monkeypatch, mocker):
    # 12 — the same, at the sink that actually persists: a full _publish_one writes error_reason.
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg)
    _mock_presign(mocker)
    _mock_put(mocker, _R(403, {}, text=f"denied: {_UPLOAD_URL}"))
    _publish_one(cfg, "p1", "zernio")
    reason = Ledger.load(cfg).posts["p1"].error_reason or ""
    assert reason
    for s in _SECRETS:
        assert s not in reason


def test_api_key_never_in_error_evidence(tmp_path, monkeypatch, mocker):
    # 13 — redact() still does its original job: a WAF/debug page reflecting the presented Bearer key.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, _R(500, {}, text="upstream rejected Authorization: Bearer sk_test"))
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert "sk_test" not in str(ei.value)
    assert "***" in str(ei.value)


def test_evidence_is_bounded(tmp_path, monkeypatch):
    # 14 — a 1 MB error page must not become a 1 MB ledger row. The body is capped at 400 chars; the
    # returned string adds only the `body=` wrapper and an optional `Allow=` prefix.
    cfg = _cfg(tmp_path, monkeypatch)
    ev = _evidence(cfg, _R(500, {}, text=("A" * 1_000_000) + "TAIL-SENTINEL"))
    assert len(ev) <= 460
    assert "TAIL-SENTINEL" not in ev


# ---------------------------------------------------------------- §9.4 Signed-url redaction — TRANSPORT (15-20)
# The leak Rev 2 would have shipped: requests embeds the full presigned url in BOTH str(exc) and
# exc.request.url, and run.py's publish handler redacts only the two API KEYS — so an unwrapped
# Timeout/ConnectionError writes X-Amz-Signature straight into the ledger (report 09 §8.5).

def test_put_timeout_signature_absent_from_exception_error_reason_and_logs(tmp_path, monkeypatch, mocker, caplog):
    # 15
    raised, post, blob = _leak_probe(tmp_path, monkeypatch, mocker, caplog, _rq.exceptions.Timeout)
    assert post.error_reason                             # the failure IS reported...
    assert _SIG not in blob                              # ...without the signature, in ANY sink


def test_put_connection_error_credential_and_token_absent_everywhere(tmp_path, monkeypatch, mocker, caplog):
    # 16 — a signature is not the only credential in the query string.
    raised, post, blob = _leak_probe(tmp_path, monkeypatch, mocker, caplog, _rq.exceptions.ConnectionError)
    assert "AKIAEXAMPLE" not in blob
    assert _TOK not in blob


def test_put_transport_error_never_contains_full_upload_url(tmp_path, monkeypatch, mocker, caplog):
    # 17 — not merely the credentials: the url itself, its host, and its path never appear.
    raised, post, blob = _leak_probe(tmp_path, monkeypatch, mocker, caplog, _rq.exceptions.ConnectionError)
    assert _UPLOAD_URL not in blob
    assert "storage.zernio.com" not in blob
    assert "/temp/1752_abc_v.mp4" not in blob


def test_put_transport_error_message_is_class_and_stage_only(tmp_path, monkeypatch, mocker):
    # 18 — the message is EXACTLY class + stage. No str(exc), no repr(exc), no host, no url. And the fresh
    # instance carries no request/response, which is the SECOND leak vector (exc.request.url).
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker); _mock_put(mocker, exc=_transport_exc(_rq.exceptions.ConnectTimeout))
    with pytest.raises(_rq.exceptions.RequestException) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    raised = ei.value
    assert str(raised) == "Zernio signed upload transport failed (ConnectTimeout)"
    assert raised.request is None and raised.response is None
    assert raised.__cause__ is None                      # `from None` — no chained traceback reprints the url


def test_put_transport_failure_never_calls_posts(tmp_path, monkeypatch, mocker):
    # 19 — a failed upload must not publish a post with no media.
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg)
    p = _mock_presign(mocker); _mock_put(mocker, exc=_transport_exc(_rq.exceptions.ConnectionError))
    _publish_one(cfg, "p1", "zernio")
    assert not any(url.endswith("/posts") for url in _urls(p))
    assert Ledger.load(cfg).posts["p1"].state is not PostState.published


def test_put_connection_error_remains_transient(tmp_path, monkeypatch, mocker):
    # 20 — REGRESSION GUARD (report 09 §8.4.1). _is_transient_publish_error classifies a RequestException
    # by TYPE but a RuntimeError by MESSAGE SUBSTRING. Wrapping in RuntimeError would silently make this
    # ConnectionError TERMINAL (burning a retryable post on one network blip) while a Timeout — whose class
    # name contains "timeout" — stayed transient. Re-raising the SAME CLASS keeps classification identical.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker); _mock_put(mocker, exc=_transport_exc(_rq.exceptions.ConnectionError))
    with pytest.raises(_rq.exceptions.RequestException) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert isinstance(ei.value, _rq.exceptions.ConnectionError)
    assert _is_transient_publish_error(ei.value) is True


# ---------------------------------------------------------------- §9.5 Status-code matrix (21-29)

def test_405_on_put_surfaces_allow_header(tmp_path, monkeypatch, mocker):
    # 21 — THE regression that cost four posts. RFC 9110 REQUIRES a 405 to carry Allow naming the permitted
    # methods: the server answered the question on every one of those failures and the client dropped it.
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg)
    _mock_presign(mocker)
    _mock_put(mocker, _R(405, {}, text="Method Not Allowed", headers={"Allow": "GET, HEAD"}))
    _publish_one(cfg, "p1", "zernio")
    reason = Ledger.load(cfg).posts["p1"].error_reason or ""
    assert "Allow=" in reason and "GET, HEAD" in reason
    assert "405" in reason


def test_405_on_presign_surfaces_allow_header(tmp_path, monkeypatch, mocker):
    # 22 — the same discipline on the presign leg.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, _R(405, {}, text="Method Not Allowed", headers={"Allow": "POST"}))
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert "Allow=" in str(ei.value) and "405" in str(ei.value)


def test_400_on_presign_is_redacted_and_fails_the_post(tmp_path, monkeypatch, mocker):
    # 23 — the DOCUMENTED presign 400: "missing filename, contentType, or unsupported content type".
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg)
    _mock_presign(mocker, _R(400, {}, text=json.dumps({"error": "unsupported content type",
                                                       "type": "validation_error", "code": "BAD_CONTENT_TYPE"})))
    put = _mock_put(mocker)
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.failed
    assert "BAD_CONTENT_TYPE" in (p.error_reason or "")   # bounded evidence is KEPT — that is the point
    put.assert_not_called()


def test_401_on_presign_is_typed_auth_error_body_withheld(tmp_path, monkeypatch, mocker):
    # 24 — a bad key fails EVERY post: it must halt the run (typed AuthError), never burn the queue. The
    # body is withheld entirely, matching the existing auth-error discipline.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, _R(401, {}, text="unauthorized SENTINEL-BODY"))
    put = _mock_put(mocker)
    with pytest.raises(ZernioAuthError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert "SENTINEL-BODY" not in str(ei.value)
    put.assert_not_called()


def test_401_on_put_is_not_an_auth_error(tmp_path, monkeypatch, mocker):
    # 25 — a signed url carries NO Bearer, so a 401 there says nothing about the API key. Raising
    # ZernioAuthError would halt the whole run over one expired signature.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker); _mock_put(mocker, _R(401, {}, text="expired signature"))
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert not isinstance(ei.value, ZernioAuthError)
    assert "401" in str(ei.value)


def test_413_on_put_fails_cleanly_with_evidence(tmp_path, monkeypatch, mocker):
    # 26 — 413 is NOT in Zernio's documented taxonomy (400/422, 401, 403, 404, 429, 500, 502). The old 4 MB
    # cap was reverse-engineered from exactly such a live 413; it must stay a generic failure with evidence,
    # never a special case that re-derives a constant from a burn.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker); _mock_put(mocker, _R(413, {}, text="Payload Too Large"))
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert "413" in str(ei.value) and "Payload Too Large" in str(ei.value)


def test_429_on_presign_surfaces_and_never_succeeds(tmp_path, monkeypatch, mocker):
    # 27 — rate limiting must never be mistaken for an upload.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, _R(429, {}, text="rate limited"))
    put = _mock_put(mocker)
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert "429" in str(ei.value)
    put.assert_not_called()


def test_500_on_presign_fails_never_publishes(tmp_path, monkeypatch, mocker):
    # 28 — a 5xx on PRESIGN is unambiguous: nothing was posted, no media exists. failed (re-queueable) is
    # correct; published is not.
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg)
    _mock_presign(mocker, _R(500, {}, text="internal"))
    _mock_put(mocker)
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.failed
    assert p.published_at is None


def test_502_on_presign_is_not_a_success(tmp_path, monkeypatch, mocker):
    # 29 — a platform_error upstream is not an upload.
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg)
    _mock_presign(mocker, _R(502, {}, text="bad gateway"))
    _mock_put(mocker)
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.failed
    assert p.state is not PostState.published


# ---------------------------------------------------------------- §9.6 Malformed responses (30-34)

def test_presign_2xx_but_no_upload_url(tmp_path, monkeypatch, mocker):
    # 30 — no url to PUT to: fail cleanly BEFORE opening the file.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, _R(200, {"publicUrl": _PUBLIC_URL}))
    put = _mock_put(mocker)
    with pytest.raises(RuntimeError, match="uploadUrl"):
        zernio_upload_media(cfg, f, account_id=_ACC)
    put.assert_not_called()


def test_presign_2xx_but_no_public_url(tmp_path, monkeypatch, mocker):
    # 31 — uploading with nothing to reference afterwards would burn the bytes for nothing.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, _R(200, {"uploadUrl": _UPLOAD_URL}))
    put = _mock_put(mocker)
    with pytest.raises(RuntimeError, match="publicUrl"):
        zernio_upload_media(cfg, f, account_id=_ACC)
    put.assert_not_called()


def test_presign_2xx_non_json(tmp_path, monkeypatch, mocker):
    # 32 — a proxy/WAF HTML page with a 200. No unhandled JSONDecodeError.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, _R(200, ValueError("Expecting value: line 1 column 1"), text="<html>hi</html>"))
    _mock_put(mocker)
    with pytest.raises(RuntimeError, match="presign 2xx"):
        zernio_upload_media(cfg, f, account_id=_ACC)


def test_presign_2xx_empty_body(tmp_path, monkeypatch, mocker):
    # 33
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, _R(200, {}))
    _mock_put(mocker)
    with pytest.raises(RuntimeError, match="presign 2xx"):
        zernio_upload_media(cfg, f, account_id=_ACC)


def test_put_2xx_body_ignored(tmp_path, monkeypatch, mocker):
    # 34 — publicUrl comes from STEP 1. Storage returns an empty body on a successful PUT; parsing it (as
    # the legacy contract did) is exactly the coupling that broke.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker); _mock_put(mocker, _R(200, ValueError("no json"), text=""))
    assert zernio_upload_media(cfg, f, account_id=_ACC) == _PUBLIC_URL


# ---------------------------------------------------------------- §9.7 Transport — presign side (35-37)

def test_presign_transport_error_is_bounded_and_redacted(tmp_path, monkeypatch, mocker):
    # 35 — the presign url is NOT a credential, so bounded redacted transport evidence is allowed here. The
    # API key must still never appear.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    exc = _rq.exceptions.ConnectionError("HTTPSConnectionPool(host='zernio.com', port=443): Max retries "
                                         "exceeded with url: /api/v1/media/presign (key sk_test) " + "Z" * 5000)
    _mock_presign(mocker, exc=exc)
    with pytest.raises(_rq.exceptions.RequestException) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    msg = str(ei.value)
    assert "sk_test" not in msg
    assert "media/presign" in msg                        # the presign url MAY appear — it is not a secret
    assert len(msg) < 300                                # detail capped at 200 + the fixed prefix


def test_presign_transport_error_preserves_class(tmp_path, monkeypatch, mocker):
    # 36 — same class-preserving rule as the signed PUT: a wrap must not change transient classification.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _mock_presign(mocker, exc=_rq.exceptions.ConnectionError("dropped"))
    with pytest.raises(_rq.exceptions.RequestException) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert isinstance(ei.value, _rq.exceptions.ConnectionError)
    assert _is_transient_publish_error(ei.value) is True


def test_presign_transport_failure_never_calls_put_or_posts(tmp_path, monkeypatch, mocker):
    # 37
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg)
    p = _mock_presign(mocker, exc=_rq.exceptions.ConnectionError("dropped"))
    put = _mock_put(mocker)
    _publish_one(cfg, "p1", "zernio")
    put.assert_not_called()
    assert not any(url.endswith("/posts") for url in _urls(p))


# ---------------------------------------------------------------- §9.8 mediaItems + TikTok (38-40)
# Regression guards on code this PR does NOT change. Post creation was already correct against the
# official contract — only the upload was obsolete (report 09 §6.2).

def test_media_items_not_legacy_media_key(tmp_path, monkeypatch):
    # 38
    p = build_zernio_payload(account_id=_ACC, platform="tiktok", content="c",
                             media_urls=[_PUBLIC_URL], scheduled_time=None)
    assert "mediaItems" in p and "media" not in p


def test_tiktok_payload_shape_unchanged():
    """Pins the shape FanOps sends today. The official Platform Settings guide documents this nested form
    verbatim; the OpenAPI models `platformSpecificData` directly as `TikTokPlatformData`; the two current
    official sources CONFLICT (report 09 §5.4). 21 production publishes prove it is ACCEPTED; which values
    the platform APPLIES is unverified. This test makes any change deliberate and visible — it does NOT
    assert the shape is correct, incorrect, or inert."""
    # 39
    p = build_zernio_payload(account_id=_ACC, platform="tiktok", content="c",
                             media_urls=[_PUBLIC_URL], scheduled_time=None)
    assert p["platforms"][0]["platformSpecificData"] == {
        "tiktokSettings": {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True, "allow_duet": True,
                           "allow_stitch": True, "content_preview_confirmed": True, "express_consent_given": True}}


def test_platforms_shape():
    # 40
    p = build_zernio_payload(account_id=_ACC, platform="tiktok", content="c",
                             media_urls=[_PUBLIC_URL], scheduled_time=None)
    assert p["platforms"][0]["platform"] == "tiktok"
    assert p["platforms"][0]["accountId"] == _ACC


# ---------------------------------------------------------------- §9.10 Ledger integrity (44-46)

def test_upload_failure_sets_failed_with_reason(tmp_path, monkeypatch, mocker):
    # 44 — a failed upload leaves NO trace of success anywhere on the row.
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg, public_url=None)
    _mock_presign(mocker, _R(400, {}, text="bad request"))
    _mock_put(mocker)
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is PostState.failed
    assert p.error_reason
    assert p.media_id is None and p.public_url is None and p.published_at is None


def test_upload_failure_leaves_other_posts_untouched(tmp_path, monkeypatch, mocker):
    # 45 — no collateral damage: the 343 parked rows must not move because one upload failed.
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p2", parent_id="c1", account="tk", account_id="z1", platform=Platform.tiktok,
                          caption="other", scheduled_time="2020-01-01T00:00:00Z",
                          state=PostState.awaiting_approval))
    _mock_presign(mocker, _R(400, {}, text="bad request"))
    _mock_put(mocker)
    _publish_one(cfg, "p1", "zernio")
    p2 = Ledger.load(cfg).posts["p2"]
    assert p2.state is PostState.awaiting_approval
    assert p2.error_reason is None


def test_success_transitions_queued_to_submitted_only(tmp_path, monkeypatch, mocker):
    # 46 — an upload success is not a publish success. Zernio returns no permalink on the create 2xx, so
    # the R1/D2 gate parks it for reconcile; it must never self-promote to published.
    cfg = _live_cfg(tmp_path, monkeypatch); _accounts(tmp_path); _queued(cfg, public_url=None)
    _mock_put(mocker)
    # requests.post is the symbol BOTH legs use: call 1 is presign, call 2 is the real /posts create.
    mocker.patch("fanops.post.zernio.requests.post", side_effect=[_presign_ok(), _R(201, {"_id": "z_post_1"})])
    _publish_one(cfg, "p1", "zernio")
    p = Ledger.load(cfg).posts["p1"]
    assert p.state is not PostState.published
    assert p.state in (PostState.submitted, PostState.needs_reconcile)


# ---------------------------------------------------------------- Carried over from test_zernio_media.py
# These two outlived the contract that file pinned: neither touches the upload wire format.

def test_media_uploader_dispatches_to_zernio(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert get_media_uploader(cfg, "zernio") is zernio_upload_media


def test_zernio_upload_rejects_oversize_before_network(tmp_path, monkeypatch, mocker):
    # The cap preflight still fails BEFORE any network call. (The 4 MB cap itself is a second legacy
    # artifact — reverse-engineered from a live 413 vs presign's documented 5 GB — and is deliberately
    # NOT changed here: it alters output quality, a different risk surface. Report 09 §4.5, §8.7.)
    monkeypatch.setenv("FANOPS_ZERNIO_MAX_UPLOAD_MB", "1")
    cfg = _cfg(tmp_path, monkeypatch)
    f = tmp_path / "big.mp4"; f.write_bytes(b"V" * (2 * 1024 * 1024))
    post = mocker.patch("fanops.post.zernio.requests.post")
    put = mocker.patch("fanops.post.zernio.requests.put")
    with pytest.raises(RuntimeError, match="oversize"):
        zernio_upload_media(cfg, f, account_id=_ACC)
    post.assert_not_called()
    put.assert_not_called()
