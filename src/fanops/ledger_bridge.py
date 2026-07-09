# src/fanops/ledger_bridge.py — MOL-348/M1-F: one-shot idempotent legacy ledger.json -> SqliteLedgerStore import.
# BREAK-GLASS: production never writes ledger.json after M1-F. A pre-flip ledger.json (or its timestamped
# snapshot copy) is the operator rollback artifact — run import_json_to_sqlite manually to re-hydrate sqlite.
from __future__ import annotations
import json, os
from contextlib import contextmanager
from pathlib import Path
from fanops.config import Config
from fanops.errors import ControlFileError
from fanops.ledger import (
    Ledger, SCHEMA_VERSION, _NewerSchema, _canonicalize_ledger_account_refs, _migrate,
)
from fanops.ledger_sqlite import SqliteLedgerStore, _MAP_NAMES
from fanops.models import (
    Batch, Clip, ImportedMedia, Moment, Post, Render, Source, StitchPlan,
)


class _EphemeralLedgerStore:
    def read_raw(self) -> dict | None: return None
    def write_raw(self, doc: dict) -> None: raise RuntimeError("ephemeral store is read-only")
    @contextmanager
    def lock(self, timeout: float | None = None): yield
    def snapshot(self, dest: Path) -> None: raise RuntimeError("ephemeral store is read-only")
    def restore(self, src: Path) -> None: raise RuntimeError("ephemeral store is read-only")


def _row_count(doc: dict) -> int:
    return sum(len(doc.get(m) or {}) for m in _MAP_NAMES)


def _doc_from_migrated_raw(cfg: Config, raw: dict) -> dict:
    raw = _canonicalize_ledger_account_refs(raw)
    led = Ledger(cfg, store=_EphemeralLedgerStore())  # type: ignore[arg-type]
    try:
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
    except Exception as e:
        from fanops.errors import reason as _reason
        raise ControlFileError(f"{cfg.ledger_path.name} invalid: {_reason(e)}") from e
    return led._to_doc()


def _expected_doc_from_json(cfg: Config) -> dict:
    p = cfg.legacy_ledger_json_path
    if not p.exists():
        raise ControlFileError(f"legacy ledger.json not found: {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        from fanops.errors import reason as _reason
        raise ControlFileError(f"{cfg.ledger_path.name} invalid: {_reason(e)}") from e
    on_disk = raw.get("schema_version", 0)
    if on_disk > SCHEMA_VERSION:
        raise _NewerSchema(on_disk)
    if on_disk < SCHEMA_VERSION:
        raw = _migrate(raw, on_disk)
    return _doc_from_migrated_raw(cfg, raw)


def import_json_to_sqlite(cfg: Config) -> bool:
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
