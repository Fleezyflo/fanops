# tests/test_ledger_purge.py — MOL-758: scoped purge (days + origins dual-facet agreement).
# MACHINERY ONLY — tmp-path fixtures; never against live 00_control. Global wipe byte-identity is proven
# by the UNEDITED tests/test_ledger_wipe.py suite (scope=None path).
import pytest
from fanops.config import Config
from fanops.ledger import Ledger
from fanops import ledger_wipe
from fanops.models import (
    Source, Moment, Clip, Post, Batch, BatchState, Platform, PostState, ClipState,
    MomentOrigin,
)


DAYS = frozenset({"2026-07-29", "2026-07-30", "2026-08-01"})
ORIGIN = frozenset({MomentOrigin.machine_inferred})


def _lineage(led, *, sid, mid, cid, pid, day, origin, state=PostState.awaiting_approval,
             clip_path=None, source_path=None, batch_id=None):
    """One source→moment→clip→post chain. Returns nothing; mutates led under an open transaction."""
    if sid not in led.sources:
        led.add_source(Source(id=sid, source_path=source_path or f"/{sid}.mp4", batch_id=batch_id))
    led.add_moment(Moment(id=mid, parent_id=sid, content_token=mid, start=0, end=2, reason="t",
                          origin=origin))
    led.add_clip(Clip(id=cid, parent_id=mid, path=clip_path or f"/{cid}.mp4", state=ClipState.rendered))
    led.add_post(Post(id=pid, parent_id=cid, account="a", account_id="ig1",
                      platform=Platform.instagram, caption="x", state=state,
                      public_url=f"dryrun://{pid}", created_at=f"{day}T12:00:00Z",
                      batch_id=batch_id))


def _agreed_scope_ledger(cfg):
    """3 in-window machine_inferred lineages + 1 out-of-window unknown lineage."""
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="s1", mid="m1", cid="c1", pid="p1", day="2026-07-29",
                 origin=MomentOrigin.machine_inferred, clip_path=str(cfg.root / "c1.mp4"))
        _lineage(led, sid="s2", mid="m2", cid="c2", pid="p2", day="2026-07-30",
                 origin=MomentOrigin.machine_inferred, clip_path=str(cfg.root / "c2.mp4"))
        _lineage(led, sid="s3", mid="m3", cid="c3", pid="p3", day="2026-08-01",
                 origin=MomentOrigin.machine_inferred, clip_path=str(cfg.root / "c3.mp4"))
        _lineage(led, sid="s4", mid="m4", cid="c4", pid="p4", day="2026-06-01",
                 origin=MomentOrigin.unknown, clip_path=str(cfg.root / "c4.mp4"))
        for name in ("c1.mp4", "c2.mp4", "c3.mp4", "c4.mp4"):
            (cfg.root / name).write_bytes(b"clip")
        # source files on disk (must SURVIVE purge media deletion)
        for sid in ("s1", "s2", "s3", "s4"):
            p = cfg.root / f"{sid}.mp4"
            p.write_bytes(b"source")
            led.sources[sid].source_path = str(p)
    return Ledger.load(cfg)


def test_empty_purge_scope_selects_nothing(tmp_path):
    cfg = Config(root=tmp_path)
    _agreed_scope_ledger(cfg)
    plan = ledger_wipe.compute_wipe_set(Ledger.load(cfg), scope=ledger_wipe.PurgeScope())
    assert plan.post_ids == set()
    assert plan.clip_ids == set() and plan.moment_ids == set() and plan.source_ids == set()


def test_day_and_origin_agreement_selects_three(tmp_path):
    cfg = Config(root=tmp_path)
    led = _agreed_scope_ledger(cfg)
    scope = ledger_wipe.PurgeScope(days=DAYS, origins=ORIGIN)
    plan = ledger_wipe.compute_wipe_set(led, scope=scope)
    assert plan.post_ids == {"p1", "p2", "p3"}
    assert plan.clip_ids == {"c1", "c2", "c3"}
    assert plan.moment_ids == {"m1", "m2", "m3"}
    assert "p4" not in plan.post_ids and "c4" not in plan.clip_ids and "m4" not in plan.moment_ids


def test_origin_machine_inferred_only(tmp_path):
    # Property: origin facet selects descendants of moments carrying that MomentOrigin, and no others.
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="sa", mid="ma", cid="ca", pid="pa", day="2026-07-29",
                 origin=MomentOrigin.machine_inferred)
        _lineage(led, sid="sb", mid="mb", cid="cb", pid="pb", day="2026-07-29",
                 origin=MomentOrigin.operator)
    scope = ledger_wipe.PurgeScope(days=frozenset({"2026-07-29"}),
                                   origins=frozenset({MomentOrigin.machine_inferred}))
    # day selects {pa, pb}; origin selects {pa} → disagreement
    with pytest.raises(ledger_wipe.PurgeFacetDisagreement):
        ledger_wipe.compute_wipe_set(Ledger.load(cfg), scope=scope)
    # agreed narrower day set that matches only machine_inferred:
    with Ledger.transaction(cfg) as led:
        led.posts["pb"].created_at = "2026-06-01T12:00:00Z"
    scope2 = ledger_wipe.PurgeScope(days=frozenset({"2026-07-29"}),
                                    origins=frozenset({MomentOrigin.machine_inferred}))
    plan = ledger_wipe.compute_wipe_set(Ledger.load(cfg), scope=scope2)
    assert plan.post_ids == {"pa"}
    assert "pb" not in plan.post_ids


