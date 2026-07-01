from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState
from fanops.post.media import dryrun_media_url, ensure_clip_media


def test_dryrun_url(tmp_path):
    f = tmp_path / "v.mp4"; f.write_bytes(b"V")
    assert dryrun_media_url(f).startswith("file://") and "v.mp4" in dryrun_media_url(f)

def test_ensure_clip_media_uploads_once(tmp_path, monkeypatch, mocker):
    # FIX F44: two posts off one clip -> ONE upload; second call is cached. Backend-dispatched via
    # get_media_uploader (postiz/zernio); patch the uploader factory so the cache-once contract is
    # asserted independent of the concrete backend.
    monkeypatch.setenv("FANOPS_POSTER", "postiz"); monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "clip_1.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="clip_1", parent_id="m", path=str(f), state=ClipState.queued))
    up = mocker.patch("fanops.post.get_media_uploader", return_value=lambda c, p, **_kw: "https://cdn/clip_1.mp4")
    u1 = ensure_clip_media(led, cfg, "clip_1")
    u2 = ensure_clip_media(led, cfg, "clip_1")
    assert u1 == u2 == "https://cdn/clip_1.mp4"
    assert up.call_count == 1                          # uploader resolved once, then the URL is cached on the clip
    assert led.clips["clip_1"].media_url == "https://cdn/clip_1.mp4"

def test_ensure_clip_media_dryrun_branch_returns_file_url(tmp_path, monkeypatch):
    # The dryrun branch of ensure_clip_media (poster_backend==dryrun) returns a file:// url and caches it.
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.clips / "clip_d.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_clip(Clip(id="clip_d", parent_id="m", path=str(f), state=ClipState.queued))
    u = ensure_clip_media(led, cfg, "clip_d")
    assert u.startswith("file://") and led.clips["clip_d"].media_url == u
