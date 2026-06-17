# tests/test_postiz.py — the free, self-hosted Postiz poster backend (FANOPS_POSTER=postiz). All
# offline (mocked requests). REST contract confirmed vs docs.postiz.com/public-api: Authorization:
# {key} header, POST /public/v1/upload(-from-url), POST /public/v1/posts. The exact response id key
# + image-ref shape are INTEGRATION CHECKPOINTS (locked by SHAPE here, like the Blotato posters).
import pytest
from fanops.config import Config
from fanops.errors import PostizAuthError
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.post.postiz import (PostizPoster, build_postiz_payload, postiz_upload_media,
                                postiz_list_integrations, postiz_check_auth, PostizIntegration)
from fanops.post import get_poster, get_media_uploader


class _R:
    def __init__(s, code, body=None, text=""):
        s.status_code = code; s._b = body if body is not None else {}; s.text = text
    def json(s): return s._b


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    return Config(root=tmp_path)

def _post(pid="p1", acct_id="intg_1"):
    return Post(id=pid, parent_id="c1", account="@a", account_id=acct_id, platform=Platform.instagram,
                caption="fire", state=PostState.submitting,
                media_urls=["https://uploads.postiz.com/x.mp4"], scheduled_time="2099-01-01T00:00:00Z")

def _led(cfg, post):
    led = Ledger.load(cfg); led.add_post(post); return led


# ---- payload shape (offline lock) ----
def test_payload_shape():
    p = build_postiz_payload(integration_id="intg_1", platform="instagram", content="fire",
                             media_urls=["https://uploads.postiz.com/x.mp4"],
                             scheduled_time="2099-01-01T00:00:00Z")
    assert p["type"] == "schedule" and p["date"] == "2099-01-01T00:00:00Z"
    body = p["posts"][0]
    assert body["integration"]["id"] == "intg_1"
    assert body["settings"]["__type"] == "instagram"
    assert body["value"][0]["content"] == "fire"
    assert body["value"][0]["image"][0]["path"] == "https://uploads.postiz.com/x.mp4"


def test_payload_image_carries_id_and_path_and_post_type():
    # Round-3 contract fix (verified live vs the running Postiz): this version's image[] requires BOTH
    # `id` AND `path` (it validates id as a string and the path's extension), and settings needs a
    # `post_type` of "post"/"story". The uploader feeds "id|path"; build_postiz_payload splits it.
    p = build_postiz_payload(integration_id="intg_1", platform="instagram", content="fire",
                             media_urls=["mid_9|https://uploads.postiz.com/x.mp4"],
                             scheduled_time="2099-01-01T00:00:00Z")
    img = p["posts"][0]["value"][0]["image"][0]
    assert img["id"] == "mid_9" and img["path"] == "https://uploads.postiz.com/x.mp4"
    assert p["posts"][0]["settings"]["post_type"] == "post"


# ---- factory wiring ----
def test_get_poster_returns_postiz(tmp_path, monkeypatch):
    assert isinstance(get_poster(_cfg(tmp_path, monkeypatch)), PostizPoster)

def test_media_uploader_dispatches_to_postiz(tmp_path, monkeypatch):
    assert get_media_uploader(_cfg(tmp_path, monkeypatch)) is postiz_upload_media


# ---- publish state machine (mirrors the Blotato poster's safety) ----
def test_publish_submitted_on_2xx_with_id(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(201, {"id": "postiz_1"}))
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.submitted and led.posts["p1"].submission_id == "postiz_1"

def test_publish_401_is_typed_auth_redacted(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post",
                 return_value=_R(401, {"e": "denied SENTINEL"}, text="denied SENTINEL"))
    with pytest.raises(PostizAuthError) as ei:
        PostizPoster(cfg).publish(led, "p1")
    assert "SENTINEL" not in str(ei.value)

def test_publish_5xx_parks_needs_reconcile_no_repost(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(500, {}, text="boom"))
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile

def test_publish_2xx_no_id_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(200, {"ok": True}))
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile

def test_publish_other_4xx_fails(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(422, {}, text="bad"))
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.failed

def test_publish_network_error_parks_needs_reconcile_no_repost(tmp_path, monkeypatch, mocker):
    # The body may have landed on Postiz (the response, not the request, was lost) — ambiguous, so
    # park needs_reconcile and NEVER re-POST (no idempotency key). The safety-critical path.
    import requests as _rq
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post",
                 side_effect=_rq.exceptions.ConnectionError("dropped"))
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile

def test_publish_429_exhausted_marks_failed(tmp_path, monkeypatch, mocker):
    # A 429 is rejected pre-processing (not posted), so retrying is safe; exhausting retries -> failed
    # (re-queueable), never needs_reconcile. Mock sleep so the jittered backoff doesn't stall the test.
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.time.sleep")
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(429, {}, text="rate"))
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.failed


# ---- construction guards ----
def test_missing_key_raises_typed_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://x")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    with pytest.raises(PostizAuthError):
        PostizPoster(Config(root=tmp_path))

def test_missing_url_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.delenv("POSTIZ_URL", raising=False)
    with pytest.raises(Exception):
        PostizPoster(Config(root=tmp_path))


