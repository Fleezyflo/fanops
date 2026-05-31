from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState
from fanops.post.media import upload_media, dryrun_media_url, ensure_clip_media

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
