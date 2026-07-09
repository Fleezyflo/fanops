# src/fanops/ledger_bridge.py — MOL-348: one-shot idempotent ledger.json -> SqliteLedgerStore import.
from __future__ import annotations
import json, os
from fanops.config import Config
from fanops.errors import ControlFileError
from fanops.ledger import (
    Ledger, SCHEMA_VERSION, _NewerSchema, _canonicalize_ledger_account_refs, _migrate,
)
from fanops.ledger_sqlite import SqliteLedgerStore, _MAP_NAMES
from fanops.models import (
    Batch, Clip, ImportedMedia, Moment, Post, Render, Source, StitchPlan,
)


def _row_count(doc: dict) -> int:
    return sum(len(doc.get(m) or {}) for m in _MAP_NAMES)


def _doc_from_migrated_raw(cfg: Config, raw: dict) -> dict:
    """Pydantic round-trip — same reconstruction Ledger.load performs after _migrate."""
    raw = _canonicalize_ledger_account_refs(raw)
    led = Ledger(cfg)
    led.sources = {k: Source(**v) for k, v in raw.get("sources", {}).items()}
    led.moments = {k: Moment(**v) for k, v in raw.get("moments", {}).items()}
    led.clips = {k: Clip(**v) for k, v in raw.get("clips", {}).items()}
    led.posts = {k: Post(**v) for k, v in raw.get("posts", {}).items()}
    led.tag_log = raw.get("tag_log", {})
    led.variant_streaks = raw.get("variant_streaks", {})
    led.stitch_plans = {k: StitchPlan(**v) for k, v in raw.get("stitch_plans", {}).items()}
    led.batches = {k: Batch(**v) for k, v in raw.get("batches", {}).items()}
    led.renders = {k: Render(**v) for k, v in raw.get("renders", {}).items()}
    led.imported_media = {k: ImportedMedia(**v) for k, v in raw.get("imported_media", {}).items()}
    return led._to_doc()


def _expected_doc_from_json(cfg: Config) -> dict:
    p = cfg.ledger_path
    if not p.exists():
        raise ControlFileError(f"ledger.json not found: {p}")
    raw = json.loads(p.read_text())
    on_disk = raw.get("schema_version", 0)
    if on_disk > SCHEMA_VERSION:
        raise _NewerSchema(on_disk)
    if on_disk < SCHEMA_VERSION:
        raw = _migrate(raw, on_disk)
    return _doc_from_migrated_raw(cfg, raw)


def import_json_to_sqlite(cfg: Config) -> bool:
    """Import ledger.json into SqliteLedgerStore (temp DB, parity verify, atomic place). Not the default backend."""
    expected = _expected_doc_from_json(cfg)
    store = SqliteLedgerStore(cfg)
    dest = store.db_path
    if dest.exists():
        existing = store.read_raw()
        if existing == expected:
            return False
    tmp = dest.with_suffix(".sqlite.importing")
    if tmp.exists():
        tmp.unlink()
    tmp_store = SqliteLedgerStore(cfg)
    tmp_store.db_path = tmp
    try:
        with tmp_store.lock():
            tmp_store.write_raw(expected)
        got = tmp_store.read_raw()
        if got != expected or _row_count(got) != _row_count(expected):
            raise ControlFileError("ledger sqlite import parity check failed")
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(tmp), str(dest))
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    return True
