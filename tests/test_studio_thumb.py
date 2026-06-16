# tests/test_studio_thumb.py — the /clip-thumb/<clip_id> poster route (the black-box-grid fix): a
# cached JPEG first-frame so <video preload="none"> shows a real frame instead of a black box.
# Mirrors /clips/<clip_id> + _bounded path-safety; reuses discover.make_thumbnail; FAIL-OPEN (404,
# never 500) when ffmpeg is missing/fails. The frame extraction engine is covered by discover tests;
# here we prove the route's resolve/cache/guard wiring.
import fanops.studio.app as app_mod
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, ClipState, MomentState, Fmt
from fanops.studio.app import create_app


def _seed_clip(cfg):
    cdir = cfg.clips; cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "clip_1.mp4").write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path=str(cdir / "clip_1.mp4"), aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.save(); return cfg


def _client(cfg):
    app = create_app(cfg); app.config.update(TESTING=True)
    return app.test_client()


def test_clip_thumb_serves_jpeg_and_caches(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed_clip(cfg)
    cache = cfg.clips / "clip_1.jpg"

    def fake_thumb(path, out_jpg, *, at_seconds=0.5):
        out_jpg.write_bytes(b"\xff\xd8\xff\xe0JPEGBYTES")     # a non-empty "jpeg"
        return True
    monkeypatch.setattr(app_mod, "make_thumbnail", fake_thumb)

    r = _client(cfg).get("/clip-thumb/clip_1")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert len(r.data) > 0
    assert cache.exists()                                      # generated once, cached next to the clip


def test_clip_thumb_uses_cache_without_reextracting(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path); _seed_clip(cfg)
    (cfg.clips / "clip_1.jpg").write_bytes(b"\xff\xd8\xffCACHED")  # pre-existing cache
    calls = []
    monkeypatch.setattr(app_mod, "make_thumbnail", lambda *a, **k: calls.append(1) or True)
    r = _client(cfg).get("/clip-thumb/clip_1")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert calls == []                                        # never re-extracted when the cache is warm


def test_clip_thumb_unknown_clip_404(tmp_path):
    cfg = Config(root=tmp_path); _seed_clip(cfg)
    assert _client(cfg).get("/clip-thumb/nope").status_code == 404


def test_clip_thumb_traversal_rejected(tmp_path):
    cfg = Config(root=tmp_path); _seed_clip(cfg)
    assert _client(cfg).get("/clip-thumb/..%2f..%2fetc%2fpasswd").status_code == 404


def test_clip_thumb_fail_open_when_ffmpeg_absent(tmp_path, monkeypatch):
    # ffmpeg missing/failing -> make_thumbnail returns False, no file written -> 404, NEVER 500.
    cfg = Config(root=tmp_path); _seed_clip(cfg)
    monkeypatch.setattr(app_mod, "make_thumbnail", lambda *a, **k: False)
    r = _client(cfg).get("/clip-thumb/clip_1")
    assert r.status_code == 404


def test_clip_thumb_zero_byte_cache_is_reextracted(tmp_path, monkeypatch):
    # a partial/0-byte cache (a timed-out previous extraction) must NOT be served as a valid jpeg —
    # it's treated as a miss and re-extracted; if the re-extract still yields nothing, 404 (fail-open).
    cfg = Config(root=tmp_path); _seed_clip(cfg)
    (cfg.clips / "clip_1.jpg").write_bytes(b"")               # 0-byte partial
    calls = []

    def fake_thumb(path, out_jpg, *, at_seconds=0.5):
        calls.append(1); out_jpg.write_bytes(b"\xff\xd8\xffREAL"); return True
    monkeypatch.setattr(app_mod, "make_thumbnail", fake_thumb)
    r = _client(cfg).get("/clip-thumb/clip_1")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and len(r.data) > 0
    assert calls == [1]                                       # the empty cache forced a re-extract


def test_clip_thumb_reextracted_when_clip_is_newer_than_cache(tmp_path, monkeypatch):
    # The staleness bug behind "the UI shows the old hooks": a RE-RENDERED clip keeps the same
    # clip_id, so the cached poster .jpg (with the OLD burned hook) was served forever. The cache
    # must be treated as stale and regenerated when the mp4 is newer than the cached jpg.
    import os
    cfg = Config(root=tmp_path); _seed_clip(cfg)
    cache = cfg.clips / "clip_1.jpg"
    cache.write_bytes(b"\xff\xd8\xffOLDPOSTER")                # a warm cache from a prior render
    base = cache.stat().st_mtime
    os.utime(cfg.clips / "clip_1.mp4", (base + 10, base + 10))  # re-render bumps the mp4 mtime
    calls = []
    def fake_thumb(path, out_jpg, *, at_seconds=0.5):
        calls.append(1); out_jpg.write_bytes(b"\xff\xd8\xffNEWPOSTER"); return True
    monkeypatch.setattr(app_mod, "make_thumbnail", fake_thumb)
    r = _client(cfg).get("/clip-thumb/clip_1")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and len(r.data) > 0
    assert calls == [1]                                        # mp4 newer than cache -> regenerated


def test_clip_thumb_missing_clip_file_404(tmp_path, monkeypatch):
    # ledger has the clip but the underlying mp4 is gone -> 404 (no extraction attempt).
    cfg = Config(root=tmp_path); _seed_clip(cfg)
    (cfg.clips / "clip_1.mp4").unlink()
    called = []
    monkeypatch.setattr(app_mod, "make_thumbnail", lambda *a, **k: called.append(1) or True)
    assert _client(cfg).get("/clip-thumb/clip_1").status_code == 404
    assert called == []
