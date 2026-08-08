# tests/test_cli_retire_source.py — MOL-842: CLI retire-source preview / confirm / snapshot / audit guards.
import json
from fanops.cli import main
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Moment, Source, SourceState


def _seed_with_clip(cfg: Config, clip_path) -> None:
    clip_path.write_bytes(b"CLIP_BYTES")
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="src_x", source_path="/x.mp4", state=SourceState.catalogued))
        led.add_moment(Moment(id="m0", parent_id="src_x", content_token="t", start=0, end=2, reason="a"))
        led.add_clip(Clip(id="c0", parent_id="m0", path=str(clip_path), state=ClipState.rendered))


def test_retire_source_preview_is_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    clip_path = tmp_path / "c0.mp4"
    _seed_with_clip(cfg, clip_path)
    before = (cfg.ledger_path.read_bytes() if cfg.ledger_path.exists() else b"")
    assert main(["retire-source", "src_x"]) == 0
    assert (cfg.ledger_path.read_bytes() if cfg.ledger_path.exists() else b"") == before
    assert clip_path.exists()
    out = json.loads(capsys.readouterr().out)
    assert out["source_id"] == "src_x"
    assert out["delete_moments"] == 1 and out["delete_clips"] == 1


def test_retire_source_confirm_snapshots_audits_and_cascades(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    clip_path = tmp_path / "c0.mp4"
    _seed_with_clip(cfg, clip_path)
    assert main(["retire-source", "src_x", "--i-understand-this-deletes-unshipped-media"]) == 0
    assert "retire-source src_x" in capsys.readouterr().out
    led = Ledger.load(cfg)
    assert led.sources["src_x"].state is SourceState.retired
    assert "c0" not in led.clips and not clip_path.exists()
    snaps = list(cfg.control.glob("ledger.snapshot.*.sqlite"))
    assert snaps, "a restorable pre-retire snapshot must exist before mutation"
    from fanops import ledger_wipe
    assert ledger_wipe.snapshot_is_restorable(snaps[0])
    audit = (cfg.control / "studio_audit.log").read_text(encoding="utf-8").splitlines()
    assert len(audit) == 1
    entry = json.loads(audit[0])
    assert entry["action"] == "retire_source" and entry["post_ids"] == ["src_x"]
    assert entry["reason"] == "cli_retire"


def test_retire_source_refuses_unrestorable_snapshot_no_unlink(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(root=tmp_path)
    clip_path = tmp_path / "c0.mp4"
    _seed_with_clip(cfg, clip_path)
    from fanops import ledger_wipe
    monkeypatch.setattr(ledger_wipe, "snapshot_is_restorable", lambda _p: False)
    assert main(["retire-source", "src_x", "--i-understand-this-deletes-unshipped-media"]) == 2
    assert clip_path.exists()
    assert Ledger.load(cfg).sources["src_x"].state is SourceState.catalogued
    assert not (cfg.control / "studio_audit.log").exists()
