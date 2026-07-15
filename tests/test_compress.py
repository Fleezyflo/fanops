"""Oversize shrink helpers — cap gate, path resolution, ledger persist (no live ffmpeg required)."""
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Post, PostState, Platform, Render, RenderState
from fanops.post.compress import (media_path_for_post, upload_cap_bytes, apply_shrink_to_post,
                                  persist_post_shrink, publish_backend_for_post, maybe_shrink_for_cap)
from fanops.accounts import add_account, set_backend


def test_zernio_max_upload_default_4mb(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_ZERNIO_MAX_UPLOAD_MB", raising=False)
    assert Config(root=tmp_path).zernio_max_upload_bytes == 4 * 1024 * 1024


def test_upload_cap_only_zernio_tiktok(tmp_path):
    cfg = Config(root=tmp_path)
    p = Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.tiktok, caption="x")
    assert upload_cap_bytes(cfg, p, "zernio") == cfg.zernio_max_upload_bytes
    assert upload_cap_bytes(cfg, p, "postiz") is None


def test_media_path_for_post_prefers_render(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    fat = tmp_path / "fat.mp4"
    thin = tmp_path / "thin.mp4"
    fat.write_bytes(b"x" * 100)
    thin.write_bytes(b"y")
    led.add_render(Render(id="r1", clip_id="c", account="a", surface_key="a/tiktok",
                          hook_text="h", path=str(thin), state=RenderState.rendered))
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.tiktok,
                      caption="x", render_id="r1", media_urls=[f"file://{fat}"]))
    assert media_path_for_post(cfg, led, led.posts["p"]) == thin


def test_apply_shrink_persists_media_urls_when_mocked(tmp_path, monkeypatch, mocker):
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    led = Ledger.load(cfg)
    src = tmp_path / "big.mp4"
    src.write_bytes(b"Z" * 8_000_000)
    shrunk = tmp_path / "small.mp4"
    shrunk.write_bytes(b"S" * 1000)
    led.add_post(Post(id="p", parent_id="c", account="tt", account_id="z1", platform=Platform.tiktok,
                      caption="x", state=PostState.queued, media_urls=[f"file://{src}"]))
    mocker.patch("fanops.post.compress.maybe_shrink_for_cap", return_value=shrunk)
    monkeypatch.setenv("FANOPS_ZERNIO_MAX_UPLOAD_MB", "4")
    post = led.posts["p"]
    assert apply_shrink_to_post(cfg, led, post, backend="zernio") is True
    assert post.media_urls == [f"file://{shrunk.resolve()}"]


def test_noop_shrink_persist_leaves_https_urls(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    src = tmp_path / "a.mp4"
    src.write_bytes(b"1")
    led.add_render(Render(id="r1", clip_id="c", account="a", surface_key="a/tiktok",
                          hook_text="h", path=str(src), state=RenderState.rendered))
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.tiktok,
                      caption="x", render_id="r1", media_urls=[f"file://{src}"]))
    led.save()
    with Ledger.transaction(cfg) as led:
        p = led.posts["p"]
        led.posts["p"] = p.model_copy(update={"media_urls": ["https://media.zernio.com/v.mp4"]})
    snap = Ledger.load(cfg)
    snap.posts["p"] = snap.posts["p"].model_copy(update={"media_urls": [f"file://{src}"]})
    persist_post_shrink(cfg, snap, "p")
    led2 = Ledger.load(cfg)
    assert led2.posts["p"].media_urls == ["https://media.zernio.com/v.mp4"]


