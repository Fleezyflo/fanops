"""R1: media path integrity — resolve stale absolute paths after FANOPS_ROOT move."""
import json
from pathlib import Path
import pytest
pytest.importorskip("flask")
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Source, Moment, Clip, Post, Render, Platform, PostState, ClipState,
                            MomentState, RenderState, Fmt)
from fanops.post.media import resolve_media_path, ensure_clip_media
from fanops.post.compress import media_path_for_post
from fanops.studio.preview_media import preview_media_path
from fanops.studio.app import _media_path_for_post, create_app


def _old_root(tmp_path) -> Path:
    return tmp_path / "old" / "MohFlow-FanOps"


def _fixture_mp4(cfg: Config, name: str = "clip_x.mp4", *, sub: str = "") -> Path:
    d = cfg.clips / sub if sub else cfg.clips
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"X" * 32)
    return p


def _stale_clip_path(cfg: Config, name: str = "clip_x.mp4") -> str:
    old = _old_root(cfg.root)
    return str(old / "03_clips" / name)


def _seed_stale_post(cfg: Config, *, post_id: str = "p0", clip_id: str = "c0", render: bool = False):
    real = _fixture_mp4(cfg)
    stale = _stale_clip_path(cfg, real.name)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path=_stale_clip_path(cfg, "src.mp4"), language="en"))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="0-7", start=0, end=7, reason="r",
                          state=MomentState.clipped, hook="H"))
    led.add_clip(Clip(id=clip_id, parent_id="m1", path=stale, aspect=Fmt.r9x16, state=ClipState.rendered))
    kw = dict(id=post_id, parent_id=clip_id, account="a", account_id="1", platform=Platform.instagram,
              caption="c", state=PostState.awaiting_approval)
    if render:
        rid = "r1"
        led.add_render(Render(id=rid, clip_id=clip_id, account="a", surface_key="a|instagram",
                              hook_text="h", path=stale, state=RenderState.rendered))
        led.add_post(Post(**kw, render_id=rid, media_urls=[f"file://{stale}"]))
    else:
        led.add_post(Post(**kw, media_urls=[f"file://{stale}"]))
    led.save()
    return real, stale


# ---- resolve_media_path truth table ----
def test_resolve_empty_returns_none(tmp_path):
    cfg = Config(root=tmp_path)
    assert resolve_media_path(cfg, "", "clip") is None
    assert resolve_media_path(cfg, None, "clip") is None


def test_resolve_exists_absolute(tmp_path):
    cfg = Config(root=tmp_path)
    p = _fixture_mp4(cfg)
    assert resolve_media_path(cfg, str(p), "clip") == p.resolve()


def test_resolve_basename_clip(tmp_path):
    cfg = Config(root=tmp_path)
    real = _fixture_mp4(cfg)
    stale = _stale_clip_path(cfg, real.name)
    got = resolve_media_path(cfg, stale, "clip")
    assert got == real.resolve()


