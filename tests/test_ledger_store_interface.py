# tests/test_ledger_store_interface.py — MOL-346/M1-F: LedgerStore protocol + sqlite-only seam.
from fanops.config import Config
from fanops.ledger import Ledger, LedgerStore
from fanops.ledger_sqlite import SqliteLedgerStore
from fanops.models import Source, SourceState


def test_sqlite_ledger_store_satisfies_protocol(tmp_path):
    assert isinstance(SqliteLedgerStore(Config(root=tmp_path)), LedgerStore)


def test_ledger_defaults_to_sqlite_store(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    assert isinstance(led._store, SqliteLedgerStore)


def test_sqlite_store_round_trips_raw_doc(tmp_path):
    cfg = Config(root=tmp_path)
    store = SqliteLedgerStore(cfg)
    doc = {"schema_version": 11, "sources": {}, "moments": {}, "clips": {}, "posts": {},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {},
           "renders": {}, "imported_media": {}}
    with store.lock():
        store.write_raw(doc)
    assert store.read_raw() == doc


def test_ledger_save_routes_through_store(tmp_path):
    """Ledger.save must persist s1 to disk — a call-count spy can no-op write_raw and still pass."""
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    led.add_source(Source(id="s1", source_path="/x.mp4", width=1, height=1, state=SourceState.catalogued))
    led.save()
    assert "s1" in Ledger.load(cfg).sources
    raw = SqliteLedgerStore(cfg).read_raw()
    assert raw is not None and "s1" in raw["sources"]


def test_transaction_exit_save_routes_through_store(tmp_path):
    """Ledger.transaction exit-save must persist s1 — call-count spies are not a document on disk."""
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/x.mp4", width=1, height=1, state=SourceState.catalogued))
    assert "s1" in Ledger.load(cfg).sources
    raw = SqliteLedgerStore(cfg).read_raw()
    assert raw is not None and "s1" in raw["sources"]
