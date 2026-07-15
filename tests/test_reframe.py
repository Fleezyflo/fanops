# tests/test_reframe_dryrun.py — the read-only dry-run: WRITE SAFETY, reconstruction-as-proof, classification.
#
# Controlled fakes and temporary SQLite only. Never a real ledger snapshot, never a real service stop,
# never the live corpus.
from __future__ import annotations

import json

import pytest

from fanops import clip as clipmod
from fanops import framing
from fanops import reframe
from fanops.config import Config
from fanops.framing_outcomes import FramingEventType as _FE
from fanops.ids import child_id
from fanops.ledger import Ledger
from fanops.models import Clip, ClipState, Fmt, Moment, MomentState, Post, PostState, Platform, Source
from fanops.reframe import ProductionWriteError, ReframeClass, ReframePaths

_STATS = {"fps": 4.0, "frames": [[[0.5, 0.5, 0.3, 0.42, 0.9]]]}
_FOCUS = (0.61, 0.44, 0.30, 0.38)
_SAL = (0.61, 0.44)


def _stub_framing(monkeypatch, *, ct, detect=_STATS, focus=None, track=None, sal=None, events=None):
    monkeypatch.setattr(framing, "_framing_runtime_or_raise", lambda c: object())
    ev = events or {}

    def mk(name, value):
        def fn(*a, _trace=None, **kw):
            for e in ev.get(name, []):
                if _trace is not None:
                    _trace.record(e)
            return value
        return fn
    monkeypatch.setattr(framing, "detect_window", mk("detect_window", detect))
    monkeypatch.setattr(framing, "classify_window", mk("classify_window", ct))
    monkeypatch.setattr(framing, "speaker_track", mk("speaker_track", track))
    monkeypatch.setattr(framing, "subject_focus", mk("subject_focus", focus))
    monkeypatch.setattr(framing, "motion_saliency", mk("motion_saliency", sal))


def _corpus(tmp_path, monkeypatch, *, hook=None, framing_pin=None, segments=None,
            clip_id=None, media_url=None, stamp_fp=True):
    """A minimal PRODUCTION tree: one source, one moment, one clip, its media, its {cid}.render.json
    stamped with the CENTERED fingerprint (i.e. what a broken/absent detector actually rendered)."""
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    monkeypatch.setenv("FANOPS_VISUAL_START", "0")               # keep the window math pure in the fixture
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    prod = tmp_path / "prod"
    cfg = Config(root=prod)
    cfg.sources.mkdir(parents=True, exist_ok=True)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    media = cfg.sources / "s.mp4"
    media.write_bytes(b"\x00" * 64)

    led = Ledger.load(cfg)
    led.add_source(Source(id="src_1", source_path=str(media), width=1920, height=1080, duration=120.0))
    led.add_moment(Moment(id="mom_1", parent_id="src_1", content_token="t", start=10.0, end=28.0,
                          reason="r", state=MomentState.clipped, hook=hook, framing=framing_pin,
                          segments=segments or []))
    cid = clip_id or child_id("clip", "mom_1", Fmt.r9x16.value)
    led.add_clip(Clip(id=cid, parent_id="mom_1", state=ClipState.rendered,
                      path=str(cfg.clips / f"{cid}.mp4"), aspect=Fmt.r9x16, media_url=media_url))
    led.save()

    if stamp_fp:
        m, src = led.moments["mom_1"], led.sources["src_1"]
        from fanops.bands import band_for
        band = band_for(clipmod._moment_profile(m, cfg))
        cs, ce = clipmod.fit_window(m.start, m.end, src.duration, lo=band.lo, hi=band.hi)
        cs, ce = clipmod.snap_window(cs, ce, clipmod._trusted_transcript(src), duration=src.duration)
        ass, _ = clipmod._build_ass_text(led, cfg, "mom_1", cid, Fmt.r9x16, clip_start=cs, clip_end=ce)
        p = clipmod._render_fingerprint_payload(src.source_path, cs, ce, Fmt.r9x16.value, 1920, 1080,
                                                ass or "", top_bias=clipmod._moment_top_bias(m, cfg),
                                                focus=None, track=None, content_type=None)   # CENTERED
        (cfg.clips / f"{cid}.render.json").write_text(
            json.dumps({"fp": clipmod.fingerprint_of_payload(p)}))
    return ReframePaths.build(prod, tmp_path / "scratch"), cid


