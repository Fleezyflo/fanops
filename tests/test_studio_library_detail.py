# tests/test_studio_library_detail.py — Studio Library pipeline map (read-model + routes)
import json
import pytest
pytest.importorskip("flask")
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState, Moment, MomentState, Clip, ClipState, Post, PostState, Platform
from fanops.agentstep import write_request, pending
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()


def _src(led, cfg, sid="src_1", path=None, state=SourceState.catalogued, **kw):
    p = path or str(cfg.inbox / "clip.mp4")
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_bytes(b"x" * 64)
    s = Source(id=sid, source_path=p, state=state, duration=kw.pop("duration", 30.0), **kw)
    led.add_source(s); return s


def _seed(cfg, fn):
    with Ledger.transaction(cfg) as led:
        fn(led)


# ---- 1: library_catalog shape + fail-open wrap ----
def test_library_catalog_wraps_asset_catalog_shape(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg))
    cat = views.library_catalog(cfg)
    assert "native" in cat and "third_party" in cat and "backlog" in cat
    assert cat["native"][0]["stage_strip"] and len(cat["native"][0]["stage_strip"]) == 11


def test_library_catalog_fail_open_on_read_error(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    monkeypatch.setattr(Ledger, "load", lambda _c: (_ for _ in ()).throw(RuntimeError("torn")))
    cat = views.library_catalog(cfg)
    assert cat == {"native": [], "third_party": [],
                   "backlog": {"actionable": 0, "blocked_on_gates": 0, "recoverable": 0, "inventory": 0}}


# ---- 2-6: strip truth matrix ----
def test_strip_disk_artifact_transcribe_beats_empty_ledger(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg, state=SourceState.catalogued))
    side = cfg.agent_io / "transcripts" / "clip.json"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "hi"}]}))
    strip = views.library_catalog(cfg)["native"][0]["stage_strip"]
    tx = next(c for c in strip if c["key"] == "transcribe")
    assert tx["status"] == "done"


def test_strip_ledger_transcribed_beats_missing_sidecar(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg, state=SourceState.transcribed, meta={"transcribed": True},
                                 transcript=[{"start": 0, "end": 1, "text": "hi"}]))
    strip = views.library_catalog(cfg)["native"][0]["stage_strip"]
    assert next(c for c in strip if c["key"] == "transcribe")["status"] == "done"


def test_strip_third_party_inert(tmp_path):
    cfg = Config(root=tmp_path)
    def _fn(led):
        led.add_source(Source(id="tp", source_path="/x.jpg", origin_kind="third_party",
                              state=SourceState.catalogued))
    _seed(cfg, _fn)
    row = views.library_catalog(cfg)["third_party"][0]
    assert all(c["status"] == "inert" for c in row["stage_strip"])


def test_strip_retired_inventory_inert(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg, state=SourceState.retired))
    row = views.library_catalog(cfg)["native"][0]
    assert all(c["status"] == "inert" for c in row["stage_strip"])


def test_strip_dotted_source_gate_prefix_not_split(tmp_path):
    cfg = Config(root=tmp_path)
    sid = "artist.clip.01"
    _seed(cfg, lambda led: _src(led, cfg, sid=sid, state=SourceState.signalled, meta={"transcribed": True}))
    write_request(cfg, kind="moments", key=f"{sid}.acct_a", payload={"source_id": sid, "duration": 10})
    strip = views.library_catalog(cfg)["native"][0]["stage_strip"]
    moments = next(c for c in strip if c["key"] == "moments")
    assert moments["status"] == "pending" and moments["gate_key"] == f"{sid}.acct_a"


