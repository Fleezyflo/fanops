# src/fanops/ledger_sqlite.py — MOL-347: SQLite/WAL LedgerStore backend (standalone, not wired).
from __future__ import annotations
import json, os, shutil, sqlite3
from contextlib import contextmanager
from pathlib import Path
from fanops.config import Config
from fanops.errors import ControlFileError, LockBusyError, reason as _reason

_DEFAULT_LOCK_TIMEOUT = 30.0
# kv(map_name, row_id) — one table for all 10 top-level maps (_save_unlocked doc shape).
_MAP_NAMES = (
    "sources", "moments", "clips", "posts", "tag_log", "variant_streaks",
    "stitch_plans", "batches", "renders", "imported_media",
)


class SqliteLedgerStore:
    """SQLite/WAL persistence implementing LedgerStore (MOL-347). Schema: ledger_meta + ledger_rows kv."""
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db_path = cfg.ledger_path.with_suffix(".sqlite")
        self._conn: sqlite3.Connection | None = None

    def _open(self, *, timeout: float | None = None) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=timeout if timeout is not None else _DEFAULT_LOCK_TIMEOUT)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS ledger_rows (map_name TEXT NOT NULL, row_id TEXT NOT NULL,"
            " payload TEXT NOT NULL, PRIMARY KEY (map_name, row_id));"
        )
        return conn

    def _payload_rows(self, doc: dict) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for map_name in _MAP_NAMES:
            for row_id, val in (doc.get(map_name) or {}).items():
                rows.append((map_name, str(row_id), json.dumps(val, separators=(",", ":"), default=str)))
        return rows

    def read_raw(self) -> dict | None:
        if not self.db_path.exists(): return None
        conn = self._open()
        try:
            row = conn.execute("SELECT value FROM ledger_meta WHERE key='schema_version'").fetchone()
            if row is None: return None
            doc: dict = {"schema_version": int(row[0])}
            for map_name in _MAP_NAMES:
                fetched = conn.execute(
                    "SELECT row_id, payload FROM ledger_rows WHERE map_name=? ORDER BY row_id", (map_name,)
                ).fetchall()
                doc[map_name] = {rid: json.loads(payload) for rid, payload in fetched}
            return doc
        finally:
            conn.close()

    def write_raw(self, doc: dict) -> None:
        own = self._conn is None
        conn = self._conn or self._open()
        try:
            if own: conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM ledger_meta")
            conn.execute("DELETE FROM ledger_rows")
            conn.execute("INSERT INTO ledger_meta(key, value) VALUES('schema_version', ?)",
                         (str(doc["schema_version"]),))
            conn.executemany(
                "INSERT INTO ledger_rows(map_name, row_id, payload) VALUES(?, ?, ?)",
                self._payload_rows(doc),
            )
            if own: conn.commit()
        except Exception:
            if own: conn.rollback()
            raise
        finally:
            if own: conn.close()

    @contextmanager
    def lock(self, timeout: float | None = None):
        if self._conn is not None:
            raise RuntimeError("SqliteLedgerStore.lock() nested on same instance")
        tout = timeout if timeout is not None else _DEFAULT_LOCK_TIMEOUT
        self._conn = self._open(timeout=tout)
        try:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as err:
                raise LockBusyError(
                    f"ledger lock busy > {tout}s (another fanops process is writing): {self.db_path}") from err
            yield
            self._conn.commit()
        except LockBusyError:
            self._conn.rollback()
            raise
        except Exception:
            self._conn.rollback()
            raise
        finally:
            conn, self._conn = self._conn, None
            conn.close()

    def snapshot(self, dest: Path) -> None:
        if dest.exists():
            raise ControlFileError(f"ledger snapshot already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        own = self._conn is None
        conn = self._conn or self._open()
        relock = not own
        try:
            if relock: conn.commit()  # backup needs a quiesced txn (uncommitted write-txn deadlocks backup)
            dest_conn = sqlite3.connect(dest)
            try:
                conn.backup(dest_conn)
            finally:
                dest_conn.close()
            if relock: conn.execute("BEGIN IMMEDIATE")
        finally:
            if own: conn.close()

    def restore(self, src: Path) -> None:
        if not src.exists():
            raise ControlFileError(_reason("ledger snapshot not found", str(src)))
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            tmp = self.db_path.with_suffix(".sqlite.tmp")
            shutil.copy2(str(src), str(tmp))
            os.replace(str(tmp), str(self.db_path))
            self._conn = self._open()
            self._conn.execute("BEGIN IMMEDIATE")
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.db_path.with_suffix(".sqlite.tmp")
        shutil.copy2(str(src), str(tmp))
        os.replace(str(tmp), str(self.db_path))
