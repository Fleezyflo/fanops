# tests/test_selection_facts.py — M4a: the DURABLE per-account SELECTION FACT (which account got which moment
# and WHY — the selector's audit trail) + the additive v6->v7 migration + the account/moment-keyed accessors.
# Casting writes only Moment.affinities (handles, NON-durable: reset each re-decision); a SelectionFact persists
# that decision and its why. One fact per (moment, account), content-addressed -> a re-cast OVERWRITES (current
# selection, not a growing history). Old ledgers load with selection_facts={} (byte-identical).
import json
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import SelectionFact, Post, Render, Platform, PostState, RenderState
from fanops.ids import child_id


def _fact(moment_id, account, **extra):
    return SelectionFact(id=child_id("selfact", moment_id, account), moment_id=moment_id, account=account, **extra)


def test_selection_fact_defaults():
    f = _fact("m1", "@a")
    assert f.method == "heuristic" and f.reason == "" and f.overlap is None and f.rank is None
    assert f.source_id is None and f.batch_id is None and f.created_at is None

def test_add_get_selection_fact_round_trips(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_selection_fact(_fact("m1", "@a", method="llm", overlap=3, signal=2.0, rank=0,
                                     reason="guitar solo", source_id="src_1", batch_id="b1"))
    led = Ledger.load(cfg)
    f = led.get_selection_fact(child_id("selfact", "m1", "@a"))
    assert f is not None and f.account == "@a" and f.overlap == 3 and f.reason == "guitar solo"
    assert f.source_id == "src_1" and f.batch_id == "b1" and f.rank == 0
    assert f.method == "llm"                                    # the SelectionMethod enum round-trips through save/load

def test_add_selection_fact_overwrites_on_recast(tmp_path):
    # one fact per (moment, account); a re-cast UPDATES the why (latest selection wins), NOT a growing history
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_selection_fact(_fact("m1", "@a", rank=2))
        led.add_selection_fact(_fact("m1", "@a", rank=0))      # same content-addressed id -> overwrite
    led = Ledger.load(cfg)
    assert len(led.selection_facts) == 1
    assert led.get_selection_fact(child_id("selfact", "m1", "@a")).rank == 0

def test_selection_facts_of_account_and_moment(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_selection_fact(_fact("m1", "@a"))
        led.add_selection_fact(_fact("m1", "@b"))
        led.add_selection_fact(_fact("m2", "@a"))
    led = Ledger.load(cfg)
    assert {f.moment_id for f in led.selection_facts_of_account("@a")} == {"m1", "m2"}   # account-keyed lookup
    assert {f.account for f in led.selection_facts_of_moment("m1")} == {"@a", "@b"}      # moment-keyed lookup

def test_posts_and_renders_of_account(tmp_path):
    # the account-keyed accessors the plan calls the 'account index' — direct per-account lookups (scans today)
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_post(Post(id="p_a", parent_id="c1", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval))
        led.add_post(Post(id="p_b", parent_id="c1", account="@b", account_id="2", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval))
        led.add_render(Render(id="r_a", clip_id="c1", account="@a", surface_key="@a/instagram", path="/r.mp4",
                             state=RenderState.rendered))
    led = Ledger.load(cfg)
    assert {p.id for p in led.posts_of_account("@a")} == {"p_a"}
    assert {r.id for r in led.renders_of_account("@a")} == {"r_a"}

def test_v6_ledger_migrates_to_v7_injecting_selection_facts(tmp_path):
    # additive v6->v7: a v6 ledger (no selection_facts key) loads with an empty map, no row lost, idempotent
    cfg = Config(root=tmp_path)
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    raw = {"schema_version": 6,
           "sources": {"src_aaaaaaaaaaaa": {"id": "src_aaaaaaaaaaaa", "source_path": "/s.mp4",
                                            "created_at": "2026-06-01T00:00:00Z"}},
           "moments": {}, "clips": {}, "posts": {}, "tag_log": {}, "variant_streaks": {},
           "stitch_plans": {}, "batches": {}, "renders": {}}
    cfg.ledger_path.write_text(json.dumps(raw))
    led = Ledger.load(cfg)
    assert led.selection_facts == {}                          # v7 injected the empty map
    assert set(led.sources) == {"src_aaaaaaaaaaaa"}           # no row lost across the hop
    with Ledger.transaction(cfg):
        pass                                                  # re-save stamps the new version
    assert json.loads(cfg.ledger_path.read_text())["schema_version"] == 7
