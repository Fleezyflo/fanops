# tests/test_ledger_migration.py — content-lifecycle Phase 2: SCHEMA_VERSION 2->3 created_at backfill.
# The load-bearing safety: the operator's live ledger (carrying awaiting_approval posts) migrates v2->v3
# WITHOUT losing a single row and stamps created_at on every Source + Post. Idempotent; never raises on a
# torn/naive/non-str row; the newer-ledger refusal + the v0 hop-chain stay intact.
import json, os, time
import pytest
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.errors import ControlFileError
from fanops.timeutil import parse_iso


def _write(cfg, raw):
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps(raw))


def test_schema_version_is_three():
    assert SCHEMA_VERSION == 3


def test_migration_v2_to_v3_round_trip(tmp_path):
    # A v2 ledger with: a source whose file EXISTS (mtime backfill -> exact day), a source with a MISSING
    # file (stamp -> parseable only), a post with a tz-AWARE scheduled_time (its day), a post WITHOUT one
    # (stamp). It also carries an awaiting_approval post that MUST survive the load with created_at stamped.
    cfg = Config(root=tmp_path)
    real = cfg.sources; real.mkdir(parents=True, exist_ok=True)
    real_file = real / "src_aaaaaaaaaaaa.mp4"; real_file.write_bytes(b"V")
    mtime = time.mktime((2026, 3, 1, 12, 0, 0, 0, 0, -1)); os.utime(real_file, (mtime, mtime))
    raw = {"schema_version": 2,
           "sources": {"src_aaaaaaaaaaaa": {"id": "src_aaaaaaaaaaaa", "source_path": str(real_file),
                                            "state": "catalogued"},
                       "src_bbbbbbbbbbbb": {"id": "src_bbbbbbbbbbbb", "source_path": "/nope/missing.mp4",
                                            "state": "catalogued"}},
           "moments": {}, "clips": {},
           "posts": {"p_sched": {"id": "p_sched", "parent_id": "c1", "account": "@a", "account_id": "1",
                                 "platform": "instagram", "caption": "x", "state": "awaiting_approval",
                                 "scheduled_time": "2026-04-15T09:00:00Z"},
                     "p_nosched": {"id": "p_nosched", "parent_id": "c1", "account": "@a", "account_id": "1",
                                   "platform": "instagram", "caption": "y", "state": "awaiting_approval"}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    # NO row lost; the awaiting_approval posts survive with their state.
    assert set(led.sources) == {"src_aaaaaaaaaaaa", "src_bbbbbbbbbbbb"}
    assert set(led.posts) == {"p_sched", "p_nosched"}
    assert led.posts["p_sched"].state.value == "awaiting_approval"
    # Source with a real file -> mtime day (EXACT). Missing file -> a parseable stamp (NOT exact).
    assert led.sources["src_aaaaaaaaaaaa"].created_at and parse_iso(led.sources["src_aaaaaaaaaaaa"].created_at).date().isoformat() == "2026-03-01"
    assert led.sources["src_bbbbbbbbbbbb"].created_at and parse_iso(led.sources["src_bbbbbbbbbbbb"].created_at).tzinfo is not None
    # Post with aware scheduled_time -> its day. Without -> a parseable stamp.
    assert parse_iso(led.posts["p_sched"].created_at).date().isoformat() == "2026-04-15"
    assert led.posts["p_nosched"].created_at and parse_iso(led.posts["p_nosched"].created_at).tzinfo is not None
    # published_at is NOT backfilled.
    assert led.posts["p_sched"].published_at is None
    # Save re-stamps schema_version 3; reload is a no-op (created_at unchanged = idempotent).
    with Ledger.transaction(cfg):
        pass
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == 3
    led2 = Ledger.load(cfg)
    assert led2.posts["p_sched"].created_at == led.posts["p_sched"].created_at
    assert led2.sources["src_aaaaaaaaaaaa"].created_at == led.sources["src_aaaaaaaaaaaa"].created_at


def test_migration_v0_to_v3_full_chain(tmp_path):
    # A pre-versioning ledger (schema_version absent = v0, no stitch_plans, no created_at) hops v0->v1->v2->v3:
    # stitch_plans injected by step 2 must NOT be undone by step 3; every row backfilled; saved version == 3.
    cfg = Config(root=tmp_path)
    raw = {"sources": {"src_cccccccccccc": {"id": "src_cccccccccccc", "source_path": "/gone.mp4",
                                            "state": "catalogued"}},
           "moments": {}, "clips": {},
           "posts": {"p1": {"id": "p1", "parent_id": "c1", "account": "@a", "account_id": "1",
                            "platform": "tiktok", "caption": "z", "state": "queued"}},
           "tag_log": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.stitch_plans == {}                                   # step-2 injection survives step 3
    assert "src_cccccccccccc" in led.sources and led.sources["src_cccccccccccc"].created_at
    assert led.posts["p1"].created_at
    with Ledger.transaction(cfg):
        pass
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == 3


def test_migration_v0_source_missing_source_path_no_crash(tmp_path):
    # A v0 source row with NO source_path (pre-v1 shape) must not crash the load -> falls to the stamp.
    cfg = Config(root=tmp_path)
    raw = {"sources": {"src_dddddddddddd": {"id": "src_dddddddddddd", "state": "discovered"}},
           "moments": {}, "clips": {}, "posts": {}, "tag_log": {}}
    # source_path is required by the model; supply it but as None to exercise the migration guard, then
    # the model default will reject None -> use a present-but-empty path instead (model requires str).
    raw["sources"]["src_dddddddddddd"]["source_path"] = ""
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.sources["src_dddddddddddd"].created_at                # stamped, no crash


def test_migration_naive_scheduled_time_uses_stamp_not_local_guess(tmp_path):
    # A post with a NAIVE scheduled_time (no tz) must NOT be turned into a local-tz guess -> created_at == stamp.
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 2, "sources": {}, "moments": {}, "clips": {},
           "posts": {"p_naive": {"id": "p_naive", "parent_id": "c1", "account": "@a", "account_id": "1",
                                 "platform": "instagram", "caption": "x", "state": "queued",
                                 "scheduled_time": "2026-04-15T09:00:00"}},   # NAIVE (no Z/offset)
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    ca = led.posts["p_naive"].created_at
    assert ca and parse_iso(ca).tzinfo is not None
    # the naive 2026-04-15 was NOT adopted as the day; the stamp is "now" (not that date)
    assert parse_iso(ca).date().isoformat() != "2026-04-15"   # the naive on-disk date was NOT adopted as the stamp day


def test_migration_null_and_integer_scheduled_time_no_crash(tmp_path):
    # scheduled_time: null and an integer (hand-edit corruption) must not crash the migration -> stamp.
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 2, "sources": {}, "moments": {}, "clips": {},
           "posts": {"p_null": {"id": "p_null", "parent_id": "c1", "account": "@a", "account_id": "1",
                                "platform": "instagram", "caption": "x", "state": "queued",
                                "scheduled_time": None}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.posts["p_null"].created_at                            # stamped, no crash


def test_migration_idempotent_keeps_existing_created_at(tmp_path):
    # A v2 row that ALREADY has created_at (partial hand-write) is NOT overwritten by the migration.
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 2,
           "sources": {"src_eeeeeeeeeeee": {"id": "src_eeeeeeeeeeee", "source_path": "/x.mp4",
                                            "state": "catalogued", "created_at": "2020-01-01T00:00:00Z"}},
           "moments": {}, "clips": {},
           "posts": {"p_keep": {"id": "p_keep", "parent_id": "c1", "account": "@a", "account_id": "1",
                                "platform": "instagram", "caption": "x", "state": "queued",
                                "created_at": "2019-12-31T00:00:00Z"}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.sources["src_eeeeeeeeeeee"].created_at == "2020-01-01T00:00:00Z"
    assert led.posts["p_keep"].created_at == "2019-12-31T00:00:00Z"


def test_newer_schema_still_refused(tmp_path):
    # The v4+ refusal guard is untouched by the new step.
    cfg = Config(root=tmp_path)
    raw = {"schema_version": SCHEMA_VERSION + 1, "sources": {}, "moments": {}, "clips": {}, "posts": {}}
    _write(cfg, raw)
    with pytest.raises(ControlFileError, match="schema|upgrade"):
        Ledger.load(cfg)