# ---- media upload (multipart -> uploads.postiz.com path) ----
def test_postiz_upload_media_returns_id_and_path(tmp_path, monkeypatch, mocker):
    # Round-3 contract fix: image[] needs BOTH the media id and its public path, so the uploader returns
    # them joined "id|path" (build_postiz_payload splits them back). Locked vs the real /upload response.
    cfg = _cfg(tmp_path, monkeypatch)
    f = tmp_path / "a.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.postiz.requests.post",
                 return_value=_R(201, {"id": "img1", "path": "https://uploads.postiz.com/a.mp4"}))
    assert postiz_upload_media(cfg, f) == "img1|https://uploads.postiz.com/a.mp4"

def test_postiz_upload_media_401_typed(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    f = tmp_path / "a.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(401, {}, text="x"))
    with pytest.raises(PostizAuthError):
        postiz_upload_media(cfg, f)


# ---- preflight + doctor wiring (postiz needs URL + key) ----
def test_preflight_blocks_postiz_without_creds(tmp_path, monkeypatch):
    from fanops.cli import _check_preflight
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.delenv("POSTIZ_URL", raising=False); monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    assert _check_preflight(Config(root=tmp_path)) == 2

def test_preflight_passes_postiz_with_creds(tmp_path, monkeypatch):
    from fanops.cli import _check_preflight
    assert _check_preflight(_cfg(tmp_path, monkeypatch)) == 0

def test_doctor_flags_postiz_creds(tmp_path, monkeypatch):
    from fanops import doctor
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.delenv("POSTIZ_URL", raising=False); monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert any("POSTIZ" in c["label"] and not c["ok"] for c in rep["checks"])


# ---- integrations list (Go-Live tab: map an account to a Postiz integration without hand-editing
# accounts.json). GET /public/v1/integrations; response SHAPE is an integration checkpoint (locked
# here): a bare list OR {"integrations":[...]}, each item -> {id, name, platform}, malformed skipped.
def test_list_integrations_parses_id_name_platform(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get",
                 return_value=_R(200, [{"id": "intg_42", "name": "IG Reels", "identifier": "instagram"}]))
    out = postiz_list_integrations(cfg)
    assert out == [PostizIntegration("intg_42", "IG Reels", "instagram")]
    assert out[0].id == "intg_42" and out[0].name == "IG Reels" and out[0].platform == "instagram"  # named fields (W7)

def test_list_integrations_accepts_wrapped_shape(tmp_path, monkeypatch, mocker):
    # name falls back to the platform identifier when Postiz omits a display name
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get",
                 return_value=_R(200, {"integrations": [{"id": "i1", "identifier": "tiktok"}]}))
    assert postiz_list_integrations(cfg) == [PostizIntegration("i1", "tiktok", "tiktok")]

def test_list_integrations_skips_malformed_item(tmp_path, monkeypatch, mocker):
    # no usable id, or a non-dict entry -> skipped (not raised); the good one survives
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get",
                 return_value=_R(200, [{"name": "no id"}, "garbage", {"id": "ok", "identifier": "youtube"}]))
    assert postiz_list_integrations(cfg) == [PostizIntegration("ok", "youtube", "youtube")]

def test_list_integrations_coerces_numeric_id(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get",
                 return_value=_R(200, [{"id": 51, "name": "TikTok", "identifier": "tiktok"}]))
    assert postiz_list_integrations(cfg) == [PostizIntegration("51", "TikTok", "tiktok")]

def test_list_integrations_401_typed_redacted(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get",
                 return_value=_R(401, {"e": "denied SENTINEL"}, text="denied SENTINEL"))
    with pytest.raises(PostizAuthError) as ei:
        postiz_list_integrations(cfg)
    assert "SENTINEL" not in str(ei.value)

def test_list_integrations_5xx_raises_runtime(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(503, {}, text="down"))
    with pytest.raises(RuntimeError):
        postiz_list_integrations(cfg)


# ---- cheap auth probe (Go-Live "Save & test"): 2xx -> True, 401 -> typed (halt), else False
def test_check_auth_true_on_2xx(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(200, []))
    assert postiz_check_auth(cfg) is True

def test_check_auth_raises_on_401(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(401, {}, text="x"))
    with pytest.raises(PostizAuthError):
        postiz_check_auth(cfg)

def test_check_auth_false_on_other_failure(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(500, {}, text="boom"))
    assert postiz_check_auth(cfg) is False

def test_check_auth_logs_swallowed_failure(tmp_path, monkeypatch, mocker, caplog):
    # W8: a swallowed (non-401) probe failure must be LOGGED so a silent "auth failed" is diagnosable.
    import logging
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get", return_value=_R(503, {}, text="down"))
    with caplog.at_level(logging.WARNING, logger="fanops.post.postiz"):
        assert postiz_check_auth(cfg) is False
    assert "auth probe failed" in caplog.text

def test_check_auth_false_on_network_error(tmp_path, monkeypatch, mocker):
    import requests as _rq
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.postiz.requests.get", side_effect=_rq.exceptions.ConnectionError("dropped"))
    assert postiz_check_auth(cfg) is False