# ---------------------------------------------------------------------------- C-3 / write safety

def test_scratch_config_redirects_every_write_and_inherits_production_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("FANOPS_SMART_FRAMING", "1")
    monkeypatch.setenv("FANOPS_BURN_SUBS", "0")
    paths = ReframePaths.build(tmp_path / "prod", tmp_path / "scratch")
    s = paths.scratch_cfg
    for p in (s.clips, s.agent_io, s.control, s.ledger_path, s.reports):
        assert str(p).startswith(str(paths.scratch_root)), f"{p} escapes scratch"
    # Flags are @property reads of os.environ, so the scratch Config INHERITS production's values —
    # which is exactly what reconstruction requires.
    assert s.smart_framing is paths.production_cfg.smart_framing
    assert s.burn_subs is paths.production_cfg.burn_subs
    assert s.aware_reframe is paths.production_cfg.aware_reframe
    assert s.clip_profile == paths.production_cfg.clip_profile


def test_stage_lock_path_is_scratch_so_it_cannot_contend_with_the_daemon(tmp_path):
    from fanops.stage_lock import _lock_path_for
    paths = ReframePaths.build(tmp_path / "prod", tmp_path / "scratch")
    lp = _lock_path_for(paths.scratch_cfg, stage="framing", key="src_1")
    assert str(lp).startswith(str(paths.scratch_root))


def test_assert_write_target_rejects_a_production_path(tmp_path):
    paths = ReframePaths.build(tmp_path / "prod", tmp_path / "scratch")
    paths.assert_write_target(paths.scratch_root / "a" / "b.json")          # fine
    with pytest.raises(ProductionWriteError):
        paths.assert_write_target(paths.production_root / "x.json")
    with pytest.raises(ProductionWriteError):
        paths.assert_write_target(tmp_path / "elsewhere.json")


def test_the_runner_never_mutates_FANOPS_ROOT(tmp_path, monkeypatch):
    """FANOPS_ROOT is in conftest's _LEAKY_ENV for good reason. Pass root=, never mutate the env."""
    monkeypatch.delenv("FANOPS_ROOT", raising=False)
    paths, _cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS, events={"subject_focus": [_FE.FOCUS_PLACED]})
    reframe.run_dry_run(paths, argv=["reframe", "--dry-run"])
    import os
    assert "FANOPS_ROOT" not in os.environ


def test_analysis_phase_diff_is_EMPTY_nothing_lands_in_production(tmp_path, monkeypatch):
    """THE invariant, and the PROOF rather than the mechanism: across the ANALYSIS phase — where the
    framing pass runs — no sidecar, lockfile, stamp_stage manifest, keyframe jpg, vstart sidecar or log
    may appear under the production root."""
    paths, _cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS, events={"subject_focus": [_FE.FOCUS_PLACED]})
    before = reframe.scan_tree(paths.production_root)
    man = reframe.run_dry_run(paths, argv=["reframe", "--dry-run"])
    after = reframe.scan_tree(paths.production_root)

    assert man["analysis_phase_diff"] == {"added": [], "removed": [], "changed": []}
    assert man["analysis_phase_clean"] is True

    # And across the WHOLE run, the ONLY thing production gained is SQLite's own WAL coordination — the
    # unavoidable, disclosed, BOUNDED cost of reading a live WAL database consistently. Nothing else:
    # no sidecar, no lockfile, no keyframe, no vstart, no log.
    whole = reframe.diff_tree(before, after)
    assert reframe._snapshot_diff_ok(whole, paths.production_cfg), f"the dry-run MUTATED production: {whole}"
    lp = str(paths.production_cfg.ledger_path)
    assert all(k in (lp + "-wal", lp + "-shm") for k in whole["added"]), whole["added"]
    assert whole["removed"] == []
    assert list(paths.scratch_root.rglob("*")), "the scratch root should have received the writes"


