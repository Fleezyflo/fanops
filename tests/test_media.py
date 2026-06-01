from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState
from fanops.post.media import upload_media, dryrun_media_url, ensure_clip_media


class _Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._b = body or {}
        self.text = str(self._b)
    def json(self):
        return self._b

def test_dryrun_url(tmp_path):
    f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    assert dryrun_media_url(f).startswith("file://") and "v.mp4" in dryrun_media_url(f)

def test_upload_presign_then_put(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k123")
    cfg = Config(root=tmp_path); f = tmp_path / "c.mp4"; f.write_bytes(b"V")
    class _R:
        def __init__(s, c, b=None): s.status_code = c; s._b = b or {}; s.text = str(s._b)
        def json(s): return s._b
    pm = mocker.patch("fanops.post.media.requests.post",
                      return_value=_R(200, {"presignedUrl": "https://up/a", "publicUrl": "https://cdn/c.mp4"}))
    put = mocker.patch("fanops.post.media.requests.put", return_value=_R(200))
    assert upload_media(cfg, f) == "https://cdn/c.mp4"
    assert pm.call_args.kwargs["json"]["filename"] == "c.mp4"
    assert pm.call_args.kwargs["headers"]["blotato-api-key"] == "k123"
    assert put.call_args.args[0] == "https://up/a"

def test_ensure_clip_media_uploads_once(tmp_path, monkeypatch, mocker):
    # FIX F44: two posts off one clip -> ONE upload; second call is cached.
    monkeypatch.setenv("FANOPS_POSTER", "rest"); monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "clip_1.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="clip_1", parent_id="m", path=str(f), state=ClipState.queued))
    up = mocker.patch("fanops.post.media.upload_media", return_value="https://cdn/clip_1.mp4")
    u1 = ensure_clip_media(led, cfg, "clip_1")
    u2 = ensure_clip_media(led, cfg, "clip_1")
    assert u1 == u2 == "https://cdn/clip_1.mp4"
    assert up.call_count == 1                          # uploaded once, then cached on the clip
    assert led.clips["clip_1"].media_url == "https://cdn/clip_1.mp4"

def test_upload_media_missing_key_raises(tmp_path, monkeypatch):
    # AUDIT H8: missing key is a fatal AUTH condition -> typed BlotatoAuthError (halts the queue
    # by type in run.py), not a generic RuntimeError.
    from fanops.errors import BlotatoAuthError
    monkeypatch.delenv("BLOTATO_API_KEY", raising=False)
    cfg = Config(root=tmp_path); f = tmp_path / "c.mp4"; f.write_bytes(b"V")
    import pytest
    with pytest.raises(BlotatoAuthError, match="BLOTATO_API_KEY"):
        upload_media(cfg, f)

def test_upload_media_non_2xx_presign_raises_contextful(tmp_path, monkeypatch, mocker):
    # A non-2xx, non-401 presign surfaces the status code in a RuntimeError (not a bare KeyError).
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); f = tmp_path / "c.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.media.requests.post", return_value=_Resp(403, {"error": "forbidden"}))
    import pytest
    with pytest.raises(RuntimeError, match="403"):
        upload_media(cfg, f)


def test_upload_media_401_presign_raises_typed_auth(tmp_path, monkeypatch, mocker):
    # AUDIT H8: a 401 on the media presign is the SAME fatal auth condition as a 401 on the post
    # -> typed BlotatoAuthError so run.py halts the queue by type, not by a "401" substring.
    from fanops.errors import BlotatoAuthError
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); f = tmp_path / "c.mp4"; f.write_bytes(b"V")
    mocker.patch("fanops.post.media.requests.post", return_value=_Resp(401, {"error": "bad key"}))
    import pytest
    with pytest.raises(BlotatoAuthError, match="401"):
        upload_media(cfg, f)

def test_upload_media_missing_keys_in_presign_raises(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("BLOTATO_API_KEY", "k")
    cfg = Config(root=tmp_path); f = tmp_path / "c.mp4"; f.write_bytes(b"V")
    # 200 but the response lacks presignedUrl/publicUrl (contract drift at the integration checkpoint)
    mocker.patch("fanops.post.media.requests.post", return_value=_Resp(200, {"unexpected": "shape"}))
    import pytest
    with pytest.raises(RuntimeError, match="missing presignedUrl"):
        upload_media(cfg, f)

def test_ensure_clip_media_dryrun_branch_returns_file_url(tmp_path, monkeypatch):
    # The dryrun branch of ensure_clip_media (poster_backend==dryrun) returns a file:// url and caches it.
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    from fanops.models import Clip, ClipState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "clip_d.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="clip_d", parent_id="m", path=str(f), state=ClipState.queued))
    u = ensure_clip_media(led, cfg, "clip_d")
    assert u.startswith("file://") and led.clips["clip_d"].media_url == u
