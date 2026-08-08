# tests/test_origin_backfill.py — MOL-756 (T2.2): reconstruct provenance for the moments minted before
# `Moment.origin` existed.
#
# The engine's whole claim is that it labels ONLY what the lineage licenses and says out loud that the
# label was inferred. So these tests pin, in order: the selection (day -> post -> clip -> moment), the
# label (`machine_inferred`, never `machine`), idempotence, the orphan tally, that a dry-run writes
# nothing, that the snapshot is taken BEFORE the mutation, and that every STOP refuses instead of adapting.
# tmp-path fixtures ONLY — nothing here runs against live 00_control.
from fanops.config import Config
from fanops.ledger import Ledger
from fanops.models import (Clip, ClipState, Moment, MomentOrigin, Platform, Post, PostState, Source)
from fanops import origin_backfill
from fanops.origin_backfill import backfill_origin, display_origin, survey_amplify_descended

IN = ["2026-07-29", "2026-07-30"]
OUT_DAY = "2026-07-13"


def _lineage(led, tag: str, *, day: str, source: str = "s1", origin=None):
    """One post -> one clip -> one moment, born on `day`. The shape the real ledger has: strictly 1:1:1."""
    kw = {"origin": origin} if origin is not None else {}
    led.add_moment(Moment(id=f"m{tag}", parent_id=source, content_token=tag, start=0, end=2, reason="r", **kw))
    led.add_clip(Clip(id=f"c{tag}", parent_id=f"m{tag}", path=f"/{tag}.mp4", state=ClipState.queued))
    led.add_post(Post(id=f"p{tag}", parent_id=f"c{tag}", account="a", account_id="1", platform=Platform.instagram,
                      caption="x", state=PostState.awaiting_approval, created_at=f"{day}T10:00:00Z"))


def _seed(cfg, *, extra=None):
    """Two in-window lineages + one out-of-window lineage. `extra` runs inside the same transaction."""
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
        _lineage(led, "A", day=IN[0]); _lineage(led, "B", day=IN[1]); _lineage(led, "Z", day=OUT_DAY)
        if extra:
            extra(led)