def test_snapshot_is_a_scratch_copy_not_Ledger_snapshot(tmp_path, monkeypatch):
    """Ledger.snapshot() writes its backup into 00_control — a PRODUCTION directory — and takes the ledger
    lock. We open the live DB mode=ro and back it up into scratch instead."""
    paths, _cid = _corpus(tmp_path, monkeypatch)
    dest = reframe.snapshot_ledger(paths)
    assert str(dest).startswith(str(paths.scratch_root))
    assert Ledger.load(paths.scratch_cfg).clips, "the snapshot must be loadable"


def test_snapshot_phase_may_touch_ONLY_sqlites_own_wal_and_shm(tmp_path, monkeypatch):
    """Reading a WAL database — even mode=ro — makes SQLite create its -wal/-shm coordination files. That
    is unavoidable (the alternative, immutable=1, is a LIE while the daemon may be writing, and would let
    us read a torn state), so we DISCLOSE it rather than scan after the fact and call it clean.

    But it is BOUNDED: SQLite's two sidecars beside the live ledger, plus the mtime of the directory that
    now holds them. A real file appearing here means the snapshot mutated production and the run is void."""
    paths, _cid = _corpus(tmp_path, monkeypatch)
    before = reframe.scan_tree(paths.production_root)
    reframe.snapshot_ledger(paths)
    d = reframe.diff_tree(before, reframe.scan_tree(paths.production_root))
    assert reframe._snapshot_diff_ok(d, paths.production_cfg), f"the snapshot touched more than SQLite's own: {d}"
    lp = str(paths.production_cfg.ledger_path)
    for k in d["added"]:
        assert k in (lp + "-wal", lp + "-shm"), f"unexpected production write during snapshot: {k}"

    # And a REAL production write during the snapshot phase is caught, not excused.
    bad = {"added": [str(paths.production_cfg.clips / "x.mp4")], "removed": [], "changed": []}
    assert reframe._snapshot_diff_ok(bad, paths.production_cfg) is False


# ---------------------------------------------------------------------------- reconstruction is a PROOF

def test_reconstruction_proves_the_centered_payload(tmp_path, monkeypatch):
    paths, cid = _corpus(tmp_path, monkeypatch)
    cfg = paths.scratch_cfg
    reframe.snapshot_ledger(paths)
    led = Ledger.load(cfg)
    rec = reframe.reconstruct(paths, cfg, led, led.clips[cid], paths.read_stored_fingerprint(cid))
    assert rec.proved and rec.matches == 1
    assert rec.payload["focus"] is None if "focus" in rec.payload else True     # centered: no framing keys
    assert "focus" not in rec.payload and "track" not in rec.payload and "geom" not in rec.payload


def test_reconstruction_fails_HONESTLY_when_it_cannot_reproduce(tmp_path, monkeypatch):
    """Zero matches means WE COULD NOT REPRODUCE IT. The cause is unknown, and we do not name one:
    calling it DRIFT would be a positive claim about a change nobody observed."""
    paths, cid = _corpus(tmp_path, monkeypatch)
    (paths.production_clips / f"{cid}.render.json").write_text(json.dumps({"fp": "deadbeef" * 8}))
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS, events={"subject_focus": [_FE.FOCUS_PLACED]})
    man = reframe.run_dry_run(paths, argv=["x"])
    assert man["clips"][0]["classification"] == ReframeClass.UNRECONSTRUCTABLE.value
    assert "unknown" in man["clips"][0]["reason"]
    assert man["clips"][0]["classification"] != ReframeClass.DRIFT.value


def test_candidate_dedup_is_on_canonical_BYTES_not_dict_equality(tmp_path, monkeypatch):
    """A REGRESSION GUARD, not a live-bug fix. {"cs": 0.0} and {"cs": -0.0} compare EQUAL as dicts but
    serialize DIFFERENTLY. No candidate axis can produce a signed zero today, so keying on bytes changes
    no count — it exists so a future axis that introduces one cannot silently drop a candidate and report
    a false UNRECONSTRUCTABLE."""
    a = {"cs": 0.0, "ce": 5.0}
    b = {"cs": -0.0, "ce": 5.0}
    assert a == b                                              # dict-equal ...
    assert clipmod.fingerprint_payload_bytes(a) != clipmod.fingerprint_payload_bytes(b)   # ... byte-distinct
    assert clipmod.fingerprint_of_payload(a) != clipmod.fingerprint_of_payload(b)