def test_real_shrink_persist_writes_shrunk_render_and_file_urls(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    src = tmp_path / "a.mp4"
    shrunk = tmp_path / "b.mp4"
    src.write_bytes(b"1")
    shrunk.write_bytes(b"2")
    led.add_render(Render(id="r1", clip_id="c", account="a", surface_key="a/tiktok",
                          hook_text="h", path=str(src), state=RenderState.rendered))
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.tiktok,
                      caption="x", render_id="r1", media_urls=[f"file://{src}"]))
    led.save()
    snap = Ledger.load(cfg)
    snap.renders["r1"] = snap.renders["r1"].model_copy(update={"path": str(shrunk)})
    snap.posts["p"].media_urls = [f"file://{shrunk}"]
    persist_post_shrink(cfg, snap, "p")
    led2 = Ledger.load(cfg)
    assert led2.renders["r1"].path == str(shrunk)
    assert led2.posts["p"].media_urls == [f"file://{shrunk}"]


def test_persist_post_shrink_writes_ledger(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    src = tmp_path / "a.mp4"
    dst = tmp_path / "b.mp4"
    src.write_bytes(b"1")
    dst.write_bytes(b"2")
    led.add_render(Render(id="r1", clip_id="c", account="a", surface_key="a/tiktok",
                          hook_text="h", path=str(src), state=RenderState.rendered))
    led.add_post(Post(id="p", parent_id="c", account="a", account_id="1", platform=Platform.tiktok,
                      caption="x", render_id="r1", media_urls=[f"file://{dst}"]))
    led.save()
    snap = Ledger.load(cfg)
    snap.renders["r1"] = snap.renders["r1"].model_copy(update={"path": str(dst)})
    snap.posts["p"].media_urls = [f"file://{dst}"]
    persist_post_shrink(cfg, snap, "p")
    led2 = Ledger.load(cfg)
    assert led2.posts["p"].media_urls == [f"file://{dst}"]
    assert led2.renders["r1"].path == str(dst)


def test_publish_backend_for_post_uses_channel_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    p = Post(id="p", parent_id="c", account="tt", account_id="z1", platform=Platform.tiktok, caption="x")
    assert publish_backend_for_post(cfg, p) == "zernio"


def test_publish_due_persists_shrunk_render_path(tmp_path, monkeypatch, mocker):
    from fanops.post.run import publish_due
    from fanops.models import Clip, ClipState
    monkeypatch.setenv("FANOPS_LIVE", "1")
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_test")
    monkeypatch.setenv("FANOPS_ZERNIO_MAX_UPLOAD_MB", "4")
    cfg = Config(root=tmp_path)
    add_account(cfg, "@tt", [Platform.tiktok], status="active")
    set_backend(cfg, "@tt", "tiktok", "zernio")
    src = tmp_path / "big.mp4"
    shrunk = tmp_path / "small.mp4"
    src.write_bytes(b"Z" * 8_000_000)
    shrunk.write_bytes(b"S" * 1000)
    led = Ledger.load(cfg)
    led.add_clip(Clip(id="c", parent_id="m", path=str(src), state=ClipState.queued))
    led.add_render(Render(id="r1", clip_id="c", account="tt", surface_key="tt/tiktok",
                          hook_text="h", path=str(src), state=RenderState.rendered))
    led.add_post(Post(id="p", parent_id="c", account="tt", account_id="z1", platform=Platform.tiktok,
                      caption="x", state=PostState.queued, render_id="r1",
                      scheduled_time="2020-01-01T00:00:00Z", media_urls=[f"file://{src}"],
                      public_url="dryrun://p"))
    led.save()

    def shrink(cfg_, path, cap, **kw):
        return shrunk
    mocker.patch("fanops.post.compress.maybe_shrink_for_cap", side_effect=shrink)
    mocker.patch("fanops.postiz_lifecycle.ensure_up")
    mocker.patch("fanops.post.media.ensure_render_media", return_value="https://media.zernio.com/v.mp4")

    class FakePoster:
        def publish(self, led_, pid):
            led_.posts[pid].state = PostState.published
            led_.posts[pid].public_url = "https://www.tiktok.com/@x/1"
            return led_
    mocker.patch("fanops.post.run.get_poster", return_value=FakePoster())
    publish_due(cfg, now="2026-06-02T18:00:00Z")
    led2 = Ledger.load(cfg)
    assert led2.renders["r1"].path == str(shrunk)  # shrink persisted to ledger
    assert led2.posts["p"].media_urls == ["https://media.zernio.com/v.mp4"]  # upload replaced file://


# ── RC-10 (S09): maybe_shrink_for_cap's per-call scratch dir must never outlive the call ──────────
# The helper mkdtemp'd a `fanops-shrink-*` dir under 04_agent_io on every over-cap attempt and NEVER
# removed it. These mock ffmpeg (no live encoder) and assert the scratch is gone on every exit path.

def _shrink_dirs(cfg):
    root = cfg.base / "04_agent_io"
    return sorted(root.glob("fanops-shrink-*")) if root.exists() else []


def test_shrink_failure_removes_scratch_dir(tmp_path, mocker):
    # FAILS BEFORE / PASSES AFTER: ffmpeg can't get under cap -> fail-open to the original path AND the
    # per-call scratch dir is removed (it used to leak forever).
    cfg = Config(root=tmp_path)
    src = tmp_path / "big.mp4"; src.write_bytes(b"X" * 5000)
    mocker.patch("fanops.post.compress.subprocess.run", return_value=mocker.Mock(returncode=1))
    out = maybe_shrink_for_cap(cfg, src, cap=1000, label="t")
    assert out == src                                            # fail-open contract preserved
    assert _shrink_dirs(cfg) == []                              # RC-10: no leaked scratch dir


def test_shrink_exception_removes_scratch_dir(tmp_path, mocker):
    # fail-open on a raised encoder AND the scratch is still removed (finally, not a happy-path cleanup).
    cfg = Config(root=tmp_path)
    src = tmp_path / "big.mp4"; src.write_bytes(b"X" * 5000)
    mocker.patch("fanops.post.compress.subprocess.run", side_effect=OSError("boom"))
    out = maybe_shrink_for_cap(cfg, src, cap=1000, label="t")
    assert out == src
    assert _shrink_dirs(cfg) == []


def test_shrink_success_promotes_winner_and_removes_scratch(tmp_path, mocker):
    # On success the returned file lives OUTSIDE any per-call scratch (a deterministic 04_agent_io path)
    # and no fanops-shrink-* dir remains.
    cfg = Config(root=tmp_path)
    src = tmp_path / "big.mp4"; src.write_bytes(b"X" * 5000)
    def _ffmpeg(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"s" * 100)                   # the encoder writes a small file under cap
        return mocker.Mock(returncode=0)
    mocker.patch("fanops.post.compress.subprocess.run", side_effect=_ffmpeg)
    out = maybe_shrink_for_cap(cfg, src, cap=1000, label="t")
    assert out.exists() and out.stat().st_size <= 1000          # a real shrunk file, under cap
    assert "fanops-shrink-" not in str(out)                     # promoted OUT of the per-call scratch
    assert out.parent == (cfg.base / "04_agent_io")             # deterministic home
    assert _shrink_dirs(cfg) == []                              # RC-10: scratch removed


def test_shrink_success_deterministic_no_proliferation(tmp_path, mocker):
    # Re-shrinking the SAME source returns the SAME path (overwrite) — the leak cannot re-accumulate.
    cfg = Config(root=tmp_path)
    src = tmp_path / "big.mp4"; src.write_bytes(b"X" * 5000)
    def _ffmpeg(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"s" * 100)
        return mocker.Mock(returncode=0)
    mocker.patch("fanops.post.compress.subprocess.run", side_effect=_ffmpeg)
    a = maybe_shrink_for_cap(cfg, src, cap=1000, label="t")
    b = maybe_shrink_for_cap(cfg, src, cap=1000, label="t")
    assert a == b                                               # deterministic dest — no new dir on retry
    assert _shrink_dirs(cfg) == []
