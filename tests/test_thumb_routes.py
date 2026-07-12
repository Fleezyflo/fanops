# U2: /thumb/source + /thumb/clip routes (cache, alias parity, GIF fallback, guards).
import os

import fanops.studio.thumb_media as thumb_mod
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, Moment, Clip, ClipState, MomentState, Fmt
from fanops.studio.app import create_app
from fanops.studio.thumb_media import _TRANSPARENT_GIF


def _seed_clip(cfg):
    cdir = cfg.clips
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "clip_1.mp4").write_bytes(b"V")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/s.mp4", language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped))
    led.add_clip(Clip(id="clip_1", parent_id="m1", path=str(cdir / "clip_1.mp4"), aspect=Fmt.r9x16,
                      state=ClipState.queued))
    led.save()
    return cfg


def _seed_source(cfg, *, duration=30.0):
    inbox = cfg.inbox
    inbox.mkdir(parents=True, exist_ok=True)
    vid = inbox / "src.mp4"
    vid.write_bytes(b"VIDEO")
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=str(vid), language="en"))
    led.save()
    return cfg, duration


def _client(cfg):
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app.test_client()


def test_source_thumb_caches_once(tmp_path, monkeypatch):
    cfg, dur = _seed_source(Config(root=tmp_path))
    calls = []

    def fake_thumb(path, out_jpg, *, at_seconds=1.0):
        calls.append(at_seconds)
        out_jpg.write_bytes(b"\xff\xd8\xffSRC")
        return True

    monkeypatch.setattr(thumb_mod, "make_thumbnail", fake_thumb)
    monkeypatch.setattr(thumb_mod, "probe_dimensions", lambda _p: (320, 180, dur))
    c = _client(cfg)
    r1 = c.get("/thumb/source/s1")
    r2 = c.get("/thumb/source/s1")
    assert r1.status_code == 200 and r1.mimetype == "image/jpeg"
    assert r2.data == r1.data
    assert calls == [max(0.5, dur * 0.1)]
    cache = cfg.agent_io / "thumbs" / "s1.jpg"
    assert cache.exists()


def test_source_thumb_bogus_id_returns_gif(tmp_path, caplog):
    cfg = Config(root=tmp_path)
    r = _client(cfg).get("/thumb/source/nope")
    assert r.status_code == 200
    assert r.data == _TRANSPARENT_GIF
    assert r.mimetype == "image/gif"
    assert "public" in r.headers.get("Cache-Control", "")


def test_source_thumb_traversal_guard(tmp_path):
    cfg = Config(root=tmp_path)
    r = _client(cfg).get("/thumb/source/..")
    assert r.status_code == 200
    assert r.data == _TRANSPARENT_GIF


def test_clip_routes_alias_parity(tmp_path, monkeypatch):
    cfg = _seed_clip(Config(root=tmp_path))

    def fake_thumb(path, out_jpg, *, at_seconds=0.5):
        out_jpg.write_bytes(b"\xff\xd8\xffCLIP")
        return True

    monkeypatch.setattr(thumb_mod, "make_thumbnail", fake_thumb)
    c = _client(cfg)
    a = c.get("/thumb/clip/clip_1")
    b = c.get("/clip-thumb/clip_1")
    assert a.status_code == 200 and b.status_code == 200
    assert a.data == b.data


def test_clip_thumb_stale_mtime_reextracts(tmp_path, monkeypatch):
    cfg = _seed_clip(Config(root=tmp_path))
    cache = cfg.clips / "clip_1.jpg"
    cache.write_bytes(b"\xff\xd8\xffOLDPOSTER")
    base = cache.stat().st_mtime
    os.utime(cfg.clips / "clip_1.mp4", (base + 10, base + 10))
    calls = []

    def fake_thumb(path, out_jpg, *, at_seconds=0.5):
        calls.append(1)
        out_jpg.write_bytes(b"\xff\xd8\xffNEWPOSTER")
        return True

    monkeypatch.setattr(thumb_mod, "make_thumbnail", fake_thumb)
    r = _client(cfg).get("/thumb/clip/clip_1")
    assert r.status_code == 200 and calls == [1]


def test_clip_unknown_returns_gif_not_500(tmp_path):
    cfg = _seed_clip(Config(root=tmp_path))
    r = _client(cfg).get("/thumb/clip/nope")
    assert r.status_code == 200
    assert r.data == _TRANSPARENT_GIF


def test_never_500_on_source_thumb(tmp_path, monkeypatch):
    cfg, _ = _seed_source(Config(root=tmp_path))

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(thumb_mod, "make_thumbnail", boom)
    monkeypatch.setattr(thumb_mod, "probe_dimensions", lambda _p: (320, 180, 10.0))
    r = _client(cfg).get("/thumb/source/s1")
    assert r.status_code == 200
    assert r.data == _TRANSPARENT_GIF