def test_provenance_labels_are_a_SET_never_one_fabricated_label(tmp_path, monkeypatch):
    """An empty .ass collapses BOTH ass-candidates onto ONE payload. Stamping a single winning label
    ('the ass came from disk') would be a fabricated provenance claim — indistinguishable, on the
    evidence, from 'the ass was empty'. We record both."""
    paths, cid = _corpus(tmp_path, monkeypatch)
    (paths.production_clips / f"{cid}.ass").write_text("")     # on disk, and EMPTY
    cfg = paths.scratch_cfg
    reframe.snapshot_ledger(paths)
    led = Ledger.load(cfg)
    rec = reframe.reconstruct(paths, cfg, led, led.clips[cid], paths.read_stored_fingerprint(cid))
    assert rec.proved
    assert len(rec.labels) >= 2, f"both ass-candidates collapse onto one payload: {rec.labels}"
    assert any("ass:disk" in x for x in rec.labels) and any("ass:empty" in x for x in rec.labels)


def test_stale_ass_on_disk_does_not_cause_a_false_drift(tmp_path, monkeypatch):
    """D6. _subtitles_vf returns (None, False) with no hook and no segments and does NOT delete an
    existing {cid}.ass; render_moment then hashes ass_text="" WHILE THE FILE EXISTS. The "" candidate
    must win, and the clip must not be reported as DRIFT."""
    paths, cid = _corpus(tmp_path, monkeypatch, hook=None)     # no hook, burn_subs off -> nothing to burn
    (paths.production_clips / f"{cid}.ass").write_text("[Script Info]\nstale garbage from an older hook\n")
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS, events={"subject_focus": [_FE.FOCUS_PLACED]})
    man = reframe.run_dry_run(paths, argv=["x"])
    row = man["clips"][0]
    assert row["classification"] == ReframeClass.ELIGIBLE.value, row["reason"]
    assert row["payload_old"]["ass"] == ""                     # the empty candidate won, as the renderer did


# ---------------------------------------------------------------------------- classification

def test_eligible_when_only_framing_keys_change(tmp_path, monkeypatch):
    paths, cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS, events={"subject_focus": [_FE.FOCUS_PLACED]})
    row = reframe.run_dry_run(paths, argv=["x"])["clips"][0]
    assert row["classification"] == ReframeClass.ELIGIBLE.value
    assert set(row["payload_delta"]) <= reframe.APPROVED_FRAMING_KEYS
    assert row["reconstruction_proved"] is True


def test_DRIFT_is_real_a_changed_hook_would_also_be_re_burned(tmp_path, monkeypatch):
    """D5. The delta guard is only load-bearing because payload_new is derived from CURRENT state: it
    catches 're-rendering would ALSO re-burn a changed hook'."""
    paths, cid = _corpus(tmp_path, monkeypatch, hook=None)     # rendered with NO hook -> fp has ass=""
    cfg = paths.scratch_cfg
    reframe.snapshot_ledger(paths)
    led = Ledger.load(cfg)
    led.moments["mom_1"].hook = "wait for the drop"            # the hook CHANGED since the render
    led.save()
    monkeypatch.setenv("FANOPS_BURN_SUBS", "1")
    monkeypatch.setattr(clipmod.overlay, "ffmpeg_has_textfilter", lambda: True)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS, events={"subject_focus": [_FE.FOCUS_PLACED]})
    row = reframe.classify_clip(paths, cfg, led, led.clips[cid])
    assert row["classification"] == ReframeClass.DRIFT.value
    assert "ass" in row["payload_delta"] and row["delta_keys_ok"] is False


def test_legitimate_center_requires_affirmative_evidence(tmp_path, monkeypatch):
    """fp_stored == fp_old == fp_new is NOT enough. The detector must have RUN and found no subject."""
    paths, cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=None, events={"subject_focus": [_FE.NO_FACE]})
    row = reframe.run_dry_run(paths, argv=["x"])["clips"][0]
    assert row["classification"] == ReframeClass.LEGITIMATE_CENTER.value
    assert row["fp_stored"] == row["fp_old"] == row["fp_new"]