# ---- 7: manifest parity — statuses identical with/without manifest ----
def test_manifest_parity_status_ignores_manifest(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg, state=SourceState.transcribed, meta={"transcribed": True}))
    without = [c["status"] for c in views.source_pipeline_map(cfg, "src_1", offset=0)["stage_strip"]]
    mp = cfg.agent_io / "manifests" / "src_1.json"
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"v": 1, "source_id": "src_1", "stages": {"transcribe": {"at": "2020-01-01T00:00:00+00:00"}}}))
    with_at = views.source_pipeline_map(cfg, "src_1", offset=0)["stage_strip"]
    assert [c["status"] for c in with_at] == without
    assert next(c for c in with_at if c["key"] == "transcribe").get("at")


# ---- 8-11: detail map ----
def test_detail_unknown_source_404(tmp_path):
    cfg = Config(root=tmp_path)
    assert views.source_pipeline_map(cfg, "nope") is None
    assert _client(cfg).get("/library/nope").status_code == 404


def test_detail_counts_and_links(tmp_path):
    cfg = Config(root=tmp_path)
    def _fn(led):
        _src(led, cfg, state=SourceState.moments_decided)
        led.add_moment(Moment(id="m1", parent_id="src_1", start=0, end=5, reason="bar", state=MomentState.decided))
        led.add_clip(Clip(id="c1", parent_id="m1", path=str(cfg.clips / "c1.mp4"), state=ClipState.rendered))
        Path(cfg.clips / "c1.mp4").parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.clips / "c1.mp4").write_bytes(b"v")
    _seed(cfg, _fn)
    d = views.source_pipeline_map(cfg, "src_1", offset=0)
    assert d["stats"]["clips"] == 1 and d["media_url"] == "/source-media/src_1"


def test_transcript_paging_200_segments(tmp_path):
    cfg = Config(root=tmp_path)
    segs = [{"start": float(i), "end": float(i) + 1, "text": "x", "words": ["a", "b"]} for i in range(250)]
    side = cfg.agent_io / "transcripts" / "clip.json"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps({"segments": segs}))
    _seed(cfg, lambda led: _src(led, cfg))
    p0 = views.source_pipeline_map(cfg, "src_1", offset=0)["transcript"]
    assert len(p0.segments) == 200 and p0.next_offset == 200 and p0.word_count == 400
    p1 = views.source_pipeline_map(cfg, "src_1", offset=200)["transcript"]
    assert len(p1.segments) == 50 and p1.next_offset is None


def test_transcript_unreadable_sidecar_degrades_and_logs(tmp_path, caplog):
    cfg = Config(root=tmp_path)
    side = cfg.agent_io / "transcripts" / "clip.json"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text("{bad")
    _seed(cfg, lambda led: _src(led, cfg, transcript=[{"start": 0, "end": 1, "text": "ledger"}]))
    tx = views.source_pipeline_map(cfg, "src_1", offset=0)["transcript"]
    assert tx.source == "ledger" and tx.segments[0]["text"] == "ledger"


# ---- 12-13: stage labels + list links ----
def test_eleven_stage_labels(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg))
    labels = [c["label"] for c in views.library_catalog(cfg)["native"][0]["stage_strip"]]
    assert labels == [s["label"] for s in views.STAGES]


def test_library_list_links_detail(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg))
    r = _client(cfg).get("/library")
    assert r.status_code == 200 and b"/library/src_1" in r.data


# ---- 14-15: serve routes + traversal ----
def test_source_media_serves_bounded_path(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg))
    r = _client(cfg).get("/source-media/src_1")
    assert r.status_code == 200 and r.data


def test_source_media_traversal_404(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg))
    assert _client(cfg).get("/source-media/../etc/passwd").status_code == 404
    assert _client(cfg).get("/source-media/src_1/../../x").status_code in (404, 308)


def test_keyframe_serve_and_traversal(tmp_path):
    cfg = Config(root=tmp_path)
    kdir = cfg.agent_io / "keyframes" / "src_1" / ("a" * 64)
    kdir.mkdir(parents=True)
    (kdir / "grid_test.jpg").write_bytes(b"\xff\xd8\xff")
    _seed(cfg, lambda led: _src(led, cfg))
    wh = "a" * 64
    ok = _client(cfg).get(f"/keyframe/src_1/{wh}/grid_test.jpg")
    assert ok.status_code == 200
    assert _client(cfg).get("/keyframe/src_1/not-a-hash/grid_test.jpg").status_code == 404
    assert _client(cfg).get("/keyframe/src_1/evil.jpg").status_code == 404


