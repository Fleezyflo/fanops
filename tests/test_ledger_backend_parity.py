# tests/test_ledger_backend_parity.py — MOL-349: json vs sqlite backend parity under FANOPS_LEDGER_BACKEND.
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.ledger_sqlite import SqliteLedgerStore
from fanops.models import (
    Clip, ClipState, Fmt, Moment, MomentState, Platform, Post, PostState, Source, SourceState,
)
from fanops.timeutil import iso_z


def _run_contract_script(root: Path, backend: str, monkeypatch) -> dict:
    """Representative ledger contract: add→approve→reconcile→retire→queries (no-auto-publish + cascade)."""
    monkeypatch.setenv("FANOPS_LEDGER_BACKEND", backend)
    cfg = Config(root=root)
    now = iso_z(datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc))
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/inbox/a.mp4", width=1920, height=1080, state=SourceState.catalogued))
        led.add_moment(Moment(id="m1", parent_id="s1", content_token="1-5", start=1.0, end=5.0, reason="peak",
                              state=MomentState.decided))
        led.add_clip(Clip(id="c1", parent_id="m1", path="/clips/c.mp4", aspect=Fmt.r9x16, state=ClipState.rendered))
        led.add_post(Post(id="p1", parent_id="c1", account="acct", account_id="z1", platform=Platform.instagram,
                          caption="cap", state=PostState.awaiting_approval))
        assert led.posts["p1"].state is PostState.awaiting_approval, "no-auto-publish: born awaiting, not queued"
        led.approve_post("p1", now_iso=now, suggested_iso=now)
        assert led.posts["p1"].state is PostState.queued
        led.add_moment(Moment(id="m2", parent_id="s1", content_token="6-10", start=6.0, end=10.0, reason="alt",
                              state=MomentState.decided))
        led.add_clip(Clip(id="c2", parent_id="m2", path="/clips/d.mp4", aspect=Fmt.r9x16, state=ClipState.rendered))
        led.add_post(Post(id="p2", parent_id="c2", account="acct", account_id="z1", platform=Platform.instagram,
                          caption="rej", state=PostState.rejected))
    with Ledger.transaction(cfg) as led:
        led.reconcile_moments("s1", {})                              # cascade: m1's queued post protected
        assert "p1" in led.posts and "c1" in led.clips, "cascade must preserve queued post + clip"
        assert led.moments["m1"].state is MomentState.retired
        assert "p2" not in led.posts and "c2" not in led.clips, "rejected lineage is droppable"
    with Ledger.transaction(cfg) as led:
        led.retire_source("s1")
        assert led.is_retired_source("s1")
    led = Ledger.load(cfg)
    assert led.posts_of("c1") == [led.posts["p1"]]
    assert led.moments_of("s1")
    return led._to_doc()


def test_json_and_sqlite_backends_yield_identical_state(tmp_path, monkeypatch):
    json_doc = _run_contract_script(tmp_path / "json", "json", monkeypatch)
    sqlite_doc = _run_contract_script(tmp_path / "sqlite", "sqlite", monkeypatch)
    assert sqlite_doc == json_doc


def test_sqlite_first_load_auto_imports_from_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_LEDGER_BACKEND", "json")
    cfg = Config(root=tmp_path)
    json_doc = _run_contract_script(tmp_path, "json", monkeypatch)
    db = cfg.ledger_path.with_suffix(".sqlite")
    assert db.exists() is False
    monkeypatch.setenv("FANOPS_LEDGER_BACKEND", "sqlite")
    led = Ledger.load(Config(root=tmp_path))
    assert isinstance(led._store, SqliteLedgerStore)
    assert db.exists()
    assert led._to_doc() == json_doc


def test_default_backend_is_json_without_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("FANOPS_LEDGER_BACKEND", raising=False)
    led = Ledger.load(Config(root=tmp_path))
    from fanops.ledger import JsonLedgerStore
    assert isinstance(led._store, JsonLedgerStore)
