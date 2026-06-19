# tests/test_ledger.py
import json
from fanops.config import Config
from fanops.models import Source, Moment, Clip, Post, PostState, SourceState, ClipState, Platform
from fanops.ledger import Ledger

def test_empty(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    assert led.sources == {} and led.moments == {} and led.clips == {} and led.posts == {}

def test_roundtrip(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path="/x.mp4", sha256="d"))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="0-5", start=0, end=5, reason="r"))
    led.add_clip(Clip(id="clip_1", parent_id="mom_1", path="/c.mp4"))
    led.add_post(Post(id="post_1", parent_id="clip_1", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x"))
    led.save()
    again = Ledger.load(cfg)
    assert again.sources["src_1"].sha256 == "d"
    assert again.moments["mom_1"].reason == "r"
    assert again.posts["post_1"].platform is Platform.instagram

def test_save_is_atomic_no_partial(tmp_path):
    # After save, the file is valid JSON (temp+replace guarantees no partial doc).
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/x"))
    led.save()
    json.loads(cfg.ledger_path.read_text())   # must not raise

def test_add_idempotent(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_source(Source(id="src_1", source_path="/x.mp4"))
    led.add_source(Source(id="src_1", source_path="/x.mp4"))
    assert len(led.sources) == 1

def test_already_seen_by_sha(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_source(Source(id="src_1", source_path="/x.mp4", sha256="d"))
    assert led.already_seen(sha256="d") and not led.already_seen(sha256="e")

def test_reconcile_moments_upserts_and_deletes_cascade(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/x"))
    # two moments + a clip + a post hanging off the first moment
    led.add_moment(Moment(id="m_a", parent_id="s", content_token="A", start=0, end=2, reason="a"))
    led.add_moment(Moment(id="m_b", parent_id="s", content_token="B", start=3, end=5, reason="b"))
    led.add_clip(Clip(id="c_a", parent_id="m_a", path="/c"))
    # a REJECTED post (a deletable state — NOT a protected awaiting/queued/retired worklist) so the
    # cascade still deletes A's lineage; protected-state survival is covered in test_ledger_cascade_protect.
    led.add_post(Post(id="p_a", parent_id="c_a", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.rejected))
    # new decision keeps B, drops A, adds C
    keep = {"m_b": Moment(id="m_b", parent_id="s", content_token="B", start=3, end=5, reason="b2"),
            "m_c": Moment(id="m_c", parent_id="s", content_token="C", start=6, end=8, reason="c")}
    led.reconcile_moments("s", keep)
    assert set(m for m in led.moments) == {"m_b", "m_c"}        # A gone
    assert led.moments["m_b"].reason == "b2"                    # B updated in place
    assert "c_a" not in led.clips and "p_a" not in led.posts    # cascade deleted A's lineage

def test_reconcile_preserves_clean_awaiting_strategy_moment(tmp_path):
    # M2: a clean clip reserved for a not-yet-built strategy (clean_awaiting_strategy:<key>) must NOT be
    # GC'd when a re-decision drops it from the keep-set — else the future strategy it was reserved for
    # finds nothing. A plain dropped moment IS still cascade-deleted (control).
    from fanops.models import MomentState
    from fanops.router import awaiting
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/x"))
    led.add_moment(Moment(id="m_await", parent_id="s", content_token="A", start=0, end=2,
                          reason="a", hook_strategy=awaiting("impact_cut")))   # reserved -> preserve
    led.add_moment(Moment(id="m_plain", parent_id="s", content_token="B", start=3, end=5, reason="b"))  # control
    led.reconcile_moments("s", {})                              # empty keep-set: would delete all of s's moments
    assert "m_await" in led.moments                             # reserved moment SURVIVES GC
    assert led.moments["m_await"].state is MomentState.decided  # still routable for a future strategy
    assert "m_plain" not in led.moments                         # control: plain moment cascade-deleted

# ---- M3 (structural-hooks): stitch_plan lifecycle + in-lock idempotent approval ----
def test_add_stitch_plan_idempotent_by_id(tmp_path):
    from fanops.models import StitchPlan
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_stitch_plan(StitchPlan(id="sp1", clip_id="c1", strategy_key="impact_cut"))
    led.add_stitch_plan(StitchPlan(id="sp1", clip_id="c1", strategy_key="intro_tease"))  # same id -> dedup
    assert len(led.stitch_plans) == 1 and led.stitch_plans["sp1"].strategy_key == "impact_cut"

def test_approve_stitch_plan_idempotent(tmp_path):
    from fanops.models import StitchPlan, StitchState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_stitch_plan(StitchPlan(id="sp1", clip_id="c1", strategy_key="impact_cut"))
    led.approve_stitch_plan("sp1"); assert led.stitch_plans["sp1"].state is StitchState.approved
    led.approve_stitch_plan("sp1"); assert led.stitch_plans["sp1"].state is StitchState.approved  # no 2nd transition

def test_approve_does_not_revive_a_dismissed_plan(tmp_path):
    from fanops.models import StitchPlan, StitchState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_stitch_plan(StitchPlan(id="sp1", clip_id="c1", strategy_key="impact_cut"))
    led.dismiss_stitch_plan("sp1"); assert led.stitch_plans["sp1"].state is StitchState.dismissed
    led.approve_stitch_plan("sp1"); assert led.stitch_plans["sp1"].state is StitchState.dismissed  # terminal, in-lock re-check

def test_dismiss_stitch_plan(tmp_path):
    from fanops.models import StitchPlan, StitchState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_stitch_plan(StitchPlan(id="sp1", clip_id="c1", strategy_key="impact_cut"))
    led.dismiss_stitch_plan("sp1"); assert led.stitch_plans["sp1"].state is StitchState.dismissed

def test_cascade_preserves_needs_reconcile_post(tmp_path):
    # AUDIT C1: a needs_reconcile post MAY be live on the platform (ambiguous publish). If its
    # moment is dropped by a re-decision, the cascade must NOT delete it — that would orphan a
    # possibly-live post (untrackable, the exact class C1 guards against). A dropped moment with a
    # needs_reconcile descendant is RETIRED, not erased — same treatment as published/submitting.
    from fanops.models import Post, PostState, MomentState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="s", source_path="/x"))
    led.add_moment(Moment(id="m_r", parent_id="s", content_token="R", start=0, end=2, reason="r"))
    led.add_clip(Clip(id="c_r", parent_id="m_r", path="/c", state=ClipState.queued))
    led.add_post(Post(id="p_r", parent_id="c_r", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.needs_reconcile))
    led._delete_moment_cascade("m_r")
    assert "p_r" in led.posts, "a possibly-live needs_reconcile post must survive the cascade"
    assert led.moments["m_r"].state is MomentState.retired   # moment suppressed, not erased

def test_retired_lineage_is_queryable(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_clip(Clip(id="c1", parent_id="m1", path="/c"))
    led.retire_clip("c1")
    assert led.is_retired_clip("c1")
    assert led.clips["c1"].state is ClipState.retired

def test_set_state_typed(tmp_path):
    led = Ledger.load(Config(root=tmp_path))
    led.add_source(Source(id="src_1", source_path="/x.mp4"))
    led.set_source_state("src_1", SourceState.transcribed)
    assert led.sources["src_1"].state is SourceState.transcribed


def test_reconcile_does_not_unretire_a_retired_moment(tmp_path):
    # AUDIT M1: reconcile_moments' upsert overwrote self.moments[mid] unconditionally. If `keep`
    # carried a moment whose existing copy is `retired` (set by adjust.retire), a fresh `decided`
    # copy resurrected it -> re-rendered, re-posted, undoing the retirement. Guard: skip the upsert
    # when the prior moment is MomentState.retired.
    from fanops.models import MomentState
    led = Ledger.load(Config(root=tmp_path))
    led.add_moment(Moment(id="m1", parent_id="s1", content_token="1-2", start=1, end=2,
                          reason="r", state=MomentState.retired))   # already retired by adjust
    # a fresh decision tries to upsert the same id as `decided`
    keep = {"m1": Moment(id="m1", parent_id="s1", content_token="1-2", start=1, end=2,
                         reason="r", state=MomentState.decided)}
    led.reconcile_moments("s1", keep)
    assert led.moments["m1"].state is MomentState.retired   # stays retired, not resurrected


def test_reconcile_still_updates_a_non_retired_moment(tmp_path):
    # The retire guard must NOT block legitimate re-decision of a NON-retired moment: a `decided`
    # (or any non-retired) prior moment must still be upserted/updated by reconcile.
    from fanops.models import MomentState
    led = Ledger.load(Config(root=tmp_path))
    led.add_moment(Moment(id="m2", parent_id="s2", content_token="1-2", start=1, end=2,
                          reason="old", state=MomentState.decided))
    keep = {"m2": Moment(id="m2", parent_id="s2", content_token="3-4", start=3, end=4,
                         reason="new", state=MomentState.decided)}
    led.reconcile_moments("s2", keep)
    assert led.moments["m2"].reason == "new"          # updated in place (not blocked by the guard)
    assert led.moments["m2"].start == 3


def test_variant_streaks_roundtrips_and_defaults_empty(tmp_path):
    cfg = Config(root=tmp_path)
    led = Ledger.load(cfg)
    assert led.variant_streaks == {}                      # default empty on a fresh ledger
    led.variant_streaks["@a|instagram"] = {"hook": "WIN", "fingerprint": "abc", "streak": 2}
    led.save()
    led2 = Ledger.load(cfg)
    assert led2.variant_streaks == {"@a|instagram": {"hook": "WIN", "fingerprint": "abc", "streak": 2}}


def test_old_ledger_without_variant_streaks_loads(tmp_path):
    # An older ledger.json that predates v3 has no "variant_streaks" key -> must load as {} (no crash).
    cfg = Config(root=tmp_path)
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps({"sources": {}, "moments": {}, "clips": {}, "posts": {}}))
    led = Ledger.load(cfg)
    assert led.variant_streaks == {}


# ---- M1 (structural-hooks): retire_source (cascade, file kept) + rebuild_catalog ----
def test_retire_source_cascades_non_live_children(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_x", source_path="/x.mp4", sha256="d"))
    led.add_moment(Moment(id="m", parent_id="src_x", content_token="A", start=0, end=2, reason="a"))
    led.add_clip(Clip(id="c", parent_id="m", path="/c.mp4", state=ClipState.rendered))
    led.retire_source("src_x")
    assert led.is_retired_source("src_x") and led.sources["src_x"].state is SourceState.retired
    assert "m" not in led.moments and "c" not in led.clips          # non-live lineage cascade-dropped

def test_retire_source_preserves_live_descendants(tmp_path):
    from fanops.models import PostState, MomentState
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    led.add_source(Source(id="src_y", source_path="/y.mp4", sha256="e"))
    led.add_moment(Moment(id="m", parent_id="src_y", content_token="A", start=0, end=2, reason="a"))
    led.add_clip(Clip(id="c", parent_id="m", path="/c.mp4", state=ClipState.published))
    led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1",
                      platform=Platform.instagram, caption="x", state=PostState.published))
    led.retire_source("src_y")
    assert led.is_retired_source("src_y")
    assert led.moments["m"].state is MomentState.retired           # kept but suppressed (live descendant)
    assert "c" in led.clips and "p" in led.posts                   # the performance record survives

def test_retire_source_leaves_file_on_disk(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.sources / "src_0123456789ab.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_source(Source(id="src_0123456789ab", source_path=str(f), sha256="d"))
    led.retire_source("src_0123456789ab")
    assert f.exists()                                              # file kept — a live media_url may point at it

def test_rebuild_surfaces_orphan_as_discovered(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.sources.mkdir(parents=True, exist_ok=True)
    (cfg.sources / "src_aabbccddeeff.mp4").write_bytes(b"V")        # on disk, no ledger row
    led.rebuild_catalog(cfg)
    assert led.sources["src_aabbccddeeff"].state is SourceState.discovered

def test_rebuild_skips_non_source_files(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.sources.mkdir(parents=True, exist_ok=True)
    for name in (".gitkeep", ".DS_Store", "notes.txt", "random.mp4"):   # junk / non-src-named
        (cfg.sources / name).write_bytes(b"x")
    led.rebuild_catalog(cfg)
    assert led.sources == {}                                       # nothing bogus catalogued

def test_rebuild_never_resurrects_retired(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    f = cfg.sources / "src_0123456789ab.mp4"; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"V")
    led.add_source(Source(id="src_0123456789ab", source_path=str(f), sha256="d", state=SourceState.retired))
    led.rebuild_catalog(cfg)
    assert led.sources["src_0123456789ab"].state is SourceState.retired   # not flipped to discovered

def test_rebuild_idempotent_and_keeps_missing_file_sources(tmp_path):
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.sources.mkdir(parents=True, exist_ok=True)
    led.add_source(Source(id="src_111111111111", source_path="/gone.mp4", sha256="d",   # file missing on disk
                          state=SourceState.catalogued))
    (cfg.sources / "src_aabbccddeeff.mp4").write_bytes(b"V")
    led.rebuild_catalog(cfg); first = set(led.sources)
    led.rebuild_catalog(cfg)                                       # re-run is a no-op
    assert set(led.sources) == first
    assert led.sources["src_111111111111"].state is SourceState.catalogued   # missing-file source NOT dropped
    assert led.sources["src_aabbccddeeff"].state is SourceState.discovered

def test_rebuild_discovered_has_created_at(tmp_path):
    # content-lifecycle Phase 2 (H4): a discovered orphan carries created_at == its file's mtime DAY, so it
    # isn't silently "undated" in the Phase-3 day-bucketing. Parseable + aware.
    import os, time
    from fanops.timeutil import parse_iso
    cfg = Config(root=tmp_path); led = Ledger.load(cfg)
    cfg.sources.mkdir(parents=True, exist_ok=True)
    f = cfg.sources / "src_ffeeddccbbaa.mp4"; f.write_bytes(b"V")
    mtime = time.mktime((2026, 2, 14, 12, 0, 0, 0, 0, -1)); os.utime(f, (mtime, mtime))
    led.rebuild_catalog(cfg)
    s = led.sources["src_ffeeddccbbaa"]
    assert s.created_at and parse_iso(s.created_at).tzinfo is not None
    assert parse_iso(s.created_at).date().isoformat() == "2026-02-14"
