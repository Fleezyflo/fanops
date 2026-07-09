# tests/test_mol154_p12_ledger_drop.py — P12 (MOL-154): drop account_selections + selection_facts from the
# persisted ledger (SCHEMA_VERSION 10→11). Old ledgers upgrade; posts/moments intact; the deleted model symbols
# are gone. The v8→v9 hop still runs (creates the maps) then v11 drops them — no import error, no selection data survives.
import json
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.models import Post, Platform, PostState


def _write(cfg, raw):
    cfg.legacy_ledger_json_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.legacy_ledger_json_path.write_text(json.dumps(raw))
    if cfg.ledger_path.exists():
        cfg.ledger_path.unlink()


def test_schema_version_is_11():
    assert SCHEMA_VERSION == 11


def test_migration_v10_to_v11_drops_selections(tmp_path):
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 10,
           "sources": {"src_1": {"id": "src_1", "source_path": "/s.mp4", "state": "catalogued"}},
           "moments": {"m1": {"id": "m1", "parent_id": "src_1", "content_token": "m1", "start": 0, "end": 7,
                                "reason": "r", "state": "decided", "affinities": ["a"]}},
           "clips": {}, "posts": {"p1": {"id": "p1", "parent_id": "c1", "account": "a", "account_id": "1",
                                         "platform": "instagram", "caption": "x", "state": "queued"}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {}, "renders": {},
           "imported_media": {},
           "selection_facts": {"sf1": {"id": "sf1", "moment_id": "m1", "account": "a", "method": "llm"}},
           "account_selections": {"as1": {"id": "as1", "source_id": "src_1", "account": "a",
                                          "moment_ids": ["m1"], "method": "llm"}}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert not hasattr(led, "selection_facts")
    assert not hasattr(led, "account_selections")
    assert "m1" in led.moments and led.moments["m1"].affinities == ["a"]
    assert "p1" in led.posts
    with Ledger.transaction(cfg):
        pass
    saved = Ledger.load(cfg)._to_doc()
    assert saved["schema_version"] == 11
    assert "selection_facts" not in saved and "account_selections" not in saved


def test_v8_ledger_upgrades_through_v11(tmp_path):
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 8,
           "sources": {"src_1": {"id": "src_1", "source_path": "/s.mp4", "state": "catalogued"}},
           "moments": {"m1": {"id": "m1", "parent_id": "src_1", "content_token": "m1", "start": 0, "end": 7,
                                "reason": "r", "state": "decided", "affinities": ["a"]}},
           "clips": {}, "posts": {}, "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {},
           "renders": {}, "selection_facts": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.moments["m1"].affinities == ["a"]
    assert not hasattr(led, "account_selections")
    with Ledger.transaction(cfg):
        pass
    saved = Ledger.load(cfg)._to_doc()
    assert saved["schema_version"] == 11
    assert "account_selections" not in saved and "selection_facts" not in saved


def test_account_selection_symbols_gone():
    import fanops.models as m
    for gone in ("AccountSelection", "SelectionFact", "SelectionMethod", "account_selection_id"):
        assert gone not in dir(m), f"{gone} should be removed from fanops.models"


def test_posts_of_account(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p_a", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, public_url="dryrun://p_a"))
        led.add_post(Post(id="p_b", parent_id="c1", account="b", account_id="2", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, public_url="dryrun://p_b"))
    led = Ledger.load(cfg)
    assert {p.id for p in led.posts_of_account("a")} == {"p_a"}


def test_migrate_v10_drop_selections_idempotent():
    from fanops.ledger import _migrate_v10_drop_selections
    raw = {"account_selections": {"x": {}}, "selection_facts": {"y": {}}, "moments": {"m": {}}}
    out = _migrate_v10_drop_selections(dict(raw))
    assert "account_selections" not in out and "selection_facts" not in out and "moments" in out
    out2 = _migrate_v10_drop_selections(out)
    assert out2 == out
