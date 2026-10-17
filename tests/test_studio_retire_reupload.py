# tests/test_studio_retire_reupload.py — Studio retire confirm honesty + retired-sha dedup feedback (2026-07-10 incident)
import subprocess

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Moment, Source, SourceState
from fanops.ingest import ingest_drops, sha256_of
from fanops.studio import views
from fanops.studio.actions_run import run_ingest


def _client(cfg):
    from fanops.studio.app import create_app
    return create_app(cfg).test_client()


def _stub_ffprobe(monkeypatch, dims=(1920, 1080, 5.0)):
    """Unit CI has no ffmpeg. Answer ffprobe at subprocess.run (not a fanops.* patch)."""
    real = subprocess.run
    w, h, dur = dims
    def fake(cmd, **kw):
        if cmd and cmd[0] == "ffprobe":
            joined = " ".join(str(x) for x in cmd)
            if "codec_type" in joined:
                return subprocess.CompletedProcess(list(cmd), 0, stdout="video\n", stderr="")
            return subprocess.CompletedProcess(list(cmd), 0, stdout=f"{w}\n{h}\n{dur}\n", stderr="")
        return real(cmd, **kw)
    monkeypatch.setattr(subprocess, "run", fake)


def test_preview_retire_cascade_counts_deletions(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_x", source_path="/x.mp4", sha256="d"))
    for i in range(3):
        led.add_moment(Moment(id=f"m{i}", parent_id="src_x", content_token=f"t{i}", start=0, end=2, reason="a"))
        led.add_clip(Clip(id=f"c{i}", parent_id=f"m{i}", path=f"/c{i}.mp4", state=ClipState.rendered))
    prev = led.preview_retire_cascade("src_x")
    assert prev["delete_moments"] == 3 and prev["delete_clips"] == 3 and prev["retire_moments"] == 0


def test_asset_catalog_includes_retire_preview(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_x", source_path="/show.mp4", origin_kind="native", sha256="d"))
        led.add_moment(Moment(id="m0", parent_id="src_x", content_token="t", start=0, end=2, reason="a"))
        led.add_clip(Clip(id="c0", parent_id="m0", path="/c0.mp4", state=ClipState.rendered))
    row = next(r for r in views.asset_catalog(cfg)["native"] if r["id"] == "src_x")
    assert row["retire_preview"]["delete_moments"] == 1 and row["retire_preview"]["delete_clips"] == 1


def test_library_retire_confirm_mentions_deletions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_x", source_path="/clip.mp4", origin_kind="native", sha256="d"))
        led.add_moment(Moment(id="m0", parent_id="src_x", content_token="t", start=0, end=2, reason="a"))
        led.add_clip(Clip(id="c0", parent_id="m0", path="/c0.mp4", state=ClipState.rendered))
    html = _client(cfg).get("/library").data.decode()
    assert "Deletes 1 unshipped moment" in html and "1 clip" in html


def test_ingest_retired_sha_dedup_surfaces_match(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    vid = cfg.inbox / "again.mp4"; vid.write_bytes(b"SAME_BYTES")
    digest = sha256_of(vid)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_old", source_path=str(cfg.sources / "src_old.mp4"),
                              state=SourceState.retired, sha256=digest))
    _stub_ffprobe(monkeypatch, dims=(1920, 1080, 10.0))
    led, counts = ingest_drops(Ledger.load(cfg), cfg)
    assert counts.retired_dedup == ["src_old"]
    assert counts.added == 0 and counts.deduped == 1


def test_run_ingest_detail_includes_retired_dedup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    vid = cfg.inbox / "dup.mp4"; vid.write_bytes(b"REUPLOAD")
    digest = sha256_of(vid)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_ret", source_path="/r.mp4", state=SourceState.retired, sha256=digest))
    _stub_ffprobe(monkeypatch)
    res = run_ingest(cfg)
    assert res.ok and res.detail.get("retired_dedup") == ["src_ret"]
    assert Ledger.load(cfg).sources["src_ret"].state is SourceState.retired


def test_run_ingest_route_retired_dedup_shows_match_and_reset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    vid = cfg.inbox / "dup.mp4"; vid.write_bytes(b"REUPLOAD")
    digest = sha256_of(vid)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_ret", source_path="/r.mp4", state=SourceState.retired, sha256=digest))
    _stub_ffprobe(monkeypatch)
    r = _client(cfg).post("/run/ingest")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "src_ret" in body and "retired" in body.lower() and "Reset" in body
    assert Ledger.load(cfg).sources["src_ret"].state is SourceState.retired
    assert len(Ledger.load(cfg).sources) == 1
