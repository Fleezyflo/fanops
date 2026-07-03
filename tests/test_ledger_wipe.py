# tests/test_ledger_wipe.py — ledger-rebuild M4 (MOL-32/33): the fall-away of unbacked rows.
# MACHINERY ONLY — never executed against live 00_control here (tmp-path fixtures only). Two pieces:
#   MOL-32: a mandatory pre-wipe ledger.json SNAPSHOT that is VERIFIED restorable (write -> corrupt ->
#           restore -> byte-identical), and the wipe REFUSES to run without it.
#   MOL-33: the transitive-complement SELECTOR + per-entity disposition. Keep-guard keys on POST STATE
#           (analyzed/has-history), NEVER live-match. Remove ONLY rows whose entire descendant closure
#           contains NO kept post; the kept posts' ancestor chain (clips/moments/source) + their renders
#           survive. _delete_moment_cascade / _PROTECTED_POST_STATES stay byte-identical (asserted).
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops import ledger_wipe
from fanops.models import (Source, Moment, Clip, Post, Render, SelectionFact, AccountSelection,
                           SelectionMethod, StitchPlan, StitchState, Platform,
                           PostState, ClipState, RenderState, LIFT_SCORE, account_selection_id)


def _live_shaped(cfg):
    """A ledger mirroring the live shape: ONE source, a kept analyzed post + never-shipped awaiting rows
    that SHARE the source but hang off DIFFERENT moments/clips, plus a never-shipped subtree that is
    ENTIRELY unbacked. The kept post's ancestor chain must survive; the fully-unbacked subtree falls away."""
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
        # --- KEPT subtree: analyzed post with history (this is the "backed" one) ---
        led.add_moment(Moment(id="m_keep", parent_id="s1", content_token="K", start=0, end=3, reason="k"))
        led.add_clip(Clip(id="c_keep", parent_id="m_keep", path="/c_keep.mp4", state=ClipState.analyzed))
        led.add_render(Render(id="r_keep", clip_id="c_keep", account="@a", surface_key="instagram", path="/r_keep.mp4", state=RenderState.analyzed, source_id="s1"))
        led.add_post(Post(id="p_keep", parent_id="c_keep", account="@a", account_id="ig1",
                          platform=Platform.instagram, caption="kept", state=PostState.analyzed,
                          public_url="https://ig/reel/keep/", metrics={LIFT_SCORE: 0.5, "reach": 900}))
        # --- UNBACKED subtree: awaiting_approval never-shipped, off a DIFFERENT moment/clip ---
        led.add_moment(Moment(id="m_drop", parent_id="s1", content_token="D", start=4, end=7, reason="d"))
        led.add_clip(Clip(id="c_drop", parent_id="m_drop", path="/c_drop.mp4", state=ClipState.rendered))
        led.add_render(Render(id="r_drop", clip_id="c_drop", account="@a", surface_key="instagram", path="/r_drop.mp4", state=RenderState.rendered, source_id="s1"))
        led.add_post(Post(id="p_drop", parent_id="c_drop", account="@a", account_id="ig1",
                          platform=Platform.instagram, caption="never", state=PostState.awaiting_approval,
                          public_url="dryrun://p_drop"))
        # entity-graph refs on the UNBACKED subtree (must be swept):
        led.add_selection_fact(SelectionFact(id="sf_drop", moment_id="m_drop", account="@a",
                                             method=SelectionMethod.llm, source_id="s1"))
        led.add_account_selection(AccountSelection(id="as_drop", source_id="s1", account="@drop",
                                                   moment_ids=["m_drop"], method=SelectionMethod.llm))
        led.add_stitch_plan(StitchPlan(id="st_drop", clip_id="c_drop", strategy_key="k", state=StitchState.suggested))
        led.tag_log["@a|c_drop"] = "2026-06-01T00:00:00Z"
        # entity-graph refs on the KEPT subtree (must SURVIVE):
        led.add_selection_fact(SelectionFact(id="sf_keep", moment_id="m_keep", account="@a",
                                             method=SelectionMethod.llm, source_id="s1"))
        led.tag_log["@a|c_keep"] = "2026-06-02T00:00:00Z"
    return Ledger.load(cfg)


