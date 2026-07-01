# tests/test_studio_library.py — M1 (structural-hooks): the Studio Library tab (asset-memory surface)
import io
import pytest
pytest.importorskip("flask")   # Studio is the optional [studio] extra — skip route tests when Flask is absent
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.studio import views


def _client(cfg):
    from fanops.studio.app import create_app
    app = create_app(cfg); app.config.update(TESTING=True); return app.test_client()

def _seed_mixed(cfg):
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_n", source_path="/n.mp4", state=SourceState.catalogued))
        led.add_source(Source(id="src_t", source_path="/t.jpg", origin_kind="third_party",
                              state=SourceState.catalogued))


# ---- read-models ----
def test_asset_catalog_splits_native_and_third_party(tmp_path):
    cfg = Config(root=tmp_path); _seed_mixed(cfg)
    cat = views.asset_catalog(cfg)
    assert [s["id"] for s in cat["native"]] == ["src_n"]
    assert len(cat["third_party"]) == 1 and cat["third_party"][0]["origin_kind"] == "third_party"

def test_asset_catalog_fail_open_on_absent_ledger(tmp_path):
    # the tab must never 500 — an empty/torn ledger yields empty lists, not an exception
    cat = views.asset_catalog(Config(root=tmp_path))
    assert cat["native"] == [] and cat["third_party"] == []

def test_asset_catalog_records_read_failure_not_silent(tmp_path, monkeypatch):
    # fail-open must NOT be silent: a real read failure (vs a genuinely-empty library) must leave a
    # run.log signal, else the operator reads "0 assets" as "nothing uploaded" when the ledger is torn.
    cfg = Config(root=tmp_path)
    def _boom(_cfg): raise RuntimeError("torn ledger")
    monkeypatch.setattr(Ledger, "load", _boom)
    cat = views.asset_catalog(cfg)
    assert cat == {"native": [], "third_party": []}              # still fail-open (never 500)
    log = cfg.log_path.read_text() if cfg.log_path.exists() else ""
    assert "library" in log and "error" in log                   # the failure is RECORDED, not swallowed

def test_asset_catalog_surfaces_degraded_reason(tmp_path):
    # probe_failed_degraded_not_visible_in_ui (high): Source.degraded_reason is "the single VISIBLE-degradation
    # channel", but asset_catalog omitted it — a corrupt 0×0 source rendered a mangled clip with NO Library
    # warning. The read-model must carry the field so the operator can SEE (and delete) the bad asset.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_bad", source_path="/bad.mp4", state=SourceState.catalogued,
                              degraded_reason="probe_failed"))
    row = views.asset_catalog(cfg)["native"][0]
    assert row.get("degraded_reason") == "probe_failed"          # the degradation channel reaches the UI read-model

def test_library_route_shows_degraded_marker(tmp_path):
    # The Library page must VISIBLY flag a degraded source so a corrupt asset is recognizable + deletable,
    # not silently rendered into a mangled clip that ships to production.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_bad", source_path="/bad.mp4", state=SourceState.catalogued,
                              degraded_reason="probe_failed"))
    r = _client(cfg).get("/library")
    assert r.status_code == 200
    assert b"degraded" in r.data and b"probe_failed" in r.data    # the marker + reason are rendered

def test_pipeline_status_chain_count_excludes_third_party(tmp_path):
    cfg = Config(root=tmp_path); _seed_mixed(cfg)
    st = views.pipeline_status(cfg)
    assert st["sources"] == 1 and st["third_party"] == 1     # chain count native-only; third-party shown apart


# ---- routes ----
def test_library_route_renders(tmp_path):
    cfg = Config(root=tmp_path); _seed_mixed(cfg)
    r = _client(cfg).get("/library")
    assert r.status_code == 200 and b"Asset library" in r.data

def test_library_upload_catalogues_third_party(tmp_path, mocker):
    mocker.patch("fanops.ingest.has_video_stream", return_value=True)
    mocker.patch("fanops.ingest.probe_dimensions", return_value=(1080, 1920, 0.0))
    cfg = Config(root=tmp_path)
    r = _client(cfg).post("/library/upload", data={"files": (io.BytesIO(b"P"), "hold.jpg")},
                          content_type="multipart/form-data")
    assert r.status_code == 200
    srcs = list(Ledger.load(cfg).sources.values())
    assert len(srcs) == 1 and srcs[0].origin_kind == "third_party"   # one POST: validate -> land -> catalogue

def test_run_panel_shows_third_party_count(tmp_path):
    cfg = Config(root=tmp_path); _seed_mixed(cfg)
    r = _client(cfg).get("/run")
    assert r.status_code == 200 and b"3rd-party" in r.data           # operator still sees uploaded assets on Run
