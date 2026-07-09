# tests/test_ledger_json_to_sqlite_bridge.py — MOL-348/M1-F
from __future__ import annotations
import json, os
from datetime import datetime, timezone
import pytest
import fanops.ledger as ledger_mod
from fanops.config import Config
from fanops.errors import ControlFileError
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.ledger_bridge import import_json_to_sqlite
from fanops.models import (
    Batch, Clip, ClipState, Fmt, ImportedMedia, Moment, MomentState, Platform, Post, PostState,
    Render, RenderState, Source, SourceState, StitchPlan, StitchState,
)

def _populated_doc(cfg):
    led = Ledger.load(cfg)
    led.add_source(Source(id="src1", source_path="/inbox/a.mp4", width=1920, height=1080, state=SourceState.catalogued))
    led.add_moment(Moment(id="mom1", parent_id="src1", content_token="1-5", start=1.0, end=5.0, reason="peak", state=MomentState.decided))
    led.add_clip(Clip(id="clip1", parent_id="mom1", path="/clips/c.mp4", aspect=Fmt.r9x16, state=ClipState.rendered))
    led.add_post(Post(id="post1", parent_id="clip1", account="acct", account_id="z1", platform=Platform.instagram, caption="cap", state=PostState.awaiting_approval))
    led.tag_log["acct|clip1"] = "2026-06-01T12:00:00Z"
    led.variant_streaks["acct|instagram"] = {"hook": "h", "fingerprint": "fp", "streak": 2}
    led.add_stitch_plan(StitchPlan(id="st1", clip_id="clip1", strategy_key="impact_cut", state=StitchState.suggested))
    led.add_batch(Batch(id="bat1", name="Launch", target_accounts=["acct"]))
    led.add_render(Render(id="r1", clip_id="clip1", account="acct", surface_key="acct/instagram", hook_text="hook", path="/renders/r.mp4", state=RenderState.rendered))
    led.add_imported_media(ImportedMedia(media_id="ig1", permalink="https://ig/reel/A/", product_type="REELS"))
    led.save()
    return led._to_doc()

def _write_legacy(cfg, doc):
    cfg.legacy_ledger_json_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.legacy_ledger_json_path.write_text(json.dumps(doc))
    if cfg.ledger_path.exists():
        cfg.ledger_path.unlink()

def test_full_fixture_schema11_import_reconstructs_byte_identical(tmp_path):
    cfg = Config(root=tmp_path)
    doc = _populated_doc(cfg)
    _write_legacy(cfg, doc)
    import_json_to_sqlite(cfg)
    assert Ledger.load(cfg)._to_doc() == doc

def test_pre_v11_fixture_migrated_then_imported_matches_json_load(tmp_path, monkeypatch):
    fixed = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    class _FrozenDatetime(ledger_mod.datetime):
        @classmethod
        def now(cls, tz=None): return fixed
    monkeypatch.setattr(ledger_mod, "datetime", _FrozenDatetime)
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 2, "sources": {"src_aaaaaaaaaaaa": {"id": "src_aaaaaaaaaaaa", "source_path": "/inbox/x.mp4", "state": "catalogued"}},
           "moments": {}, "clips": {}, "posts": {"p1": {"id": "p1", "parent_id": "c1", "account": "a", "account_id": "1", "platform": "instagram", "caption": "x", "state": "awaiting_approval"}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write_legacy(cfg, raw)
    json_doc = Ledger.load(cfg)._to_doc()
    import_json_to_sqlite(cfg)
    assert Ledger.load(cfg)._to_doc() == json_doc

def test_import_idempotent_double_run(tmp_path):
    cfg = Config(root=tmp_path)
    _write_legacy(cfg, _populated_doc(cfg))
    db = cfg.ledger_path
    assert import_json_to_sqlite(cfg) is True
    first_mtime = db.stat().st_mtime
    first_bytes = db.read_bytes()
    assert import_json_to_sqlite(cfg) is False
    assert db.stat().st_mtime == first_mtime and db.read_bytes() == first_bytes

def test_interrupt_leaves_original_json_untouched(tmp_path, monkeypatch):
    cfg = Config(root=tmp_path)
    doc = _populated_doc(cfg)
    cfg.legacy_ledger_json_path.write_text(json.dumps(doc))  # break-glass artifact on disk
    json_before = cfg.legacy_ledger_json_path.read_bytes()
    if cfg.ledger_path.exists():
        cfg.ledger_path.unlink()
    real_replace = os.replace
    def boom_replace(src, dst):
        if str(dst).endswith(".sqlite"):
            raise OSError("simulated interrupt")
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", boom_replace)
    with pytest.raises(OSError, match="simulated interrupt"):
        import_json_to_sqlite(cfg)
    assert cfg.legacy_ledger_json_path.read_bytes() == json_before
    assert not cfg.ledger_path.exists()

def test_newer_schema_refused(tmp_path):
    cfg = Config(root=tmp_path)
    _write_legacy(cfg, {"schema_version": SCHEMA_VERSION + 1, "sources": {}, "moments": {}, "clips": {}, "posts": {}})
    with pytest.raises(ControlFileError, match="schema|upgrade"):
        import_json_to_sqlite(cfg)
