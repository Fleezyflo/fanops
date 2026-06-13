# tests/test_postiz.py — the free, self-hosted Postiz poster backend (FANOPS_POSTER=postiz). All
# offline (mocked requests). REST contract confirmed vs docs.postiz.com/public-api: Authorization:
# {key} header, POST /public/v1/upload(-from-url), POST /public/v1/posts. The exact response id key
# + image-ref shape are INTEGRATION CHECKPOINTS (locked by SHAPE here, like the Blotato posters).
import pytest
from fanops.config import Config
from fanops.errors import PostizAuthError
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.post.postiz import PostizPoster, build_postiz_payload, postiz_upload_media
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
def test_postiz_upload_media_returns_hosted_url(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    f = tmp_path / "a.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.postiz.requests.post",
                 return_value=_R(201, {"id": "img1", "path": "https://uploads.postiz.com/a.mp4"}))
    assert postiz_upload_media(cfg, f) == "https://uploads.postiz.com/a.mp4"

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
