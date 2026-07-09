"""MOL-469: snapshot bundle includes accounts.json + personas.json when present."""
from __future__ import annotations

from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Source


def test_snapshot_includes_control_files_when_present(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    accounts = b'{"accounts": [{"handle": "a", "platforms": ["instagram"]}]}\n'
    personas = b'{"personas": [{"id": "p1", "voice": "test"}]}\n'
    cfg.accounts_path.write_bytes(accounts)
    cfg.personas_path.write_bytes(personas)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
    snap = Ledger.snapshot(cfg)
    assert snap.exists()
    ac_snap = snap.with_name(snap.name.replace(".sqlite", ".accounts.json"))
    pe_snap = snap.with_name(snap.name.replace(".sqlite", ".personas.json"))
    assert ac_snap.read_bytes() == accounts
    assert pe_snap.read_bytes() == personas


def test_snapshot_ok_when_control_files_absent(tmp_path):
    cfg = Config(root=tmp_path)
    snap = Ledger.snapshot(cfg)
    ac_snap = snap.with_name(snap.name.replace(".sqlite", ".accounts.json"))
    pe_snap = snap.with_name(snap.name.replace(".sqlite", ".personas.json"))
    assert snap.exists()
    assert not ac_snap.exists()
    assert not pe_snap.exists()


def test_restore_snapshot_round_trips_control_files(tmp_path):
    cfg = Config(root=tmp_path)
    cfg.control.mkdir(parents=True, exist_ok=True)
    accounts = b'{"accounts": [{"handle": "route", "platforms": ["tiktok"]}]}\n'
    personas = b'{"personas": [{"id": "voice1", "hashtag_corpus": ["tag"]}]}\n'
    cfg.accounts_path.write_bytes(accounts)
    cfg.personas_path.write_bytes(personas)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
    snap = Ledger.snapshot(cfg)
    cfg.accounts_path.write_text("{}")
    cfg.personas_path.write_text("{}")
    Ledger.restore_snapshot(cfg, snap)
    assert cfg.accounts_path.read_bytes() == accounts
    assert cfg.personas_path.read_bytes() == personas
