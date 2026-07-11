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
                                postiz_list_integrations, postiz_check_auth, PostizIntegration,
                                _extract_postiz_id, rewrite_media_base, _mirror_media_to_r2)
from fanops.post import get_poster, get_media_uploader


class _R:
    def __init__(s, code, body=None, text=""):
        s.status_code = code; s._b = body if body is not None else {}; s.text = text
    def json(s): return s._b


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("FANOPS_RESPONDER", "manual")   # hermetic: postiz creds alone must pass preflight
    return Config(root=tmp_path)

def _post(pid="p1", acct_id="intg_1"):
    # R1: state=submitting is non-terminal — no public_url required. Tests in this file exercise
    # the publish path that's MEANT to capture/keep public_url None on failure (D2 fail-closed).
    return Post(id=pid, parent_id="c1", account="a", account_id=acct_id, platform=Platform.instagram,
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

def test_publish_5xx_error_reason_withholds_response_body(tmp_path, monkeypatch, mocker):
    # SECURITY: a misconfigured self-hosted proxy can echo the Authorization header into a 5xx error
    # page; that body must NEVER land in error_reason (persisted to ledger.json + the digest on disk).
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(500, {}, text="upstream SENTINEL-BODY-ECHO"))
    er = PostizPoster(cfg).publish(led, "p1").posts["p1"].error_reason or ""
    assert "SENTINEL-BODY-ECHO" not in er and "500" in er         # status kept, body withheld

def test_publish_4xx_error_reason_withholds_response_body(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(422, {}, text="bad SENTINEL-BODY-ECHO"))
    er = PostizPoster(cfg).publish(led, "p1").posts["p1"].error_reason or ""
    assert "SENTINEL-BODY-ECHO" not in er and "422" in er

def test_publish_2xx_no_id_error_reason_withholds_response_body(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(200, {"ok": True}, text="SENTINEL-BODY-ECHO"))
    er = PostizPoster(cfg).publish(led, "p1").posts["p1"].error_reason or ""
    assert "SENTINEL-BODY-ECHO" not in er

def test_upload_media_error_withholds_response_body(tmp_path, monkeypatch, mocker):
    # the upload error RuntimeError reaches error_reason via _submit_one's publish-failure catch -> the
    # response body (which can echo the auth header) must be withheld there too, not only on publish.
    from fanops.post.postiz import postiz_upload_media
    cfg = _cfg(tmp_path, monkeypatch); f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(500, {}, text="upstream SENTINEL-BODY-ECHO"))
    with pytest.raises(RuntimeError) as ei:
        postiz_upload_media(cfg, f)
    assert "SENTINEL-BODY-ECHO" not in str(ei.value) and "500" in str(ei.value)

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

def test_publish_429_retries_then_succeeds(tmp_path, monkeypatch, mocker):
    # audit gap: only 429-EXHAUSTION was covered. A 429 is rejected pre-processing (not posted), so the
    # retry is safe — a transient 429 followed by a 2xx must land SUBMITTED, not failed. Mock sleep.
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.time.sleep")
    mocker.patch("fanops.post.postiz.requests.post",
                 side_effect=[_R(429, {}, text="rate"), _R(201, {"id": "postiz_9"})])
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.submitted and led.posts["p1"].submission_id == "postiz_9"

def test_publish_leg_drives_real_postiz_poster_not_dryrun(tmp_path, monkeypatch, mocker):
    # audit gap: the E2E publish leg runs through DryRunPoster (stamps submitted unconditionally). This drives
    # the REAL publish leg (_publish_one: claim -> network -> finalize) through PostizPoster with a MOCKED
    # network — proving a 201 maps queued -> PUBLISHED + submission_id end-to-end, no real post.
    from fanops.post.run import _publish_one
    cfg = _cfg(tmp_path, monkeypatch)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="intg_1", platform=Platform.instagram,
                          caption="fire", media_urls=["https://uploads.postiz.com/x.mp4"],   # already uploaded -> no media network
                          state=PostState.queued, public_url="dryrun://p1"))
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(201, {"id": "postiz_1"}))
    final = _publish_one(cfg, "p1", "postiz")
    led = Ledger.load(cfg)
    assert final == "published" and led.posts["p1"].state is PostState.published   # the REAL poster ran, not DryRun
    assert led.posts["p1"].submission_id == "postiz_1"