# ---- MOL-33: the transitive-complement selector ----
def test_wipe_set_removes_only_unbacked_closure(tmp_path):
    led = _live_shaped(Config(root=tmp_path))
    plan = ledger_wipe.compute_wipe_set(led)
    assert plan.post_ids == {"p_drop"}                     # only the never-shipped post
    assert plan.moment_ids == {"m_drop"}                   # only the moment whose closure has no kept post
    assert plan.clip_ids == {"c_drop"}
    assert plan.render_ids == {"r_drop"}


def test_wipe_keeps_analyzed_post_ancestor_chain(tmp_path):
    # THE guarantee: the kept post + its clip + its moment + the source + its render ALL survive.
    led = _live_shaped(Config(root=tmp_path))
    plan = ledger_wipe.compute_wipe_set(led)
    for kept in ("p_keep",):
        assert kept not in plan.post_ids
    assert "m_keep" not in plan.moment_ids and "c_keep" not in plan.clip_ids
    assert "r_keep" not in plan.render_ids
    assert "s1" not in plan.source_ids                     # the source stays (a kept post descends from it)


def test_wipe_keep_guard_keys_on_state_not_live_match(tmp_path):
    # A published/analyzed post with NO permalink match (single-credential probe can't see it) is STILL
    # kept — the guard keys on POST STATE, never on a live-match. This is the credential-scope invariant.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s1", source_path="/v.mp4"))
        led.add_moment(Moment(id="m1", parent_id="s1", content_token="A", start=0, end=2, reason="a"))
        led.add_clip(Clip(id="c1", parent_id="m1", path="/c1.mp4", state=ClipState.analyzed))
        # analyzed, but public_url is a foreign handle IG can't return under the single credential:
        led.add_post(Post(id="p_foreign", parent_id="c1", account="@other", account_id="ig9",
                          platform=Platform.instagram, caption="shipped elsewhere", state=PostState.analyzed,
                          public_url="https://ig/reel/foreign/", metrics={LIFT_SCORE: 0.3}))
    plan = ledger_wipe.compute_wipe_set(Ledger.load(cfg))
    assert "p_foreign" not in plan.post_ids                # kept on STATE, though unmatchable by the probe
    assert plan.post_ids == set()                          # nothing removed — it's all backed history


def test_wipe_removes_selection_facts_batches_stitch_in_closure(tmp_path):
    led = _live_shaped(Config(root=tmp_path))
    plan = ledger_wipe.compute_wipe_set(led)
    assert "sf_drop" in plan.selection_fact_ids and "sf_keep" not in plan.selection_fact_ids
    assert "st_drop" in plan.stitch_plan_ids
    # AccountSelection id is content-addressed (source, account) — the CHOSEN pick on the removed moment
    # m_drop is swept (its every moment_id is gone) even though its source s1 survives.
    assert account_selection_id("s1", "@drop") in plan.account_selection_ids
    assert "@a|c_drop" in plan.tag_log_keys and "@a|c_keep" not in plan.tag_log_keys


def test_wipe_source_removed_only_when_no_kept_descendant(tmp_path):
    # A source with ONLY never-shipped rows is fully unbacked -> the source falls away too.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s_dead", source_path="/dead.mp4"))
        led.add_moment(Moment(id="m", parent_id="s_dead", content_token="X", start=0, end=1, reason="x"))
        led.add_clip(Clip(id="c", parent_id="m", path="/c.mp4", state=ClipState.rendered))
        led.add_post(Post(id="p", parent_id="c", account="@a", account_id="1", platform=Platform.instagram,
                          caption="x", state=PostState.awaiting_approval, public_url="dryrun://p"))
    plan = ledger_wipe.compute_wipe_set(Ledger.load(cfg))
    assert plan.source_ids == {"s_dead"}


