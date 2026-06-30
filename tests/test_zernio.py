# tests/test_zernio.py — Slice 1: the Zernio poster backend (FANOPS_POSTER=zernio). All offline
# (mocked requests). REST contract (operator-pasted docs): Authorization: Bearer <sk_…>, base
# https://zernio.com/api/v1, POST /posts {content, publishNow, platforms:[{platform, accountId}]},
# GET /accounts -> {accounts:[{_id, platform}]}. The create-post response id key + accounts shape are
# INTEGRATION CHECKPOINTS (locked by SHAPE here, like the Postiz/Blotato posters); verified live by the
# operator at connect/publish. publishNow:true because FanOps already gated the schedule.
import pytest
from fanops.config import Config
from fanops.errors import ZernioAuthError, AuthError
from fanops.ledger import Ledger
from fanops.models import Post, Platform, PostState
from fanops.post.zernio import (ZernioPoster, build_zernio_payload, zernio_list_accounts,
                                 zernio_check_auth, ZernioAccount)
from fanops.post import get_poster


class _R:
    def __init__(s, code, body=None, text=""):
        s.status_code = code; s._b = body if body is not None else {}; s.text = text
    def json(s): return s._b


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("ZERNIO_API_URL", raising=False)
    return Config(root=tmp_path)

def _post(pid="p1", acct_id="acc_abc"):
    return Post(id=pid, parent_id="c1", account="@tk", account_id=acct_id, platform=Platform.tiktok,
                caption="fire", state=PostState.submitting,
                media_urls=["https://media.zernio.com/x.mp4"], scheduled_time="2099-01-01T00:00:00Z", public_url=f"dryrun://c1")

def _led(cfg, post):
    led = Ledger.load(cfg); led.add_post(post); return led


# ---- config: zernio is a recognized backend; default base URL; live needs the key ----
def test_poster_backend_accepts_zernio(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    assert Config(root=tmp_path).poster_backend == "zernio"

def test_zernio_url_defaults_and_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("ZERNIO_API_URL", raising=False)
    assert Config(root=tmp_path).zernio_url == "https://zernio.com/api/v1"
    monkeypatch.setenv("ZERNIO_API_URL", "https://eu.zernio.com/api/v1/")
    assert Config(root=tmp_path).zernio_url == "https://eu.zernio.com/api/v1/"

def test_is_live_backend_true_only_with_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    assert Config(root=tmp_path).is_live_backend is False
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_x")
    assert Config(root=tmp_path).is_live_backend is True

def test_zernio_auth_error_is_fatal_autherror():
    assert issubclass(ZernioAuthError, AuthError)


# ---- payload shape (offline lock) ----
def test_payload_shape():
    p = build_zernio_payload(account_id="acc_abc", platform="tiktok", content="fire",
                             media_urls=["https://media.zernio.com/x.mp4"], scheduled_time="2099-01-01T00:00:00Z")
    assert p["content"] == "fire" and p["publishNow"] is True
    plat = p["platforms"][0]
    assert plat["platform"] == "tiktok" and plat["accountId"] == "acc_abc"
    assert p["mediaItems"] == [{"type": "video", "url": "https://media.zernio.com/x.mp4"}]
    assert plat["platformSpecificData"]["tiktokSettings"]["content_preview_confirmed"] is True

def test_payload_omits_media_when_none():
    p = build_zernio_payload(account_id="acc_abc", platform="tiktok", content="c", media_urls=[], scheduled_time=None)
    assert "mediaItems" not in p and p["platforms"][0]["accountId"] == "acc_abc"


# ---- factory wiring ----
def test_get_poster_returns_zernio(tmp_path, monkeypatch):
    assert isinstance(get_poster(_cfg(tmp_path, monkeypatch)), ZernioPoster)


# ---- publish state machine (mirrors Postiz safety) ----
def test_publish_submitted_on_2xx_with_id(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(201, {"_id": "z_1"}))
    led = ZernioPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.submitted and led.posts["p1"].submission_id == "z_1"

def test_publish_accepts_nested_post_id(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(200, {"post": {"_id": "z_9"}}))
    assert ZernioPoster(cfg).publish(led, "p1").posts["p1"].submission_id == "z_9"

def test_publish_401_is_typed_auth_redacted(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.requests.post",
                 return_value=_R(401, {"e": "denied SENTINEL"}, text="denied SENTINEL"))
    with pytest.raises(ZernioAuthError) as ei:
        ZernioPoster(cfg).publish(led, "p1")
    assert "SENTINEL" not in str(ei.value)

def test_publish_5xx_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(503, {}, text="boom"))
    led = ZernioPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.needs_reconcile