# ---- (a) the selection ------------------------------------------------------------------------
def test_labels_exactly_the_in_window_lineages(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    rep = backfill_origin(cfg, days=IN, apply=True)
    assert rep["applied"] and rep["moments"] == 2 and rep["labelled"] == 2
    led = Ledger.load(cfg)
    assert led.moments["mA"].origin is MomentOrigin.machine_inferred
    assert led.moments["mB"].origin is MomentOrigin.machine_inferred
    assert led.moments["mZ"].origin is MomentOrigin.unknown        # out of window: untouched
    assert rep["by_source"] == {"s1": 2}


def test_the_written_label_is_inferred_never_authored(tmp_path):
    # The audit amendment: a reconstructed origin must be distinguishable from one stamped at mint,
    # because the destructive consumer (the purge) has to be able to weigh the difference. The enum member
    # IS that marker — it travels with the row, so no companion flag can drift away from it.
    cfg = Config(root=tmp_path); _seed(cfg)
    backfill_origin(cfg, days=IN, apply=True)
    written = Ledger.load(cfg).moments["mA"].origin
    assert written is MomentOrigin.machine_inferred
    assert written is not MomentOrigin.machine and written is not MomentOrigin.operator
    assert origin_backfill.BACKFILL_ORIGIN is MomentOrigin.machine_inferred


def test_an_authored_origin_is_never_overwritten(tmp_path):
    # An inference may not clobber evidence. The row is counted as skipped, not silently relabelled.
    cfg = Config(root=tmp_path)
    _seed(cfg, extra=lambda led: led.moments.__setitem__(
        "mA", led.moments["mA"].model_copy(update={"origin": MomentOrigin.operator})))
    rep = backfill_origin(cfg, days=IN, apply=True)
    assert rep["skipped_authored"] == 1 and rep["labelled"] == 1
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.operator


# ---- (b) idempotence --------------------------------------------------------------------------
def test_a_second_run_labels_nothing_and_counts_the_first(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    backfill_origin(cfg, days=IN, apply=True)
    again = backfill_origin(cfg, days=IN, apply=True)
    assert again["moments"] == 2 and again["already_labelled"] == 2
    assert again["unlabelled"] == 0 and again["labelled"] == 0


# ---- (c) a missing ancestor is counted, never guessed at ---------------------------------------
def test_a_post_whose_clip_is_missing_lands_in_skipped_orphan(tmp_path):
    cfg = Config(root=tmp_path)
    _seed(cfg, extra=lambda led: led.posts.__setitem__(
        "pOrphan", Post(id="pOrphan", parent_id="c_gone", account="a", account_id="1",
                        platform=Platform.instagram, caption="x", state=PostState.awaiting_approval,
                        created_at=f"{IN[0]}T11:00:00Z")))
    rep = backfill_origin(cfg, days=IN, apply=False)          # raises nothing
    assert rep["skipped_orphan"] == 1 and rep["moments"] == 2
    assert any("missing ancestor" in s for s in rep["stops"])  # and it STOPS: an unwalkable lineage is not licensed


# ---- (d) a dry-run writes nothing --------------------------------------------------------------
def test_dry_run_mutates_nothing(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    before = Ledger.load(cfg)._to_doc()
    rep = backfill_origin(cfg, days=IN, apply=False)
    assert rep["moments"] == 2 and rep["unlabelled"] == 2 and not rep["applied"] and rep["snapshot"] is None
    assert Ledger.load(cfg)._to_doc() == before


# ---- (e) the snapshot precedes the mutation ----------------------------------------------------
def test_restoring_the_returned_snapshot_undoes_every_label(tmp_path):
    # This is step 4's ORDERING contract and nothing else pins it: if the snapshot were taken after the
    # write, restoring it would give the labels back, not take them away.
    cfg = Config(root=tmp_path); _seed(cfg)
    rep = backfill_origin(cfg, days=IN, apply=True)
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.machine_inferred
    Ledger.restore_snapshot(cfg, rep["snapshot"])
    restored = Ledger.load(cfg)
    assert restored.moments["mA"].origin is MomentOrigin.unknown
    assert restored.moments["mB"].origin is MomentOrigin.unknown


# ---- the STOP invariants: each refuses, none adapts --------------------------------------------
def test_a_second_post_on_a_selected_clip_refuses(tmp_path):
    # The 1:1:1 licence broken: a selected clip carrying two posts means the moment behind it produced
    # work the selection did not account for.
    cfg = Config(root=tmp_path)
    _seed(cfg, extra=lambda led: led.add_post(Post(id="pA2", parent_id="cA", account="b", account_id="2",
                                                   platform=Platform.tiktok, caption="x",
                                                   state=PostState.awaiting_approval,
                                                   created_at=f"{IN[0]}T12:00:00Z")))
    rep = backfill_origin(cfg, days=IN, apply=True)
    assert not rep["applied"] and rep["refused"] == "invariant"
    assert any("posts per clip" in s or "1:1:1" in s for s in rep["stops"])
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown


def test_an_out_of_window_post_on_a_selected_clip_refuses(tmp_path):
    # The cross-link check: the selected clip ALSO carries operator-era work, so labelling its moment
    # `machine_inferred` would be a lie about a lineage that spans both eras.
    cfg = Config(root=tmp_path)
    _seed(cfg, extra=lambda led: led.add_post(Post(id="pOld", parent_id="cA", account="b", account_id="2",
                                                   platform=Platform.tiktok, caption="x",
                                                   state=PostState.published, public_url="https://tt/old/",
                                                   created_at=f"{OUT_DAY}T09:00:00Z")))
    rep = backfill_origin(cfg, days=IN, apply=True)
    assert not rep["applied"] and any("outside" in s.lower() or "OUTSIDE" in s for s in rep["stops"])
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown


def test_two_clips_on_one_selected_moment_refuses(tmp_path):
    def extra(led):
        led.add_clip(Clip(id="cA2", parent_id="mA", path="/a2.mp4", state=ClipState.queued))
        led.add_post(Post(id="pA2", parent_id="cA2", account="b", account_id="2", platform=Platform.tiktok,
                          caption="x", state=PostState.awaiting_approval, created_at=f"{IN[0]}T12:00:00Z"))
    cfg = Config(root=tmp_path); _seed(cfg, extra=extra)
    rep = backfill_origin(cfg, days=IN, apply=True)
    assert not rep["applied"] and any("clips per moment" in s or "1:1:1" in s for s in rep["stops"])


def test_a_day_with_no_post_refuses_rather_than_labelling_the_rest(tmp_path):
    # The day histogram invariant, in the only form that cannot rot: the days are operator input, so a
    # typo'd day is caught by measuring the ledger rather than by comparing against a literal in the module.
    cfg = Config(root=tmp_path); _seed(cfg)
    rep = backfill_origin(cfg, days=[IN[0], "2026-07-31"], apply=True)
    assert not rep["applied"] and any("no post on this ledger" in s for s in rep["stops"])
    assert rep["day_histogram"] == {OUT_DAY: 1, IN[0]: 1, IN[1]: 1}     # the histogram is SHOWN, not asserted
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown


def test_an_operator_census_mismatch_refuses(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    rep = backfill_origin(cfg, days=IN, apply=True, expect_moments=524)
    assert not rep["applied"] and any("operator expected 524" in s for s in rep["stops"])
    assert backfill_origin(cfg, days=IN, apply=False, expect_moments=2)["stops"] == []


# ---- the plan token ----------------------------------------------------------------------------
def test_a_stale_plan_token_refuses_and_a_matching_one_applies(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    token = backfill_origin(cfg, days=IN, apply=False)["plan_token"]
    stale = backfill_origin(cfg, days=IN, apply=True, plan_token="NOPLAN")
    assert not stale["applied"] and stale["refused"] == "plan_moved"
    assert Ledger.load(cfg).moments["mA"].origin is MomentOrigin.unknown
    fresh = backfill_origin(cfg, days=IN, apply=True, plan_token=token)
    assert fresh["applied"] and fresh["labelled"] == 2


def test_the_token_moves_when_the_selection_moves(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    before = backfill_origin(cfg, days=IN, apply=False)["plan_token"]
    with Ledger.transaction(cfg) as led:
        _lineage(led, "C", day=IN[0])
    assert backfill_origin(cfg, days=IN, apply=False)["plan_token"] != before


# ---- the survey is a pure read ------------------------------------------------------------------
def test_the_survey_never_writes(tmp_path):
    cfg = Config(root=tmp_path); _seed(cfg)
    before = cfg.ledger_path.read_bytes()
    s = survey_amplify_descended(Ledger.load(cfg), days=frozenset(IN))
    assert s["moments"] == 2 and s["corpus_moments"] == 3 and s["corpus_unlabelled"] == 3
    assert cfg.ledger_path.read_bytes() == before


# ---- the operator wording -----------------------------------------------------------------------
def test_an_unobserved_origin_reads_unlabelled_to_an_operator():
    # `unknown` is the honest at-rest value; "unlabelled" is what it MEANS on a surface. One function
    # decides, so the Review card and the Home panel can never disagree.
    assert display_origin(MomentOrigin.unknown) == "unlabelled"
    assert display_origin(MomentOrigin.machine_inferred) == "machine_inferred"
    assert display_origin(MomentOrigin.operator) == "operator"


# ---- the audit record ----------------------------------------------------------------------------
def test_apply_writes_an_audit_line(tmp_path):
    import json
    cfg = Config(root=tmp_path); _seed(cfg)
    rep = backfill_origin(cfg, days=IN, apply=True)
    line = json.loads((cfg.control / "studio_audit.log").read_text().splitlines()[-1])
    assert line["action"] == "origin_backfill" and line["labelled"] == 2
    assert line["days"] == sorted(IN) and line["snapshot"] == rep["snapshot"] and line["refused"] is None
