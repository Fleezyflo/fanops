# tests/test_zernio_media.py — Zernio slice 3: TikTok video media upload. zernio_upload_media uploads a
# local file (multipart) and returns the hosted URL to reference in posts.create. Mirrors
# postiz_upload_media's safety (401 -> typed ZernioAuthError; other non-2xx -> RuntimeError with the
# response BODY WITHHELD — a misconfigured proxy can echo the Bearer header). The media endpoint path +
# response URL key are INTEGRATION CHECKPOINTS, locked here by SHAPE; the operator verifies live.
import pytest
from fanops.config import Config
from fanops.errors import ZernioAuthError
from fanops.post.zernio import zernio_upload_media
from fanops.post import get_media_uploader


class _R:
    def __init__(s, code, body=None, text=""):
        s.status_code = code; s._b = body if body is not None else {}; s.text = text
    def json(s): return s._b


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "zernio")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.delenv("ZERNIO_API_URL", raising=False)
    return Config(root=tmp_path)


def test_upload_returns_url_top_level(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.zernio.requests.post",
                 return_value=_R(201, {"url": "https://media.zernio.com/v.mp4"}))
    assert zernio_upload_media(cfg, f) == "https://media.zernio.com/v.mp4"

def test_upload_accepts_nested_media_url(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.zernio.requests.post",
                 return_value=_R(200, {"media": {"url": "https://media.zernio.com/n.mp4"}}))
    assert zernio_upload_media(cfg, f) == "https://media.zernio.com/n.mp4"

def test_upload_401_typed(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(401, {}, text="x"))
    with pytest.raises(ZernioAuthError):
        zernio_upload_media(cfg, f)

def test_upload_error_withholds_response_body(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(500, {}, text="upstream SENTINEL-BODY"))
    with pytest.raises(RuntimeError) as ei:
        zernio_upload_media(cfg, f)
    assert "SENTINEL-BODY" not in str(ei.value) and "500" in str(ei.value)

def test_upload_missing_url_in_2xx_raises(tmp_path, monkeypatch, mocker):
    cfg = _cfg(tmp_path, monkeypatch); f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.zernio.requests.post", return_value=_R(200, {"ok": True}))
    with pytest.raises(RuntimeError):
        zernio_upload_media(cfg, f)

def test_media_uploader_dispatches_to_zernio(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert get_media_uploader(cfg, "zernio") is zernio_upload_media