def test_a_broken_toolchain_is_FRAMING_UNRESOLVED_never_a_legitimate_centre(tmp_path, monkeypatch):
    """THE POINT OF THE WHOLE TOOL. The fingerprint is unchanged — and that is NOT evidence of anything."""
    paths, cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_NOPEOPLE, detect=None, sal=None,
                  events={"detect_window": [_FE.FFMPEG_UNAVAILABLE]})
    row = reframe.run_dry_run(paths, argv=["x"])["clips"][0]
    assert row["classification"] == ReframeClass.FRAMING_UNRESOLVED.value
    assert row["fp_stored"] == row["fp_new"]                   # identical fingerprint ...
    assert row["framing"]["root_cause"] == "ffmpeg_unavailable"   # ... and a broken toolchain behind it
    assert row["classification"] != ReframeClass.LEGITIMATE_CENTER.value


def test_supercut_and_stitch_are_excluded(tmp_path, monkeypatch):
    paths, cid = _corpus(tmp_path, monkeypatch, segments=[(1.0, 3.0), (7.0, 9.0)])
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS)
    assert reframe.run_dry_run(paths, argv=["x"])["clips"][0]["classification"] \
        == ReframeClass.SUPERCUT_EXCLUDED.value

    paths2, cid2 = _corpus(tmp_path / "b", monkeypatch, clip_id="clip_stitch_deadbeef")
    assert reframe.run_dry_run(paths2, argv=["x"])["clips"][0]["classification"] \
        == ReframeClass.STITCH_EXCLUDED.value


def test_remote_asset_guards_are_separate_for_clip_and_post(tmp_path, monkeypatch):
    paths, cid = _corpus(tmp_path, monkeypatch, media_url="https://cdn/x.mp4")
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS)
    assert reframe.run_dry_run(paths, argv=["x"])["clips"][0]["classification"] \
        == ReframeClass.REMOTE_ASSET_PRESENT.value

    led = Ledger.load(paths.production_cfg)
    p = Post(id="p1", parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
             caption="c", state=PostState.queued)
    assert led.post_is_remote_or_publishable(p) is True         # queued == APPROVED, publishes on the next sweep
    p2 = Post(id="p2", parent_id=cid, account="a", account_id="1", platform=Platform.instagram,
              caption="c", state=PostState.awaiting_approval)
    assert led.post_is_remote_or_publishable(p2) is False
    p2.media_urls = ["https://cdn/y.mp4"]                       # a hosted asset, whatever the state
    assert led.post_is_remote_or_publishable(p2) is True


def test_post_guard_parity_with_live_states_plus_queued():
    from fanops.ledger import Ledger as L
    from fanops.models import PostState as PS
    expected = set(L._LIVE_POST_STATES) | {PS.queued}
    led = L.__new__(L)
    hit = {s for s in PS if led.post_is_remote_or_publishable(
        type("P", (), {"media_urls": [], "state": s})())}
    assert hit == expected


def test_partial_run_suppresses_the_go_no_go(tmp_path, monkeypatch):
    """A --limit run cannot support a corpus-wide claim, so it does not get to make one."""
    paths, cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS, events={"subject_focus": [_FE.FOCUS_PLACED]})
    man = reframe.run_dry_run(paths, limit=0, argv=["x"])
    assert man["partial"] is True and man["summary"]["go_no_go"] is None


def test_manifest_marks_eligibility_as_STRUCTURAL_not_visually_reviewed(tmp_path, monkeypatch):
    """E6: nothing in the manifest may read as a visual-quality verdict. The summary flags the whole run
    UNREVIEWED, and a non-partial go_no_go — even with ZERO blockers — states that eligibility is structural
    and a visual pass is still required, so a green go_no_go cannot be misread as 'safe to ship'."""
    paths, cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS, events={"subject_focus": [_FE.FOCUS_PLACED]})
    man = reframe.run_dry_run(paths, argv=["x"])
    assert man["summary"]["visual_review_status"] == "unreviewed"
    gng = man["summary"]["go_no_go"]
    assert gng is not None and gng["blockers"] == []             # structurally clear...
    assert "REQUIRED" in gng["visual_review"]                    # ...but explicitly NOT visually cleared


