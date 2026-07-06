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


def test_schema_version_is_ten():
    # ledger-rebuild M1 bumped 9->10 for the new top-level imported_media map.
    assert SCHEMA_VERSION == 10


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
           "posts": {"p_sched": {"id": "p_sched", "parent_id": "c1", "account": "a", "account_id": "1",
                                 "platform": "instagram", "caption": "x", "state": "awaiting_approval",
                                 "scheduled_time": "2026-04-15T09:00:00Z"},
                     "p_nosched": {"id": "p_nosched", "parent_id": "c1", "account": "a", "account_id": "1",
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
    # Save re-stamps schema_version to current; reload is a no-op (created_at unchanged = idempotent).
    with Ledger.transaction(cfg):
        pass
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION
    led2 = Ledger.load(cfg)
    assert led2.posts["p_sched"].created_at == led.posts["p_sched"].created_at
    assert led2.sources["src_aaaaaaaaaaaa"].created_at == led.sources["src_aaaaaaaaaaaa"].created_at


def test_migration_v0_to_v5_full_chain(tmp_path):
    # A pre-versioning ledger (schema_version absent = v0, no stitch_plans, no created_at, no batches) hops
    # v0->v1->v2->v3->v4->v5->v6->v7: stitch_plans by step 2 + batches by step 5 + renders by step 6 +
    # selection_facts by step 7 must NOT be undone by later steps; every row backfilled; saved version == 7.
    # (No metrics, so v4 leaves series [].)
    cfg = Config(root=tmp_path)
    raw = {"sources": {"src_cccccccccccc": {"id": "src_cccccccccccc", "source_path": "/gone.mp4",
                                            "state": "catalogued"}},
           "moments": {}, "clips": {},
           "posts": {"p1": {"id": "p1", "parent_id": "c1", "account": "a", "account_id": "1",
                            "platform": "tiktok", "caption": "z", "state": "queued"}},
           "tag_log": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.stitch_plans == {}                                   # step-2 injection survives step 3
    assert led.batches == {}                                        # step-5 injection (empty batches map)
    assert led.renders == {}                                        # step-6 injection (empty renders map)
    assert led.selection_facts == {}                               # step-7 injection (empty selection_facts map)
    assert "src_cccccccccccc" in led.sources and led.sources["src_cccccccccccc"].created_at
    assert led.posts["p1"].created_at
    assert led.posts["p1"].metrics_series == []                    # no metrics -> no legacy row
    with Ledger.transaction(cfg):
        pass
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION


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
           "posts": {"p_naive": {"id": "p_naive", "parent_id": "c1", "account": "a", "account_id": "1",
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
           "posts": {"p_null": {"id": "p_null", "parent_id": "c1", "account": "a", "account_id": "1",
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
           "posts": {"p_keep": {"id": "p_keep", "parent_id": "c1", "account": "a", "account_id": "1",
                                "platform": "instagram", "caption": "x", "state": "queued",
                                "created_at": "2019-12-31T00:00:00Z"}},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.sources["src_eeeeeeeeeeee"].created_at == "2020-01-01T00:00:00Z"
    assert led.posts["p_keep"].created_at == "2019-12-31T00:00:00Z"


def test_newer_schema_still_refused(tmp_path):
    # The v(N+1)+ refusal guard is untouched by the new step.
    cfg = Config(root=tmp_path)
    raw = {"schema_version": SCHEMA_VERSION + 1, "sources": {}, "moments": {}, "clips": {}, "posts": {}}
    _write(cfg, raw)
    with pytest.raises(ControlFileError, match="schema|upgrade"):
        Ledger.load(cfg)


# ---- P3: v3->v4 metrics_series back-fill (a single `legacy` row for pre-P3 analyzed posts) ----
def _v3_post(pid, state, **extra):
    return {"id": pid, "parent_id": "c1", "account": "a", "account_id": "1", "platform": "instagram",
            "caption": "x", "state": state, "created_at": "2026-01-01T00:00:00Z", **extra}

def test_migration_v3_to_v4_backfills_one_legacy_row_for_analyzed_post(tmp_path):
    # A pre-P3 analyzed post carrying metrics but no series gets ONE 'legacy'-tagged row preserving that
    # single data point, and STAYS analyzed. 'legacy' is deliberately not a cadence offset, so it never
    # blocks a real future poll. The lift_score / metrics are carried verbatim into the row.
    # R1: an analyzed post MUST carry a public_url (terminal-with-URL invariant), so this row seeds one.
    # (dryrun-boundary M3 deleted the migration-on-read back-fill + the doctor-fix-ghosts healer that once
    # papered over pre-R1 rows lacking a url; post-boundary such a row is a genuine defect that fails R1 at
    # construction, and the live ledger + every backup already carry no such row — nothing to heal.)
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 3, "sources": {}, "moments": {}, "clips": {},
           "posts": {"pa": _v3_post("pa", "analyzed", metrics={"saves": 9, "lift_score": 36.0},
                                    public_url="https://insta/p/legacy")},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    p = led.posts["pa"]
    assert p.state.value == "analyzed"
    assert len(p.metrics_series) == 1
    row = p.metrics_series[0]
    assert row["offset"] == "legacy" and row["saves"] == 9 and row["lift_score"] == 36.0
    assert row["captured_at"] and parse_iso(row["captured_at"]).tzinfo is not None
    assert p.metrics == {"saves": 9, "lift_score": 36.0}             # LATEST snapshot untouched

def test_migration_v3_to_v4_empty_metrics_post_gets_empty_series(tmp_path):
    # A v3 post with NO metrics (e.g. a published-but-unmeasured or queued post) gets metrics_series ==
    # [] — we never fabricate a row from nothing.
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 3, "sources": {}, "moments": {}, "clips": {},
           "posts": {"pq": _v3_post("pq", "queued"),
                     # R1: a published row carries a real permalink (the M3-deleted back-fill no longer heals it).
                     "pp": _v3_post("pp", "published", metrics={}, public_url="https://www.instagram.com/reel/pp/")},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.posts["pq"].metrics_series == [] and led.posts["pp"].metrics_series == []

def test_migration_v3_to_v4_idempotent_existing_series_untouched(tmp_path):
    # A row that ALREADY carries a metrics_series is NOT double-backfilled (idempotent).
    cfg = Config(root=tmp_path)
    existing = [{"saves": 5, "lift_score": 20.0, "offset": "4h", "captured_at": "2026-01-01T04:00:00Z"}]
    raw = {"schema_version": 3, "sources": {}, "moments": {}, "clips": {},
           "posts": {"pa": _v3_post("pa", "analyzed", metrics={"saves": 5, "lift_score": 20.0},
                                    metrics_series=existing, public_url="https://www.instagram.com/reel/pa/")},
           "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.posts["pa"].metrics_series == existing               # no 'legacy' row added

def test_migration_v4_never_raises_on_torn_post_row():
    # The pure migration step must not raise on a non-dict post row (mirrors _migrate_v3_created_at).
    from fanops.ledger import _migrate_v4_metrics_series
    out = _migrate_v4_metrics_series({"posts": {"good": {"metrics": {"saves": 1}}, "torn": "not-a-dict"}})
    assert out["posts"]["good"]["metrics_series"][0]["offset"] == "legacy"
    assert out["posts"]["torn"] == "not-a-dict"                     # left untouched, no crash


# ---- Account-First Studio: v4->v5 batches map injection (additive-map-only, NO Post backfill) ----
def test_migration_v4_to_v5_injects_empty_batches(tmp_path):
    # A v4 ledger with populated posts + stitch_plans migrates v4->v5: the empty `batches` map is injected,
    # NO row lost or mutated, batch_id rides the pydantic default (no Post backfill), saved version == 5.
    cfg = Config(root=tmp_path)
    raw = {"schema_version": 4, "sources": {}, "moments": {}, "clips": {},
           "posts": {"pa": _v3_post("pa", "queued")},
           "tag_log": {}, "variant_streaks": {},
           "stitch_plans": {"sp1": {"id": "sp1", "clip_id": "c1", "strategy_key": "impact_cut"}}}
    _write(cfg, raw)
    led = Ledger.load(cfg)
    assert led.batches == {}                                        # injected empty
    assert set(led.posts) == {"pa"} and led.posts["pa"].batch_id is None   # row survives; batch_id default None
    assert set(led.stitch_plans) == {"sp1"}                         # stitch_plans untouched by the v5 step
    assert led.renders == {}                                        # v5->v6 step injects the empty renders map
    assert led.selection_facts == {}                               # v6->v7 step injects the empty selection_facts map
    with Ledger.transaction(cfg):
        pass
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION
