# tests/test_source_lifecycle.py — CLI + Studio source lifecycle verbs (retire / promote / force reset)
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source, SourceState
from fanops.pipeline import promote_source
from fanops.studio import actions


def test_cli_retire_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_x", source_path="/x.mp4", state=SourceState.catalogued))
    from fanops.cli import main
    assert main(["retire-source", "src_x"]) == 0
    assert Ledger.load(cfg).sources["src_x"].state is SourceState.retired


def test_cli_promote_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_d", source_path="/d.mp4", state=SourceState.discovered))
    from fanops.cli import main
    assert main(["promote-source", "src_d"]) == 0
    assert Ledger.load(cfg).sources["src_d"].state is SourceState.catalogued


def test_cli_promote_rejects_non_discovered(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_c", source_path="/c.mp4", state=SourceState.catalogued))
    from fanops.cli import main
    assert main(["promote-source", "src_c"]) == 2


def test_promote_source_unit(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_d", source_path="/d.mp4", state=SourceState.discovered))
        assert promote_source(led, "src_d") is True
        assert led.sources["src_d"].state is SourceState.catalogued


def test_studio_retire_and_promote(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_d", source_path="/d.mp4", state=SourceState.discovered))
    assert actions.promote_source_studio(cfg, "src_d").ok
    assert Ledger.load(cfg).sources["src_d"].state is SourceState.catalogued
    assert actions.retire_source_studio(cfg, "src_d").ok
    assert Ledger.load(cfg).sources["src_d"].state is SourceState.retired


def test_pipeline_status_finished_shows_zero_in_progress(tmp_path):
    from fanops.studio import views
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_done", source_path="/x.mp4", state=SourceState.moments_decided))
    st = views.pipeline_status(cfg)
    assert st["sources"] == 0 and st["sources_inventory"] == 1 and st["native_total"] == 1