def test_incomplete_scope_refuses(tmp_path):
    cfg = Config(root=tmp_path)
    _agreed_scope_ledger(cfg)
    led = Ledger.load(cfg)
    with pytest.raises(ledger_wipe.PurgeScopeIncomplete):
        ledger_wipe.compute_wipe_set(led, scope=ledger_wipe.PurgeScope(days=DAYS))
    with pytest.raises(ledger_wipe.PurgeScopeIncomplete):
        ledger_wipe.compute_wipe_set(led, scope=ledger_wipe.PurgeScope(origins=ORIGIN))


def test_live_guard_refuses_analyzed_and_needs_reconcile(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="s1", mid="m1", cid="c1", pid="p_ok", day="2026-07-29",
                 origin=MomentOrigin.machine_inferred, state=PostState.awaiting_approval,
                 clip_path=str(cfg.root / "c1.mp4"))
        _lineage(led, sid="s2", mid="m2", cid="c2", pid="p_an", day="2026-07-30",
                 origin=MomentOrigin.machine_inferred, state=PostState.analyzed,
                 clip_path=str(cfg.root / "c2.mp4"))
        _lineage(led, sid="s3", mid="m3", cid="c3", pid="p_nr", day="2026-08-01",
                 origin=MomentOrigin.machine_inferred, state=PostState.needs_reconcile,
                 clip_path=str(cfg.root / "c3.mp4"))
        for n in ("c1.mp4", "c2.mp4", "c3.mp4"):
            (cfg.root / n).write_bytes(b"x")
    scope = ledger_wipe.PurgeScope(days=DAYS, origins=ORIGIN)
    plan = ledger_wipe.compute_wipe_set(Ledger.load(cfg), scope=scope)
    assert plan.refused_live_post_ids == {"p_an", "p_nr"}
    assert plan.post_ids == {"p_ok"}
    assert "p_an" not in plan.post_ids and "p_nr" not in plan.post_ids
    assert "c2" not in plan.clip_ids and "m2" not in plan.moment_ids and "s2" not in plan.source_ids
    assert "c3" not in plan.clip_ids and "m3" not in plan.moment_ids and "s3" not in plan.source_ids


def test_force_live_enumerated_override(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="s1", mid="m1", cid="c1", pid="p_live", day="2026-07-29",
                 origin=MomentOrigin.machine_inferred, state=PostState.analyzed)
        _lineage(led, sid="s2", mid="m2", cid="c2", pid="p_ok", day="2026-07-30",
                 origin=MomentOrigin.machine_inferred, state=PostState.awaiting_approval)
    scope = ledger_wipe.PurgeScope(days=frozenset({"2026-07-29", "2026-07-30"}), origins=ORIGIN)
    refused = ledger_wipe.compute_wipe_set(Ledger.load(cfg), scope=scope)
    assert "p_live" in refused.refused_live_post_ids and "p_live" not in refused.post_ids
    forced = ledger_wipe.compute_wipe_set(Ledger.load(cfg), scope=scope, force_live=frozenset({"p_live"}))
    assert "p_live" in forced.post_ids
    assert "p_live" not in forced.refused_live_post_ids


def test_execute_purge_deletes_clip_media_leaves_source(tmp_path):
    cfg = Config(root=tmp_path)
    _agreed_scope_ledger(cfg)
    scope = ledger_wipe.PurgeScope(days=DAYS, origins=ORIGIN)
    # rewrite source paths to real files under tmp
    with Ledger.transaction(cfg) as led2:
        for sid in ("s1", "s2", "s3"):
            sp = cfg.root / f"{sid}_src.mp4"
            sp.write_bytes(b"SOURCE")
            led2.sources[sid].source_path = str(sp)
        for cid in ("c1", "c2", "c3"):
            cp = cfg.root / f"{cid}.mp4"
            cp.write_bytes(b"CLIP")
            led2.clips[cid].path = str(cp)
    snap = Ledger.snapshot(cfg)
    preview = ledger_wipe.purge_preview(Ledger.load(cfg), scope)
    result = ledger_wipe.execute_purge(cfg, scope, confirmed=True, snapshot_path=snap, token=preview["token"])
    assert result["removed"]["posts"] == 3
    assert result["media_deleted"] >= 3
    after = Ledger.load(cfg)
    assert "p1" not in after.posts and "c1" not in after.clips
    assert not (cfg.root / "c1.mp4").exists()
    assert (cfg.root / "s1_src.mp4").exists()               # source FILE left on disk


