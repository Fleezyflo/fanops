# tests/test_account_selection_migration.py — RF1 Task 4: the v8->v9 migration that lifts the legacy,
# non-durable Moment.affinities into durable AccountSelection rows WITHOUT losing a row or fabricating a
# "chosen" provenance. Decided default: an EMPTY-affinities moment mints NO record (the gate falls back to
# affinity_admits -> the legacy fan-to-all is preserved byte-for-byte). A non-empty affinities row mints one
# AccountSelection per (source, account), labelled `llm` ONLY when a durable SelectionFact corroborates it,
# else `migrated` (a false "chosen" badge would re-open the credibility gap RF1 closes). Idempotent; the
# ledger is NEVER wiped.
import json
from fanops.config import Config
from fanops.ledger import Ledger, SCHEMA_VERSION
from fanops.models import SelectionMethod, account_selection_id


def _write(cfg, raw):
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps(raw))

def _v8(moments, selection_facts=None):
    return {"schema_version": 8,
            "sources": {"src_1": {"id": "src_1", "source_path": "/s.mp4", "state": "catalogued"}},
            "moments": moments, "clips": {}, "posts": {},
            "tag_log": {}, "variant_streaks": {}, "stitch_plans": {}, "batches": {}, "renders": {},
            "selection_facts": selection_facts or {}}

def _mom(mid, affinities):
    return {"id": mid, "parent_id": "src_1", "content_token": mid, "start": 0, "end": 7,
            "reason": "r", "state": "decided", "affinities": affinities}


def test_migration_v8_to_v9_nonempty_affinities_mints_migrated(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg, _v8({"m1": _mom("m1", ["@a"]), "m2": _mom("m2", ["@a", "@b"])}))
    led = Ledger.load(cfg)
    sa = led.account_selection_for("src_1", "@a")
    assert sa is not None and sa.moment_ids == ["m1", "m2"] and sa.method == SelectionMethod.migrated
    sb = led.account_selection_for("src_1", "@b")
    assert sb is not None and sb.moment_ids == ["m2"] and sb.method == SelectionMethod.migrated


def test_migration_v8_to_v9_llm_only_when_a_fact_corroborates(tmp_path):
    # a durable SelectionFact (source, @a, llm) corroborates -> @a labelled llm; @b has no fact -> migrated.
    cfg = Config(root=tmp_path)
    facts = {"selfact_x": {"id": "selfact_x", "moment_id": "m1", "account": "@a", "method": "llm",
                           "source_id": "src_1"}}
    _write(cfg, _v8({"m1": _mom("m1", ["@a", "@b"])}, selection_facts=facts))
    led = Ledger.load(cfg)
    assert led.account_selection_for("src_1", "@a").method == SelectionMethod.llm
    assert led.account_selection_for("src_1", "@b").method == SelectionMethod.migrated


def test_migration_v8_to_v9_empty_affinities_mints_no_record(tmp_path):
    # decided default: empty affinities -> NO AccountSelection -> the gate fan-to-all (legacy) is preserved.
    cfg = Config(root=tmp_path)
    _write(cfg, _v8({"m1": _mom("m1", [])}))
    led = Ledger.load(cfg)
    assert led.selections_of_source("src_1") == []
    assert led.account_selection_for("src_1", "@a") is None


def test_migration_v8_to_v9_round_trip_loses_no_row_and_stamps_version(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg, _v8({"m1": _mom("m1", ["@a"])}))
    led = Ledger.load(cfg)
    assert set(led.moments) == {"m1"} and "src_1" in led.sources       # no row lost
    with Ledger.transaction(cfg):
        pass
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == SCHEMA_VERSION


def test_migration_v8_to_v9_is_idempotent(tmp_path):
    cfg = Config(root=tmp_path)
    _write(cfg, _v8({"m1": _mom("m1", ["@a"])}))
    led1 = Ledger.load(cfg)
    with Ledger.transaction(cfg):                                       # save at v9
        pass
    led2 = Ledger.load(cfg)                                             # reload (no re-migration at v9)
    assert {k: v.model_dump() for k, v in led1.account_selections.items()} == \
           {k: v.model_dump() for k, v in led2.account_selections.items()}
    assert account_selection_id("src_1", "@a") in led2.account_selections


def test_migration_step_never_raises_on_torn_row():
    # the migration is a PURE dict transform that runs BEFORE unit construction — it must never raise on a
    # torn raw shape (non-dict moment, None affinities, missing parent_id) regardless of load's later
    # validation. Tested at the function boundary (mirrors _migrate_v4's never-raise guarantee).
    from fanops.ledger import _migrate_v8_account_selections
    raw = {"moments": {"m1": _mom("m1", ["@a"]), "m_bad": "not-a-dict",
                       "m_none": {"id": "m_none", "parent_id": "src_1", "affinities": None},
                       "m_noparent": {"id": "m_np", "affinities": ["@z"]}},
           "selection_facts": {}}
    out = _migrate_v8_account_selections(raw)                           # must not raise
    sel = out["account_selections"]
    asid = account_selection_id("src_1", "@a")
    assert asid in sel and sel[asid]["moment_ids"] == ["m1"]
    assert account_selection_id("src_1", "@z") not in sel              # missing parent_id -> skipped