# ---- _extract_postiz_id (audit gap: key-precedence + nested posts[0].id + list body untested) ----
def test_extract_postiz_id_key_precedence():
    assert _extract_postiz_id({"id": "A", "postId": "B", "submissionId": "C"}) == "A"   # id wins
    assert _extract_postiz_id({"postId": "B", "submissionId": "C"}) == "B"              # then postId
    assert _extract_postiz_id({"submissionId": "C"}) == "C"                             # then submissionId

def test_extract_postiz_id_nested_and_list_and_absent():
    assert _extract_postiz_id({"posts": [{"id": "nested"}]}) == "nested"   # nested posts[0].id
    assert _extract_postiz_id([{"id": "first"}, {"id": "second"}]) == "first"   # top-level list -> [0]
    assert _extract_postiz_id({"id": 123}) is None        # non-str id ignored
    assert _extract_postiz_id({"nope": "x"}) is None      # no recognizable key
    assert _extract_postiz_id([]) is None and _extract_postiz_id("nope") is None   # empty list / non-dict


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

def test_preflight_blocks_zernio_without_creds(tmp_path, monkeypatch):
    from fanops.cli import _check_preflight
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    assert _check_preflight(Config(root=tmp_path)) == 2

def test_doctor_skips_postiz_check_without_creds(tmp_path, monkeypatch):
    # B11: FANOPS_POSTER=postiz without POSTIZ_API_KEY omits POSTIZ doctor checks (backend_has_creds gate).
    # Preflight still blocks a run — the trap is at run time, not in doctor.
    from fanops import doctor
    from fanops.cli import _check_preflight
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.delenv("POSTIZ_URL", raising=False); monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    rep = doctor.doctor_report(Config(root=tmp_path))
    assert not any("POSTIZ" in c["label"] for c in rep["checks"])
    assert _check_preflight(Config(root=tmp_path)) == 2


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

def test_list_integrations_non_401_redacts_key(tmp_path, monkeypatch, mocker):
    # audit (same key-echo class as the publish-path redaction): a NON-401 integrations error body that
    # reflects the key must NOT leak it into the RuntimeError (-> Studio Go-Live panel / operator terminal).
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_API_KEY", "SECRET-POSTIZ-KEY")
    mocker.patch("fanops.post.postiz.requests.get",
                 return_value=_R(500, {}, text="rejected Authorization=SECRET-POSTIZ-KEY"))
    with pytest.raises(RuntimeError) as ei:
        postiz_list_integrations(cfg)
    assert "SECRET-POSTIZ-KEY" not in str(ei.value) and "500" in str(ei.value)

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


# ---- P2 Task 1: _postiz_permalink — the single URL chokepoint, fail-safe None ----
# Postiz's public API returns NO social permalink and NO dashboard URL on any response (GET
# /public/v1/posts → {id, publishDate, state, integration, content}, Context7-verified), and Postiz
# documents no stable public per-post page path. So the helper ships returning None — a guessed
# 404-ing link is worse than None. These lock that contract; flipping the helper to a verified
# dashboard route later is a one-line change behind these same tests.
def test_postiz_permalink_none_for_empty_or_none(tmp_path, monkeypatch):
    from fanops.post.postiz import _postiz_permalink
    cfg = _cfg(tmp_path, monkeypatch)
    assert _postiz_permalink(cfg, "") is None
    assert _postiz_permalink(cfg, None) is None

def test_postiz_permalink_none_for_real_id_until_route_verified(tmp_path, monkeypatch):
    # A recognizable id STILL yields None: the API exposes no permalink and the per-post dashboard
    # route is unverified against the operator's Postiz version. None is correct; a 404-ing link is worse.
    from fanops.post.postiz import _postiz_permalink
    cfg = _cfg(tmp_path, monkeypatch)
    assert _postiz_permalink(cfg, "post_abc") is None


# ---- P2 Task 2: capture public_url on the SUBMITTED branch only, via the helper chokepoint ----
def test_publish_2xx_captures_public_url_via_permalink_helper(tmp_path, monkeypatch, mocker):
    # The submitted branch routes public_url through _postiz_permalink. Today the helper returns None
    # (no URL in the API), so to prove the WIRING (not a coincidental None==None) we stub the helper to
    # a sentinel and assert it lands on the post. When the route is later verified this lights up free.
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(201, {"id": "postiz_1"}))
    mocker.patch("fanops.post.postiz._postiz_permalink", return_value="https://dash.example/p/postiz_1")
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.submitted
    assert led.posts["p1"].submission_id == "postiz_1"
    assert led.posts["p1"].public_url == "https://dash.example/p/postiz_1"

# ---- R2 public media mirror + upload-from-url (v4 self-healing publish path) ----
def test_rewrite_media_base_rewrites_loopback_upload_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_MEDIA_PUBLIC_BASE", "https://media.example.com/clips")
    cfg = Config(root=tmp_path)
    assert rewrite_media_base("http://127.0.0.1:4007/uploads/clip_1.mp4", cfg) == \
        "https://media.example.com/clips/uploads/clip_1.mp4"
    assert rewrite_media_base("http://localhost:8787/media/render_x.9x16.mp4", cfg) == \
        "https://media.example.com/clips/media/render_x.9x16.mp4"


def test_rewrite_media_base_passthrough_foreign_https(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_MEDIA_PUBLIC_BASE", "https://media.example.com")
    cfg = Config(root=tmp_path)
    ext = "https://uploads.postiz.com/a.mp4"
    assert rewrite_media_base(ext, cfg) == ext


def test_rewrite_media_base_noop_without_public_base(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_MEDIA_PUBLIC_BASE", raising=False)
    cfg = Config(root=tmp_path)
    u = "http://127.0.0.1:4007/uploads/x.mp4"
    assert rewrite_media_base(u, cfg) == u


def test_mirror_media_to_r2_puts_and_returns_public_url(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_MEDIA_PUBLIC_BASE", "https://pub.r2.dev/fanops")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "clips")
    cfg = Config(root=tmp_path)
    f = tmp_path / "v.mp4"; f.write_bytes(b"VIDEO")
    put = mocker.patch("fanops.post.postiz.requests.put", return_value=_R(200))
    url = _mirror_media_to_r2(cfg, f)
    assert url.startswith("https://pub.r2.dev/fanops/fanops/")
    assert url.endswith(".mp4")
    assert put.called


def test_postiz_upload_media_uses_r2_mirror_and_upload_from_url(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_POSTER", "postiz")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("FANOPS_MEDIA_PUBLIC_BASE", "https://pub.r2.dev/fanops")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "clips")
    cfg = _cfg(tmp_path, monkeypatch)
    f = tmp_path / "a.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.postiz.requests.put", return_value=_R(200))
    post = mocker.patch("fanops.post.postiz.requests.post",
                        return_value=_R(201, {"id": "img1", "path": "https://uploads.postiz.com/a.mp4"}))
    out = postiz_upload_media(cfg, f)
    assert out == "img1|https://uploads.postiz.com/a.mp4"
    body = post.call_args[1]["json"]
    assert body["url"].startswith("https://pub.r2.dev/fanops/fanops/")
    assert post.call_args[0][0].endswith("/upload-from-url")


def test_publish_unconfirmed_branches_never_capture_public_url(tmp_path, monkeypatch, mocker):
    # 2xx-no-id / 5xx / network ⇒ needs_reconcile and public_url stays None EVEN IF the helper would
    # return a link — no confirmed id ⇒ no URL. Stub the helper to a sentinel to prove the branch
    # genuinely never assigns it (not that the helper happened to return None).
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.postiz._postiz_permalink", return_value="https://dash.example/should-not-appear")
    mocker.patch("fanops.post.postiz.requests.post", return_value=_R(200, {"ok": True}))   # 2xx, no id
    led = PostizPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile
    assert led.posts["p1"].public_url is None