def test_resolve_basename_source(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.sources.mkdir(parents=True, exist_ok=True)
    real = cfg.sources / "src.mp4"
    real.write_bytes(b"V")
    stale = str(_old_root(cfg.root) / "02_sources" / "src.mp4")
    assert resolve_media_path(cfg, stale, "source") == real.resolve()


def test_resolve_render_rglob_shallowest(tmp_path):
    cfg = Config(root=tmp_path)
    _fixture_mp4(cfg, "render_x.mp4", sub="batch/src")
    shallow = _fixture_mp4(cfg, "render_x.mp4", sub="flat")
    stale = str(_old_root(cfg.root) / "03_clips" / "nested" / "render_x.mp4")
    got = resolve_media_path(cfg, stale, "render")
    assert got == shallow.resolve()


def test_resolve_miss_returns_none(tmp_path):
    cfg = Config(root=tmp_path)
    stale = _stale_clip_path(cfg, "missing.mp4")
    assert resolve_media_path(cfg, stale, "clip") is None


# ---- preview / compress / serve choke points ----
def test_preview_media_path_stale_fixture(tmp_path):
    cfg = Config(root=tmp_path)
    real, _ = _seed_stale_post(cfg)
    path = preview_media_path(cfg, Ledger.load(cfg), "p0")
    assert path and Path(path).resolve() == real.resolve()


def test_media_path_for_post_stale_fixture(tmp_path):
    cfg = Config(root=tmp_path)
    real, _ = _seed_stale_post(cfg, render=True)
    got = media_path_for_post(cfg, Ledger.load(cfg), Ledger.load(cfg).posts["p0"])
    assert got == real.resolve()


def test_media_path_for_post_and_flask_serve_stale(tmp_path):
    cfg = Config(root=tmp_path)
    real, _ = _seed_stale_post(cfg, render=True)
    led = Ledger.load(cfg)
    got = _media_path_for_post(cfg, led, "p0")
    assert got and Path(got).resolve() == real.resolve()
    app = create_app(cfg); app.config.update(TESTING=True)
    r = app.test_client().get("/media/p0")
    assert r.status_code == 200 and r.data


def test_ensure_clip_media_dryrun_stale_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_POSTER", "dryrun")
    cfg = Config(root=tmp_path)
    real, stale = _seed_stale_post(cfg)
    led = Ledger.load(cfg)
    url = ensure_clip_media(led, cfg, "c0")
    assert url.startswith("file://")
    assert Path(url[7:]).resolve() == real.resolve()


# ---- paths-rebase verb ----
def _run_paths_rebase(cfg, *, apply=False):
    import io
    from argparse import Namespace
    from contextlib import redirect_stdout
    from fanops.paths_rebase import cmd_paths_rebase
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_paths_rebase(cfg, Namespace(apply=apply))
    return rc, buf.getvalue()


def test_paths_rebase_dry_run_counts_unchanged(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_stale_post(cfg)
    before = cfg.ledger_path.read_bytes()
    rc, out = _run_paths_rebase(cfg)
    assert rc == 0 and "clips" in out.lower()
    assert cfg.ledger_path.read_bytes() == before


def test_paths_rebase_apply_rewrites_and_snapshots(tmp_path):
    cfg = Config(root=tmp_path)
    real, stale = _seed_stale_post(cfg)
    old_prefix = str(_old_root(cfg.root))
    rc, out = _run_paths_rebase(cfg, apply=True)
    assert rc == 0 and "snapshot" in out.lower()
    led = Ledger.load(cfg)
    assert old_prefix not in led.clips["c0"].path
    assert Path(led.clips["c0"].path).resolve() == real.resolve()
    snaps = list(cfg.control.glob("ledger.snapshot.*.sqlite"))
    assert snaps


def test_paths_rebase_idempotent_second_apply(tmp_path):
    cfg = Config(root=tmp_path)
    _seed_stale_post(cfg)
    assert _run_paths_rebase(cfg, apply=True)[0] == 0
    rc, out = _run_paths_rebase(cfg, apply=True)
    assert rc == 0
    assert "total: 0" in out.lower()


def test_resolver_previews_after_future_move_without_rebase(tmp_path):
    cfg = Config(root=tmp_path)
    real, _ = _seed_stale_post(cfg)
    # simulate move: file lives under cfg.clips but ledger still has stale absolute path
    new_root = tmp_path / "newhome"
    new_cfg = Config(root=new_root)
    dest = new_cfg.clips / real.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(real.read_bytes())
    path = preview_media_path(new_cfg, Ledger.load(cfg), "p0")
    assert path and Path(path).resolve() == dest.resolve()


def test_paths_rebase_manifest(tmp_path):
    cfg = Config(root=tmp_path)
    real = _fixture_mp4(cfg)
    stale = _stale_clip_path(cfg, real.name)
    mp = cfg.agent_io / "manifests" / "s1.json"
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"v": 1, "source_id": "s1", "stages": {"clip": {"artifact": stale, "schema": 1}}}))
    rc, _ = _run_paths_rebase(cfg, apply=True)
    assert rc == 0
    d = json.loads(mp.read_text())
    assert Path(d["stages"]["clip"]["artifact"]).resolve() == real.resolve()
