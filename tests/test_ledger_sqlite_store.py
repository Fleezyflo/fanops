# tests/test_ledger_sqlite_store.py — MOL-347: SqliteLedgerStore parity + WAL properties.
from __future__ import annotations
import sqlite3, threading
from fanops.config import Config
from fanops.ledger import Ledger, LedgerStore, SCHEMA_VERSION
from fanops.ledger_sqlite import SqliteLedgerStore
from fanops.models import (
    Batch, Clip, ClipState, Fmt, ImportedMedia, Moment, MomentState, Platform, Post, PostState,
    Render, RenderState, Source, SourceState, StitchPlan, StitchState,
)

_DOC_KEYS = (
    "schema_version", "sources", "moments", "clips", "posts", "tag_log", "variant_streaks",
    "stitch_plans", "batches", "renders", "imported_media",
)


def _populated_ledger(cfg: Config) -> Ledger:
    led = Ledger.load(cfg)
    led.add_source(Source(id="src1", source_path="/inbox/a.mp4", width=1920, height=1080, state=SourceState.catalogued))
    led.add_moment(Moment(id="mom1", parent_id="src1", content_token="1-5", start=1.0, end=5.0, reason="peak",
                          state=MomentState.decided))
    led.add_clip(Clip(id="clip1", parent_id="mom1", path="/clips/c.mp4", aspect=Fmt.r9x16, state=ClipState.rendered))
    led.add_post(Post(id="post1", parent_id="clip1", account="acct", account_id="z1", platform=Platform.instagram,
                      caption="cap", state=PostState.awaiting_approval))
    led.tag_log["acct|clip1"] = "2026-06-01T12:00:00Z"
    led.variant_streaks["acct|instagram"] = {"hook": "h", "fingerprint": "fp", "streak": 2}
    led.add_stitch_plan(StitchPlan(id="st1", clip_id="clip1", strategy_key="impact_cut", state=StitchState.suggested))
    led.add_batch(Batch(id="bat1", name="Launch", target_accounts=["acct"]))
    led.add_render(Render(id="r1", clip_id="clip1", account="acct", surface_key="acct/instagram",
                          hook_text="hook", path="/renders/r.mp4", state=RenderState.rendered))
    led.add_imported_media(ImportedMedia(media_id="ig1", permalink="https://ig/reel/A/", product_type="REELS"))
    return led


def test_sqlite_store_satisfies_protocol(tmp_path):
    assert isinstance(SqliteLedgerStore(Config(root=tmp_path)), LedgerStore)


def test_full_doc_round_trip_all_ten_maps(tmp_path):
    cfg = Config(root=tmp_path)
    led = _populated_ledger(cfg)
    doc = led._to_doc()
    assert list(doc.keys()) == list(_DOC_KEYS)
    store = SqliteLedgerStore(cfg)
    with store.lock():
        store.write_raw(doc)
    got = store.read_raw()
    assert got == doc
    led2 = Ledger.load(cfg, store=store)
    assert led2._to_doc() == doc
    assert led2.sources == led.sources and led2.moments == led.moments
    assert led2.clips == led.clips and led2.posts == led.posts
    assert led2.tag_log == led.tag_log and led2.variant_streaks == led.variant_streaks
    assert led2.stitch_plans == led.stitch_plans and led2.batches == led.batches
    assert led2.renders == led.renders and led2.imported_media == led.imported_media


def test_pragma_journal_mode_is_wal(tmp_path):
    cfg = Config(root=tmp_path)
    store = SqliteLedgerStore(cfg)
    doc = {"schema_version": SCHEMA_VERSION, "sources": {}, "moments": {}, "clips": {}, "posts": {},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {},
           "renders": {}, "imported_media": {}}
    with store.lock():
        store.write_raw(doc)
    conn = sqlite3.connect(store.db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_concurrent_reader_sees_committed_while_writer_holds_txn(tmp_path):
    cfg = Config(root=tmp_path)
    store = SqliteLedgerStore(cfg)
    doc_v1 = _populated_ledger(cfg)._to_doc()
    doc_v2 = dict(doc_v1)
    doc_v2["tag_log"] = {"other|clip": "2026-07-01T00:00:00Z"}
    with store.lock():
        store.write_raw(doc_v1)
    gate = threading.Event()
    seen: dict = {}
    def reader():
        gate.wait(timeout=5)
        seen["doc"] = store.read_raw()
    with store.lock():
        store.write_raw(doc_v2)
        t = threading.Thread(target=reader)
        t.start()
        gate.set()
        t.join(timeout=5)
        assert seen["doc"] == doc_v1
    assert store.read_raw() == doc_v2


def test_killed_mid_write_recovers_prior_commit(tmp_path):
    """Uncommitted txn rolled back on close — WAL reader still sees last COMMIT (no flock orphan)."""
    cfg = Config(root=tmp_path)
    store = SqliteLedgerStore(cfg)
    doc_a = _populated_ledger(cfg)._to_doc()
    doc_b = dict(doc_a)
    doc_b["variant_streaks"] = {"x|y": {"hook": "z", "fingerprint": "f", "streak": 9}}
    with store.lock():
        store.write_raw(doc_a)
    conn = sqlite3.connect(store.db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM ledger_rows")
        conn.execute("INSERT INTO ledger_meta(key, value) VALUES('schema_version', '99')")
        conn.close()  # implicit ROLLBACK — simulates kill mid-write
    except Exception:
        conn.close()
    assert store.read_raw() == doc_a
    wal = store.db_path.with_name(store.db_path.name + "-wal")
    assert wal.exists() or store.db_path.exists()


def test_snapshot_restore_round_trip(tmp_path):
    cfg = Config(root=tmp_path)
    store = SqliteLedgerStore(cfg)
    doc = _populated_ledger(cfg)._to_doc()
    snap = cfg.control / "ledger.snapshot.test.sqlite"
    with store.lock():
        store.write_raw(doc)
        store.snapshot(snap)
    store2 = SqliteLedgerStore(cfg)
    with store2.lock():
        store2.restore(snap)
    assert store2.read_raw() == doc
