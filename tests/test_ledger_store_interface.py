# tests/test_ledger_store_interface.py — MOL-346: LedgerStore protocol + JsonLedgerStore default seam.
from contextlib import contextmanager
from fanops.config import Config
from fanops.ledger import Ledger, JsonLedgerStore, LedgerStore


def test_json_ledger_store_satisfies_protocol(tmp_path):
    assert isinstance(JsonLedgerStore(Config(root=tmp_path)), LedgerStore)


def test_ledger_defaults_to_json_store(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    assert isinstance(led._store, JsonLedgerStore)


def test_json_store_round_trips_raw_doc(tmp_path):
    cfg = Config(root=tmp_path)
    store = JsonLedgerStore(cfg)
    doc = {"schema_version": 11, "sources": {}, "moments": {}, "clips": {}, "posts": {},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {},
           "renders": {}, "imported_media": {}}
    with store.lock():
        store.write_raw(doc)
    assert store.read_raw() == doc


def test_ledger_save_routes_through_store(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    hits = {"lock": 0, "write": 0}
    real_lock = led._store.lock
    real_write = led._store.write_raw
    @contextmanager
    def spy_lock(*a, **kw):
        hits["lock"] += 1
        with real_lock(*a, **kw): yield
    def spy_write(doc):
        hits["write"] += 1
        return real_write(doc)
    monkeypatch.setattr(led._store, "lock", spy_lock)
    monkeypatch.setattr(led._store, "write_raw", spy_write)
    led.save()
    assert hits["lock"] == 1 and hits["write"] == 1


def test_transaction_exit_save_routes_through_store(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    hits = {"lock": 0, "write": 0}
    real_lock = JsonLedgerStore.lock
    real_write = JsonLedgerStore.write_raw
    @contextmanager
    def spy_lock(self, timeout=None):
        hits["lock"] += 1
        with real_lock(self, timeout=timeout): yield
    def spy_write(self, doc):
        hits["write"] += 1
        return real_write(self, doc)
    monkeypatch.setattr(JsonLedgerStore, "lock", spy_lock)
    monkeypatch.setattr(JsonLedgerStore, "write_raw", spy_write)
    with Ledger.transaction(cfg) as led:
        from fanops.models import Source, SourceState
        led.add_source(Source(id="s1", source_path="/x.mp4", width=1, height=1, state=SourceState.catalogued))
    assert hits["lock"] == 1 and hits["write"] == 1
