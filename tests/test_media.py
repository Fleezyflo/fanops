from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState
from fanops.post.media import dryrun_media_url, ensure_clip_media


def test_dryrun_url(tmp_path):
    f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    assert dryrun_media_url(f).startswith("file://") and "v.mp4" in dryrun_media_url(f)

def test_ensure_clip_media_uploads_once(tmp_path, monkeypatch, mocker):
    # FIX F44: two calls off one clip -> ONE Postiz /upload; the URL is cached on the clip and
    # survives Ledger.save/load. Mock only requests.post (the HTTP edge), not get_media_uploader.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "clip_1.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="clip_1", parent_id="m", path=str(f), state=ClipState.queued))
    class _R:
        status_code = 201
        def json(self): return {"id": "img1", "path": "https://uploads.postiz.com/clip_1.mp4"}
    posts = []
    def _post(url, **kw):
        posts.append(url); return _R()
    mocker.patch("fanops.post.postiz.requests.post", side_effect=_post)
    u1 = ensure_clip_media(led, cfg, "clip_1")
    led.save()
    u2 = ensure_clip_media(Ledger.load(cfg), cfg, "clip_1")
    assert u1 == u2 == "img1|https://uploads.postiz.com/clip_1.mp4"
    assert len(posts) == 1
    assert Ledger.load(cfg).clips["clip_1"].media_url == u1

def test_ensure_clip_media_dryrun_branch_returns_file_url(tmp_path, monkeypatch):
    # The dryrun branch of ensure_clip_media (poster_backend==dryrun) returns a file:// url and caches it.
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "clip_d.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="clip_d", parent_id="m", path=str(f), state=ClipState.queued))
    u = ensure_clip_media(led, cfg, "clip_d")
    assert u.startswith("file://") and led.clips["clip_d"].media_url == u