def test_preview_counts_match_plan(tmp_path):
    led = _live_shaped(Config(root=tmp_path))
    prev = ledger_wipe.wipe_preview(led)
    assert prev["counts"]["posts"] == 1 and prev["counts"]["moments"] == 1
    assert prev["counts"]["clips"] == 1 and prev["counts"]["renders"] == 1
    assert set(prev["post_ids"]) == {"p_drop"}
    assert prev["kept_posts"] == 1                         # the analyzed post is reported as kept


# ---- MOL-32: snapshot + verified restore ----
def test_snapshot_written_and_restorable(tmp_path):
    cfg = Config(root=tmp_path)
    _live_shaped(cfg)
    original = cfg.ledger_path.read_bytes()
    snap = Ledger.snapshot(cfg)                             # writes a timestamped copy under 00_control
    assert snap.exists()
    # corrupt the live ledger, then restore -> byte-identical to pre-corruption
    cfg.ledger_path.write_text('{"schema_version": 10, "posts": {}}')
    Ledger.restore_snapshot(cfg, snap)
    assert cfg.ledger_path.read_bytes() == original
    Ledger.load(cfg)                                       # the restored ledger loads cleanly


def test_snapshot_verify_restorable_catches_bad_snapshot(tmp_path):
    cfg = Config(root=tmp_path)
    _live_shaped(cfg)
    # a snapshot that doesn't load must be detected as NOT verified-restorable
    assert ledger_wipe.snapshot_is_restorable(cfg.ledger_path) is True
    bad = tmp_path / "bad.json"; bad.write_text("{ not json")
    assert ledger_wipe.snapshot_is_restorable(bad) is False


# ---- MOL-75: same-second snapshots must not clobber the pre-wipe rollback point ----
def test_two_same_second_snapshots_yield_two_distinct_surviving_files(tmp_path):
    # A UI double-click / fast retry inside confirm_wipe calls Ledger.snapshot(cfg) twice within the
    # SAME wall-clock second. The first (pristine, pre-wipe) image must NOT be silently overwritten:
    # both paths distinct, both files survive, each with its own content, each verified-restorable.
    from datetime import datetime, timezone
    cfg = Config(root=tmp_path)
    _live_shaped(cfg)
    fixed = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)   # identical second for both calls

    snap1 = Ledger.snapshot(cfg, now=fixed)
    first_image = snap1.read_bytes()
    # mutate the live ledger between the two snapshots so a clobber would be detectable as content-loss
    with Ledger.transaction(cfg) as led:
        led.add_source(Source(id="s_between", source_path="/between.mp4"))
    snap2 = Ledger.snapshot(cfg, now=fixed)

    assert snap1 != snap2                                        # structurally-unique dest paths
    assert snap1.exists() and snap2.exists()                    # neither clobbered
    assert snap1.read_bytes() == first_image                    # the pristine first image is intact
    assert snap2.read_bytes() != first_image                    # the second captured the mutated state
    # both remain valid rollback points (the wipe gate still accepts them)
    assert ledger_wipe.snapshot_is_restorable(snap1) is True
    assert ledger_wipe.snapshot_is_restorable(snap2) is True
    # timestamp prefix retained -> sortable/recognizable
    assert snap1.name.startswith("ledger.snapshot.2026") and snap2.name.startswith("ledger.snapshot.2026")


def test_snapshot_refuses_to_overwrite_an_existing_path(tmp_path, monkeypatch):
    # Belt-and-suspenders per the operator decision: even if two mint calls somehow collide on the same
    # dest, the copy REFUSES rather than clobbering the pristine snapshot silently.
    from fanops import ledger as ledger_mod
    from fanops.errors import ControlFileError
    cfg = Config(root=tmp_path)
    _live_shaped(cfg)
    # force both snapshots onto ONE fixed destination path to exercise the existence check directly
    fixed_dest = cfg.control / "ledger.snapshot.COLLIDE.json"
    monkeypatch.setattr(ledger_mod, "_snapshot_dest", lambda cfg, now: fixed_dest)
    first = Ledger.snapshot(cfg)
    assert first == fixed_dest and first.exists()
    with pytest.raises(ControlFileError):
        Ledger.snapshot(cfg)                                    # must refuse, not overwrite
    assert ledger_wipe.snapshot_is_restorable(first) is True    # the original survives untouched