def test_one_bad_clip_does_not_abort_the_corpus_scan(tmp_path, monkeypatch):
    """C-1's operational half: a per-clip boundary yields ERROR and the scan continues."""
    paths, cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS)

    def boom(*a, **k):
        raise RuntimeError("clip blew up")
    monkeypatch.setattr(reframe, "reconstruct", boom)
    man = reframe.run_dry_run(paths, argv=["x"])
    assert man["clips"][0]["classification"] == ReframeClass.ERROR.value
    assert man["analysis_phase_clean"] is True                  # and it STILL wrote nothing to production


def test_a_production_write_is_never_swallowed(tmp_path, monkeypatch):
    """ProductionWriteError is re-raised out of the per-clip boundary: a dry-run that writes to
    production has already failed at the only thing it promised."""
    paths, cid = _corpus(tmp_path, monkeypatch)
    _stub_framing(monkeypatch, ct=framing.CT_SINGLE, focus=_FOCUS)

    def boom(*a, **k):
        raise ProductionWriteError("wrote to prod")
    monkeypatch.setattr(reframe, "reconstruct", boom)
    with pytest.raises(ProductionWriteError):
        reframe.run_dry_run(paths, argv=["x"])


def test_attribution_stamps_everything_that_could_change_a_fingerprint(tmp_path, monkeypatch):
    paths, _cid = _corpus(tmp_path, monkeypatch)
    a = reframe.attribution(paths.scratch_cfg)
    for k in ("git_commit_sha", "git_dirty", "cv2_version", "yunet_model_sha256", "reframe_geom_v",
              "vstart_cache_v", "detect_cache_v", "sidecar_v", "smart_framing", "aware_reframe",
              "visual_start", "burn_subs", "clip_profile", "fingerprint_last_changed_commit"):
        assert k in a, f"attribution is missing {k}"
    assert a["reframe_geom_v"] == clipmod._REFRAME_GEOM_V


# ── RC-10 (S09): cmd_reframe must not leak the /tmp scratch it mints for a dry-run ────────────────

def test_cmd_reframe_dry_run_removes_owned_scratch(tmp_path, monkeypatch):
    # RC-10 fails-before/passes-after: a `--dry-run` with no --scratch mkdtemp'd a `fanops_reframe_*`
    # dir and never removed it. It is now cleaned in a finally once WE own it.
    import tempfile as _tf, types
    from fanops import cli
    cfg = Config(root=tmp_path)
    created = {}
    real = _tf.mkdtemp
    monkeypatch.setattr(_tf, "mkdtemp", lambda *a, **k: created.setdefault("p", real(*a, **k)))
    monkeypatch.setattr(reframe.ReframePaths, "build",
                        classmethod(lambda cls, root, scratch: types.SimpleNamespace(scratch_root=scratch)))
    monkeypatch.setattr(reframe, "run_dry_run", lambda paths, **k: {"analysis_phase_clean": True})
    args = types.SimpleNamespace(scratch=None, dry_run=True, apply=False, status=False, resume=False,
                                 rollback=False, cleanup=False, json=True, limit=None)
    assert cli.cmd_reframe(cfg, args) == 0
    from pathlib import Path
    assert not Path(created["p"]).exists()                      # RC-10: the auto scratch is gone


def test_cmd_reframe_preserves_operator_scratch(tmp_path, monkeypatch):
    # PRESERVED: an operator-supplied --scratch is THEIRS — never deleted.
    import types
    from fanops import cli
    cfg = Config(root=tmp_path)
    op = tmp_path / "mine"; op.mkdir()
    monkeypatch.setattr(reframe.ReframePaths, "build",
                        classmethod(lambda cls, root, scratch: types.SimpleNamespace(scratch_root=scratch)))
    monkeypatch.setattr(reframe, "run_dry_run", lambda paths, **k: {"analysis_phase_clean": True})
    args = types.SimpleNamespace(scratch=str(op), dry_run=True, apply=False, status=False, resume=False,
                                 rollback=False, cleanup=False, json=True, limit=None)
    assert cli.cmd_reframe(cfg, args) == 0
    assert op.exists()                                          # operator --scratch preserved
