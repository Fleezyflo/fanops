# tests/test_zernio_media.py — Zernio slice 3: TikTok video media upload. zernio_upload_media uploads a
# local file and returns the hosted URL to reference in posts.create. Contract DISCOVERED LIVE 2026-06-29
# is TWO-step (the single-step /media/upload with Bearer alone returned 400 "Upload token is required"):
#   1) POST /media/upload-token  {"accountId": <id>}        -> {"token": <single-use, ~60s, per-account>}
#   2) POST /media/upload?token=<token>  multipart field `files` (plural) -> {"success": true,
#      "files": [{"url": <hosted>}]}
# These endpoint paths + field names + response keys are INTEGRATION CHECKPOINTS, locked here by SHAPE so
# the two-step flow can't silently regress to the 400-ing single-step. Safety mirrors postiz: 401 -> typed
# ZernioAuthError; other non-2xx -> RuntimeError with the response BODY WITHHELD (a misconfigured proxy can
# echo the Bearer header into an error page) — withholding is enforced at BOTH steps.
import pytest
from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.post.zernio import zernio_upload_media
from fanops.post import get_media_uploader

_ACC = "acc_tiktok_1"
_TOKEN = "tok_singleuse_123"


class _R:
    def __init__(s, code, body=None, text=""):
        s.status_code = code; s._b = body if body is not None else {}; s.text = text
    def json(s): return s._b


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("ZERNIO_API_URL", raising=False)
    return Config(root=tmp_path)


def _mp4(tmp_path):
    f = tmp_path / "v.mp4"; f.write_bytes(b"VIDEO"); return f


def _two_step(mocker, *, token_resp, upload_resp):
    """Patch requests.post with a 2-call side_effect: [token-mint, byte-upload]. Returns the mock so a
    test can inspect call_args_list to PIN the contract shape."""
    return mocker.patch("fanops.post.zernio.requests.post", side_effect=[token_resp, upload_resp])


def test_upload_two_step_returns_url_and_pins_contract(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    m = _two_step(mocker,
                  token_resp=_R(200, {"token": _TOKEN}),
                  upload_resp=_R(201, {"success": True, "files": [{"url": "https://media.zernio.com/v.mp4"}]}))
    assert zernio_upload_media(cfg, f, account_id=_ACC) == "https://media.zernio.com/v.mp4"
    # PIN step 1 — token mint: /media/upload-token with JSON {"accountId": <id>}
    c1 = m.call_args_list[0]
    assert c1.args[0].endswith("/media/upload-token")
    assert c1.kwargs["json"] == {"accountId": _ACC}
    # PIN step 2 — byte upload: /media/upload, token in params, multipart field name `files` (plural)
    c2 = m.call_args_list[1]
    assert c2.args[0].endswith("/media/upload")
    assert c2.kwargs["params"] == {"token": _TOKEN}
    assert "files" in c2.kwargs["files"] and "file" not in c2.kwargs["files"]


def test_upload_accepts_back_compat_url_shape(tmp_path, monkeypatch, mocker):
    # Step-2 body using the OLD top-level/nested url shape still resolves via _extract_zernio_media_url.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _two_step(mocker,
              token_resp=_R(200, {"token": _TOKEN}),
              upload_resp=_R(200, {"media": {"url": "https://media.zernio.com/n.mp4"}}))
    assert zernio_upload_media(cfg, f, account_id=_ACC) == "https://media.zernio.com/n.mp4"


def test_upload_requires_account_id(tmp_path, monkeypatch, mocker):
    # New contract: the per-account token mint needs the id; omitting it raises BEFORE any network call.
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    spy = mocker.patch("fanops.post.zernio.requests.post")
    with pytest.raises(RuntimeError, match="account_id"):
        zernio_upload_media(cfg, f)
    spy.assert_not_called()


def test_token_mint_401_typed(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(401, {}, text="nope"))
    with pytest.raises(ZernioAuthError):
        zernio_upload_media(cfg, f, account_id=_ACC)


def test_upload_step_401_typed(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _two_step(mocker, token_resp=_R(200, {"token": _TOKEN}), upload_resp=_R(401, {}, text="nope"))
    with pytest.raises(ZernioAuthError):
        zernio_upload_media(cfg, f, account_id=_ACC)


def test_token_mint_error_withholds_body(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(500, {}, text="upstream SENTINEL-BODY"))
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert "SENTINEL-BODY" not in str(ei.value) and "500" in str(ei.value)


def test_upload_step_error_withholds_body(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _two_step(mocker, token_resp=_R(200, {"token": _TOKEN}),
              upload_resp=_R(500, {}, text="upstream SENTINEL-BODY"))
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f, account_id=_ACC)
    assert "SENTINEL-BODY" not in str(ei.value) and "500" in str(ei.value)


def test_token_mint_2xx_without_token_raises(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(200, {"ok": True}))
    with pytest.raises(RuntimeError, match="token"):
        zernio_upload_media(cfg, f, account_id=_ACC)


def test_upload_2xx_without_url_raises(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = _mp4(tmp_path)
    _two_step(mocker, token_resp=_R(200, {"token": _TOKEN}), upload_resp=_R(200, {"success": True, "files": []}))
    with pytest.raises(RuntimeError):
        zernio_upload_media(cfg, f, account_id=_ACC)


def test_media_uploader_dispatches_to_zernio(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert get_media_uploader(cfg, "zernio") is zernio_upload_media


# ---- Sprint 2: Zernio size preflight (fail before network) ----
def test_zernio_upload_rejects_oversize_before_network(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("FANOPS_ZERNIO_MAX_UPLOAD_MB", "1")
    cfg = _cfg(tmp_path, monkeypatch)
    f = tmp_path / "big.mp4"
    f.write_bytes(b"V" * (2 * 1024 * 1024))
    spy = mocker.patch("fanops.post.zernio.requests.post")
    with pytest.raises(RuntimeError, match="oversize"):
        zernio_upload_media(cfg, f, account_id=_ACC)
    spy.assert_not_called()