def test_publish_5xx_error_reason_withholds_body(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(500, {}, text="SENTINEL-BODY"))
    er = ZernioPoster(cfg).publish(led, "p1").posts["p1"].error_reason or ""
    assert "SENTINEL-BODY" not in er and "500" in er

def test_publish_2xx_no_id_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(200, {"ok": True}))
    assert ZernioPoster(cfg).publish(led, "p1").posts["p1"].state is PostState.needs_reconcile

def test_publish_other_4xx_fails(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(422, {}, text="bad"))
    assert ZernioPoster(cfg).publish(led, "p1").posts["p1"].state is PostState.failed

def test_publish_network_error_parks_needs_reconcile(tmp_path, monkeypatch, mocker):
    import requests as _rq
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.requests.post", side_effect=_rq.exceptions.ConnectionError("dropped"))
    assert ZernioPoster(cfg).publish(led, "p1").posts["p1"].state is PostState.needs_reconcile

def test_publish_429_then_success(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); led = _led(cfg, _post())
    mocker.patch("fanops.post.zernio.time.sleep")
    mocker.patch("fanops.post.zernio.requests.post", side_effect=[_R(429, {}, text="rate"), _R(201, {"_id": "z_2"})])
    led = ZernioPoster(cfg).publish(led, "p1")
    assert led.posts["p1"].state is PostState.submitted and led.posts["p1"].submission_id == "z_2"


# ---- construction guard ----
def test_missing_key_raises_typed_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio"); monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    with pytest.raises(ZernioAuthError):
        ZernioPoster(Config(root=tmp_path))


# ---- list accounts (Go-Live picklist): GET /accounts -> {accounts:[{_id, platform}]} ----
def test_list_accounts_parses_id_platform(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.zernio.requests.get",
                 return_value=_R(200, {"accounts": [{"_id": "acc_abc", "platform": "tiktok", "name": "fan1"}]}))
    out = zernio_list_accounts(cfg)
    assert out == [ZernioAccount("acc_abc", "fan1", "tiktok")]

def test_list_accounts_accepts_bare_list_and_skips_malformed(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.zernio.requests.get",
                 return_value=_R(200, [{"name": "no id"}, "garbage", {"_id": "ok", "platform": "tiktok"}]))
    assert zernio_list_accounts(cfg) == [ZernioAccount("ok", "tiktok", "tiktok")]

def test_list_accounts_401_typed_redacted(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.zernio.requests.get", return_value=_R(401, {}, text="denied SENTINEL"))
    with pytest.raises(ZernioAuthError) as ei:
        zernio_list_accounts(cfg)
    assert "SENTINEL" not in str(ei.value)


# ---- cheap auth probe (Go-Live "Save & test") ----
def test_check_auth_true_on_2xx(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.zernio.requests.get", return_value=_R(200, {"accounts": []}))
    assert zernio_check_auth(cfg) is True

def test_check_auth_raises_on_401(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.zernio.requests.get", return_value=_R(401, {}, text="x"))
    with pytest.raises(ZernioAuthError):
        zernio_check_auth(cfg)

def test_check_auth_false_on_other_failure(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch)
    mocker.patch("fanops.post.zernio.requests.get", return_value=_R(500, {}, text="boom"))
    assert zernio_check_auth(cfg) is False