def test_rolled_back_purge_unlinks_nothing(tmp_path):
    """A raised transaction that appended to `_deferred_unlinks` must unlink NOTHING (drain is post-commit)."""
    cfg = Config(root=tmp_path)
    _agreed_scope_ledger(cfg)
    scope = ledger_wipe.PurgeScope(days=DAYS, origins=ORIGIN)
    with Ledger.transaction(cfg) as led:
        for cid in ("c1", "c2", "c3"):
            cp = cfg.root / f"{cid}.mp4"
            cp.write_bytes(b"CLIP")
            led.clips[cid].path = str(cp)
    clip = cfg.root / "c1.mp4"
    assert clip.exists()
    with pytest.raises(RuntimeError, match="abort-purge"):
        with Ledger.transaction(cfg) as led:
            plan = ledger_wipe.compute_wipe_set(led, scope=scope)
            for path in ledger_wipe._wipe_file_manifest(led, plan):
                led._deferred_unlinks.append(path)
            for pid in list(plan.post_ids):
                led.posts.pop(pid, None)
            raise RuntimeError("abort-purge")
    assert clip.exists()                                     # deferred drain never ran
    assert "p1" in Ledger.load(cfg).posts                    # rows restored by rollback

def test_stale_token_refused_before_mutation(tmp_path):
    cfg = Config(root=tmp_path)
    _agreed_scope_ledger(cfg)
    scope = ledger_wipe.PurgeScope(days=DAYS, origins=ORIGIN)
    snap = Ledger.snapshot(cfg)
    with pytest.raises(ledger_wipe.StalePurgePreview):
        ledger_wipe.execute_purge(cfg, scope, confirmed=True, snapshot_path=snap, token="deadbeef" * 4)
    assert "p1" in Ledger.load(cfg).posts


def test_batch_shared_by_in_and_out_of_scope_survives(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        led.add_batch(Batch(id="batch_shared", name="shared", target_accounts=["a"], state=BatchState.open))
        _lineage(led, sid="s1", mid="m1", cid="c1", pid="p_in", day="2026-07-29",
                 origin=MomentOrigin.machine_inferred, batch_id="batch_shared")
        _lineage(led, sid="s2", mid="m2", cid="c2", pid="p_out", day="2026-06-01",
                 origin=MomentOrigin.unknown, batch_id="batch_shared")
    scope = ledger_wipe.PurgeScope(days=frozenset({"2026-07-29"}), origins=ORIGIN)
    plan = ledger_wipe.compute_wipe_set(Ledger.load(cfg), scope=scope)
    assert "p_in" in plan.post_ids
    assert "batch_shared" not in plan.batch_ids              # out-of-scope post still references it


def test_restore_snapshot_returns_rows_not_media(tmp_path):
    cfg = Config(root=tmp_path)
    _agreed_scope_ledger(cfg)
    scope = ledger_wipe.PurgeScope(days=DAYS, origins=ORIGIN)
    with Ledger.transaction(cfg) as led:
        for cid in ("c1", "c2", "c3"):
            cp = cfg.root / f"{cid}.mp4"
            cp.write_bytes(b"CLIP")
            led.clips[cid].path = str(cp)
    snap = Ledger.snapshot(cfg)
    preview = ledger_wipe.purge_preview(Ledger.load(cfg), scope)
    ledger_wipe.execute_purge(cfg, scope, confirmed=True, snapshot_path=snap, token=preview["token"])
    assert not (cfg.root / "c1.mp4").exists()
    Ledger.restore_snapshot(cfg, snap)
    after = Ledger.load(cfg)
    assert "p1" in after.posts and "c1" in after.clips       # rows return
    assert not (cfg.root / "c1.mp4").exists()                # media does NOT — purge irreversible for media


def test_purge_preview_includes_refused_in_token(tmp_path):
    cfg = Config(root=tmp_path)
    with Ledger.transaction(cfg) as led:
        _lineage(led, sid="s1", mid="m1", cid="c1", pid="p_ok", day="2026-07-29",
                 origin=MomentOrigin.machine_inferred)
        _lineage(led, sid="s2", mid="m2", cid="c2", pid="p_live", day="2026-07-30",
                 origin=MomentOrigin.machine_inferred, state=PostState.analyzed)
    scope = ledger_wipe.PurgeScope(days=frozenset({"2026-07-29", "2026-07-30"}), origins=ORIGIN)
    prev = ledger_wipe.purge_preview(Ledger.load(cfg), scope)
    assert "p_live" in prev["refused_live_post_ids"]
    assert prev["token"] == ledger_wipe.preview_token(prev)


def test_bad_origin_string_refuses_at_construction():
    with pytest.raises(ValueError):
        ledger_wipe.PurgeScope(days=DAYS, origins=frozenset({"amplify"}))  # type: ignore[arg-type]