def test_execute_wipe_refuses_without_snapshot(tmp_path):
    # the wipe CANNOT run unless a verified snapshot was taken first (enforced in code, not just doc).
    _live_shaped(Config(root=tmp_path))
    with pytest.raises(ledger_wipe.SnapshotRequired):
        ledger_wipe.execute_wipe(Config(root=tmp_path), confirmed=True, snapshot_path=None)


def test_execute_wipe_refuses_without_confirm(tmp_path):
    cfg = Config(root=tmp_path); _live_shaped(cfg)
    snap = Ledger.snapshot(cfg)
    with pytest.raises(ledger_wipe.WipeNotConfirmed):
        ledger_wipe.execute_wipe(cfg, confirmed=False, snapshot_path=snap)


def test_execute_wipe_removes_closure_keeps_history(tmp_path):
    cfg = Config(root=tmp_path); _live_shaped(cfg)
    snap = Ledger.snapshot(cfg)
    result = ledger_wipe.execute_wipe(cfg, confirmed=True, snapshot_path=snap)
    led = Ledger.load(cfg)
    # unbacked closure gone:
    assert "p_drop" not in led.posts and "m_drop" not in led.moments and "c_drop" not in led.clips
    assert "r_drop" not in led.renders and "sf_drop" not in led.selection_facts
    assert "st_drop" not in led.stitch_plans and "as_drop" not in led.account_selections
    assert "@a|c_drop" not in led.tag_log
    # kept history intact:
    assert "p_keep" in led.posts and "m_keep" in led.moments and "c_keep" in led.clips
    assert "r_keep" in led.renders and "sf_keep" in led.selection_facts and "s1" in led.sources
    assert "@a|c_keep" in led.tag_log
    assert result["removed"]["posts"] == 1


def test_execute_wipe_restore_recovers_everything(tmp_path):
    # after a wipe, restoring the snapshot brings the removed rows BACK (rollback works end-to-end).
    cfg = Config(root=tmp_path); _live_shaped(cfg)
    snap = Ledger.snapshot(cfg)
    ledger_wipe.execute_wipe(cfg, confirmed=True, snapshot_path=snap)
    assert "p_drop" not in Ledger.load(cfg).posts
    Ledger.restore_snapshot(cfg, snap)
    led = Ledger.load(cfg)
    assert "p_drop" in led.posts and "m_drop" in led.moments   # the wipe is reversible


# ---- the routine cascade guard must stay BYTE-IDENTICAL (MOL-33 acceptance) ----
def test_delete_moment_cascade_and_protected_states_byte_identical():
    import inspect, hashlib
    # the exact source of the routine cascade path is pinned — M4 must not touch it.
    src = inspect.getsource(Ledger._delete_moment_cascade)
    h = hashlib.sha256(src.encode()).hexdigest()
    # if this fails, _delete_moment_cascade was edited — the M4 wipe MUST be a separate verb, never a
    # change to the routine cascade. Re-verify the change is intended, then update this pin.
    # Repinned MOL-77 (R-037): the intended edit adds a fail-open os.remove of the dropped clip's .mp4 so it
    # can't leak past gc — the _PROTECTED_POST_STATES survival logic below is unchanged.
    assert h == "ca8fd847bc7302f6bbd80a98ee0ee2de81a88d37829fb65b836b1e023735ff3e", f"cascade source changed; new sha256={h}"
    assert Ledger._PROTECTED_POST_STATES == (
        PostState.published, PostState.analyzed, PostState.submitted, PostState.submitting,
        PostState.needs_reconcile, PostState.awaiting_approval, PostState.queued, PostState.retired)
