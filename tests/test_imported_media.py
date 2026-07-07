# tests/test_imported_media.py — ledger-rebuild M1: the ImportedMedia entity + SCHEMA_VERSION 9->10.
# The spine of "Instagram is the source of truth": a live IG post with NO clip lineage (media_id key,
# permalink, product_type, metrics, metrics_series; no Post.parent_id) held as an authoritative record.
# The load-bearing safety: an old v9 ledger migrates 9->10 WITHOUT losing a row and gains an empty
# imported_media map; the map SURVIVES a save/load round-trip (a naive add drops it on save).
import json
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.models import ImportedMedia, Source, SourceState


def _write(cfg, raw):
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps(raw))


def test_schema_version_is_eleven():
    # P12 (MOL-154) bumped 10->11 dropping account_selections + selection_facts.
    assert SCHEMA_VERSION == 11


def test_imported_media_model_has_no_clip_lineage():
    # ImportedMedia keys on the Graph media_id and carries NO parent_id (the decided representation:
    # Post keeps meaning "authored here"; ImportedMedia means "mirrored from live"). NO reader of
    # Post.parent_id can be handed one.
    im = ImportedMedia(media_id="17900000000000001",
                       permalink="https://www.instagram.com/reel/AbC/",
                       product_type="REELS")
    assert im.media_id == "17900000000000001"
    assert im.permalink == "https://www.instagram.com/reel/AbC/"
    assert im.product_type == "REELS"
    assert im.metrics == {} and im.metrics_series == []
    assert not hasattr(im, "parent_id")   # no clip lineage — the whole point of the entity


def test_v9_ledger_migrates_to_v10_with_empty_imported_media(tmp_path):
    # A v9 on-disk ledger (no imported_media key) must migrate cleanly to v10 and load with an empty
    # imported_media map — never raise "no migration path to v10". No row lost.
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 9,
           "sources": {"src_aaaaaaaaaaaa": {"id": "src_aaaaaaaaaaaa", "source_path": "/x.mp4",
                                            "state": "catalogued", "created_at": "2026-05-01T00:00:00Z"}},
           "moments": {}, "clips": {},
           "posts": {"p_keep": {"id": "p_keep", "parent_id": "c1", "account": "a", "account_id": "1",
                                "platform": "instagram", "caption": "x", "state": "awaiting_approval",
                                "created_at": "2026-05-01T00:00:00Z"}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {}, "renders": {},
           "selection_facts": {}, "account_selections": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)                                          # must NOT raise
    assert led.imported_media == {}                                # v9->v10 injection (empty map)
    assert set(led.sources) == {"src_aaaaaaaaaaaa"}                # no source lost
    assert set(led.posts) == {"p_keep"}                            # no post lost
    assert led.posts["p_keep"].state.value == "awaiting_approval"


def test_imported_media_round_trips(tmp_path):
    # The map serializes + reloads (additive top-level collection, like renders/selection_facts). A naive
    # add that forgets the _save_unlocked dump line + the load line DROPS the record on round-trip — this
    # is the acceptance the PRD flags.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_imported_media(ImportedMedia(
            media_id="17900000000000002",
            permalink="https://www.instagram.com/reel/XyZ/",
            product_type="FEED",
            metrics={"reach": 1234, "likes": 56},
            metrics_series=[{"offset": "P1D", "captured_at": "2026-06-01T00:00:00Z", "reach": 1234}]))
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION
    led2 = Ledger.load(cfg)
    assert "17900000000000002" in led2.imported_media               # SURVIVED the round-trip
    got = led2.imported_media["17900000000000002"]
    assert got.permalink == "https://www.instagram.com/reel/XyZ/"
    assert got.product_type == "FEED"
    assert got.metrics == {"reach": 1234, "likes": 56}
    assert got.metrics_series and got.metrics_series[0]["reach"] == 1234


def test_add_imported_media_upserts_by_media_id(tmp_path):
    # UPSERT by media_id: re-adding the same media_id OVERWRITES (the current live snapshot, not a growing
    # history) — never duplicates. (Mirrors add_selection_fact's overwrite, not render's first-write dedup:
    # a live re-pull carries fresher metrics that must win.)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_imported_media(ImportedMedia(media_id="mid1", permalink="https://ig/p/1/", metrics={"reach": 1}))
        led.add_imported_media(ImportedMedia(media_id="mid1", permalink="https://ig/p/1/", metrics={"reach": 999}))
    led2 = Ledger.load(cfg)
    assert list(led2.imported_media) == ["mid1"]                    # exactly one row (no dup)
    assert led2.imported_media["mid1"].metrics == {"reach": 999}    # latest snapshot won


def test_v9_migration_preserves_all_other_maps(tmp_path):
    # The v9->v10 step is additive ONLY — it must not disturb any sibling map (renders/selection_facts/
    # account_selections/batches/stitch_plans all round-trip untouched alongside the new one).
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="x.mp4", state=SourceState.catalogued))
    raw = json.loads(cfg.ledger_path.read_text())
    raw["schema_version"] = 9; raw.pop("imported_media", None)      # simulate a v9 ledger
    cfg.ledger_path.write_text(json.dumps(raw))
    led = Ledger.load(cfg)
    assert led.imported_media == {} and "s1" in led.sources
    assert led.renders == {}