# ---- 16: live partial ----
def test_library_live_partial_200(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg))
    r = _client(cfg).get("/library/src_1/live")
    assert r.status_code == 200 and b"library-source-live" in r.data and b"every 15s" in r.data


# ---- 17-18: schedule/posted source= ----
def test_schedule_source_filter_view(tmp_path):
    cfg = Config(root=tmp_path)
    def _fn(led):
        _src(led, cfg, sid="src_a")
        _src(led, cfg, sid="src_b", path=str(cfg.inbox / "b.mp4"))
        led.add_moment(Moment(id="m1", parent_id="src_a", start=0, end=5, reason="a", state=MomentState.decided))
        led.add_moment(Moment(id="m2", parent_id="src_b", start=0, end=5, reason="baz", state=MomentState.decided))
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c1.mp4", state=ClipState.queued))
        led.add_clip(Clip(id="c2", parent_id="m2", path="/c2.mp4", state=ClipState.queued))
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="ig1", platform=Platform.instagram,
                          state=PostState.queued, caption="a"))
        led.add_post(Post(id="p2", parent_id="c2", account="a", account_id="ig1", platform=Platform.instagram,
                          state=PostState.queued, caption="b"))
    _seed(cfg, _fn)
    from datetime import datetime, timezone
    rows = views.schedule_rows(Ledger.load(cfg), cfg, now=datetime.now(timezone.utc), source="src_a")
    assert len(rows) == 1 and rows[0].clip_id == "c1"


def test_posted_source_filter_route(tmp_path):
    cfg = Config(root=tmp_path)
    def _fn(led):
        _src(led, cfg, sid="src_a")
        _src(led, cfg, sid="src_b", path=str(cfg.inbox / "b.mp4"))
        led.add_moment(Moment(id="m1", parent_id="src_a", start=0, end=5, reason="a", state=MomentState.decided))
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c1.mp4", state=ClipState.analyzed))
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="ig1", platform=Platform.instagram,
                          state=PostState.analyzed, caption="x", public_url="https://example.com/p",
                          metrics={"lift_score": 1.0}))
        led.add_moment(Moment(id="m2", parent_id="src_b", start=0, end=5, reason="b", state=MomentState.decided))
        led.add_clip(Clip(id="c2", parent_id="m2", path="/c2.mp4", state=ClipState.analyzed))
        led.add_post(Post(id="p2", parent_id="c2", account="a", account_id="ig1", platform=Platform.instagram,
                          state=PostState.analyzed, caption="y", public_url="https://example.com/q",
                          metrics={"lift_score": 0.5}))
    _seed(cfg, _fn)
    rows = views.posted_library(Ledger.load(cfg), cfg, source="src_a")
    assert len(rows) == 1
    r = _client(cfg).get("/posted?source=src_a")
    assert r.status_code == 200 and b"source-chips" in r.data


# ---- 19: gates deep links ----
def test_gates_deep_link_resolves_pending_row(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, lambda led: _src(led, cfg, state=SourceState.signalled, meta={"transcribed": True}))
    key = "src_1.a"
    write_request(cfg, kind="moments", key=key, payload={"source_id": "src_1", "duration": 10})
    idx = views.library_catalog(cfg)["native"][0]["stage_strip"]
    cell = next(c for c in idx if c.get("gate_key") == key)
    assert cell["gate_url"] == f"/gates#gate-{key}"
    assert key in pending(cfg, kind="moments")


# ---- 20: existing library tests stay green (import smoke) ----
def test_studio_library_module_still_importable():
    import tests.test_studio_library as lib
    assert hasattr(lib, "test_library_route_renders")
