# tests/test_reframe_apply.py
"""The MUTATION phase. Every guard here exists because the alternative is a corpus you cannot restore.

The unit lane has NO toolchain, so the RENDERER and the PROBES are stubbed — but every guard under test
(the lock, the preimage assertion, the backup, the validators, the atomic commit, the resume state machine,
the rollback, the ledger proof) runs for real against a real temp corpus on a real filesystem.

Mutation-proof for the headline guard: delete the `_refuse_if_migrating` call from clip.render_moment and
test_normal_render_REFUSES_while_migration_lock_held fails."""
import json
from pathlib import Path

import pytest

from fanops import clip as clipmod
from fanops import reframe_apply as ra
from fanops.config import Config
from fanops.reframe import ReframePaths


# ---- a real, tiny corpus on disk -------------------------------------------------------------------

def _corpus(tmp_path, *, media=b"OLD-PIXELS", fp_old="fpold", cid="clip_a"):
    prod, scratch = tmp_path / "prod", tmp_path / "scratch"
    cfg = Config(root=prod)
    cfg.clips.mkdir(parents=True, exist_ok=True)
    cfg.control.mkdir(parents=True, exist_ok=True)
    (cfg.clips / f"{cid}.mp4").write_bytes(media)
    (cfg.clips / f"{cid}.render.json").write_text(json.dumps({"fp": fp_old}))
    (cfg.clips / f"{cid}.ass").write_text("ASS-TEXT")
    src = tmp_path / "src.mp4"; src.write_bytes(b"SOURCE")
    paths = ReframePaths.build(prod, scratch)
    return cfg, paths, src


def _row(cfg, src, *, cid="clip_a", fp_old="fpold", fp_new="fpnew"):
    mp4, side, ass = cfg.clips / f"{cid}.mp4", cfg.clips / f"{cid}.render.json", cfg.clips / f"{cid}.ass"
    return {
        "clip_id": cid, "moment_id": "m1", "source_id": "s1", "aspect": "9:16",
        "media_path": str(mp4), "sidecar_path": str(side), "ass_path": str(ass),
        "preimage": {"media_sha256": ra.sha256_file(mp4), "sidecar_sha256": ra.sha256_file(side),
                     "ass_sha256": "x", "ass_file_sha256": ra.sha256_file(ass),
                     "source_sha256_path": str(src), "clip_state": "rendered", "moment_state": "clipped",
                     "clip_media_url": None},
        "fp_old": fp_old, "fp_new": fp_new,
        "payload_old": {"src": str(src), "ass": "ASS-TEXT"},
        "payload_new": {"src": str(src), "ass": "ASS-TEXT", "focus": [0.5, 0.4], "geom": 4},
        "payload_delta": ["focus", "geom"],
        "render": {"src_path": str(src), "cs": 1.0, "ce": 5.0, "aspect": "9:16", "src_w": 1920, "src_h": 1080,
                   "top_bias": False, "focus": [0.5, 0.4], "track": None, "content_type": None, "has_ass": True},
        "expect": {"duration": 4.0, "fps": 30.0, "has_audio": True, "audio_channels": 2, "audio_rate": "48000",
                   "target_w": 1080, "target_h": 1920},
        "framing": {},
    }


class _FakeLed:
    """Only what _assert_preimage reads."""
    def __init__(self, cid="clip_a", media_url=None, remote=False):
        c = type("C", (), {})()
        c.id, c.parent_id, c.state, c.media_url = cid, "m1", type("S", (), {"value": "rendered"})(), media_url
        m = type("M", (), {})()
        m.id, m.parent_id, m.state = "m1", "s1", type("S", (), {"value": "clipped"})()
        self.clips, self.moments, self._remote = {cid: c}, {"m1": m}, remote

    def post_is_remote_or_publishable_any(self, clip_id):
        return self._remote


def _dirs(cfg, run_id="rf_test"):
    d = ra.RunDirs.build(cfg, run_id); d.mkdirs(); return d


def _good_probe(*_a, **_k):
    return {"format": {"duration": "4.0"},
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "30/1"},
                        {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}]}


def _stub_render(monkeypatch, out_bytes=b"NEW-PIXELS", rc=0):
    def fake(src_path, dst, cs, ce, aspect, **kw):
        if rc == 0:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(out_bytes)
        return type("R", (), {"returncode": rc})()
    monkeypatch.setattr(clipmod, "render_reframed", fake)
    monkeypatch.setattr(ra, "ffprobe_json", _good_probe)
    monkeypatch.setattr(ra, "decodes", lambda *_a, **_k: True)
    monkeypatch.setattr(clipmod, "fingerprint_of_payload", lambda _p: "fpnew")
    monkeypatch.setattr(clipmod, "_render_fingerprint_payload",
                        lambda *a, **k: {"src": a[0], "ass": "ASS-TEXT", "focus": [0.5, 0.4], "geom": 4})


# ================ 1/2 · THE LOCK: the invariant, not the stopped services =========================

def test_normal_render_REFUSES_while_migration_lock_held(tmp_path):
    """MUTATION-PROOF: remove _refuse_if_migrating from render_moment and this test fails. A daemon render
    landing on a clip mid-migration could overwrite migrated pixels with a centred crop."""
    cfg, _paths, _src = _corpus(tmp_path)
    lock = ra.MigrationLock(cfg, "rf_x")
    lock.acquire()
    try:
        ra._OWNED_RUN_ID = None                       # we are NOT the migration (a daemon in another process)
        with pytest.raises(ra.MigrationLockHeld):
            clipmod._refuse_if_migrating(cfg, "clip_a")
    finally:
        ra._OWNED_RUN_ID = "rf_x"; lock.release()


def test_the_migration_ITSELF_may_render_while_holding_its_own_lock(tmp_path):
    cfg, _p, _s = _corpus(tmp_path)
    with ra.MigrationLock(cfg, "rf_mine"):
        clipmod._refuse_if_migrating(cfg, "clip_a")    # must NOT raise: we own it


def test_no_lock_no_guard_byte_identical_behaviour(tmp_path):
    cfg, _p, _s = _corpus(tmp_path)
    clipmod._refuse_if_migrating(cfg, "clip_a")        # no lockfile -> a no-op


def test_two_migrations_cannot_run_at_once(tmp_path):
    cfg, _p, _s = _corpus(tmp_path)
    with ra.MigrationLock(cfg, "rf_1"):
        with pytest.raises(ra.MigrationLockHeld):
            ra.MigrationLock(cfg, "rf_2").acquire()


def test_render_account_cut_guard_is_OUTSIDE_its_fail_open_handler(tmp_path):
    """render_account_cut swallows Exception -> (False, None). If the guard were INSIDE that try, the
    refusal would be swallowed and it would silently fall back to burning over a migrating clip."""
    import inspect
    srcl = inspect.getsource(clipmod.render_account_cut)
    guard = srcl.index("_refuse_if_migrating")
    first_try = srcl.index("\n    try:")
    assert guard < first_try, "the migration guard must precede render_account_cut's fail-open try"


# ================ 5/6 · PREIMAGE: live state must still match the immutable plan ==================

def test_preimage_mismatch_media_changed_BLOCKS(tmp_path):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    Path(row["media_path"]).write_bytes(b"SOMEONE-ELSE-TOUCHED-IT")
    with pytest.raises(ra.PlanStale, match="media sha"):
        ra._assert_preimage(paths, d, _FakeLed(), row)


def test_preimage_ass_changed_BLOCKS(tmp_path):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    Path(row["ass_path"]).write_text("DIFFERENT TEXT")
    with pytest.raises(ra.PlanStale, match=r"\.ass changed"):
        ra._assert_preimage(paths, d, _FakeLed(), row)


def test_remote_asset_appearing_after_planning_BLOCKS(tmp_path):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    with pytest.raises(ra.PlanStale, match="REMOTE"):
        ra._assert_preimage(paths, d, _FakeLed(media_url="https://cdn/x.mp4"), row)


def test_post_becoming_publishable_after_planning_BLOCKS(tmp_path):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    with pytest.raises(ra.PlanStale, match="remote/publishable"):
        ra._assert_preimage(paths, d, _FakeLed(remote=True), row)


def test_stored_fp_not_fp_old_BLOCKS(tmp_path):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    Path(row["sidecar_path"]).write_text(json.dumps({"fp": "somethingelse"}))
    row["preimage"]["sidecar_sha256"] = ra.sha256_file(row["sidecar_path"])
    with pytest.raises(ra.PlanStale, match="not fp_old"):
        ra._assert_preimage(paths, d, _FakeLed(), row)


# ================ 7 · BACKUPS: byte-exact, verified, never overwritten ============================

def test_backup_is_byte_exact_and_verified(tmp_path):
    cfg, _p, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    ra.backup_clip(d, row)
    assert (d.backups / "clip_a.mp4").read_bytes() == b"OLD-PIXELS"
    assert ra.sha256_file(d.backups / "clip_a.mp4") == row["preimage"]["media_sha256"]


def test_backup_is_NEVER_overwritten_a_corrupt_one_is_a_hard_error(tmp_path):
    cfg, _p, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    (d.backups / "clip_a.mp4").write_bytes(b"NOT-THE-ORIGINAL")
    with pytest.raises(ra.PlanStale, match="refusing to overwrite"):
        ra.backup_clip(d, row)


def test_backup_reuse_is_idempotent(tmp_path):
    cfg, _p, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    ra.backup_clip(d, row)
    ra.backup_clip(d, row)          # second call must accept the valid existing backup
    assert (d.backups / "clip_a.mp4").read_bytes() == b"OLD-PIXELS"


# ================ 8/12/13/14/28 · VALIDATORS + their NEGATIVE CONTROLS ============================

@pytest.mark.parametrize("break_it,msg", [
    ({"format": {"duration": "9.9"}}, "duration"),                                   # 12
    ({"streams_w": 720}, "width"),                                                   # 13
    ({"drop_audio": True}, "audio presence"),                                        # 14
    ({"fps": "12/1"}, "fps"),
    ({"audio_channels": 1}, "audio_channels"),
])
def test_validator_negative_controls_each_guard_CAN_fail(tmp_path, monkeypatch, break_it, msg):
    """Each guard must be able to FAIL. A validator that cannot fail is decoration."""
    cfg, _p, src = _corpus(tmp_path); row = _row(cfg, src)
    staged = tmp_path / "staged.mp4"; staged.write_bytes(b"NEW")

    def probe(*_a, **_k):
        p = _good_probe()
        if "format" in break_it: p["format"] = break_it["format"]
        if "streams_w" in break_it: p["streams"][0]["width"] = break_it["streams_w"]
        if "fps" in break_it: p["streams"][0]["avg_frame_rate"] = break_it["fps"]
        if "audio_channels" in break_it: p["streams"][1]["channels"] = break_it["audio_channels"]
        if break_it.get("drop_audio"): p["streams"] = [p["streams"][0]]
        return p
    monkeypatch.setattr(ra, "ffprobe_json", probe)
    ok, fails = ra.validate_output(str(staged), row)
    assert not ok and any(msg in f for f in fails), fails


def test_validator_PASSES_a_good_output(tmp_path, monkeypatch):
    cfg, _p, src = _corpus(tmp_path); row = _row(cfg, src)
    staged = tmp_path / "s.mp4"; staged.write_bytes(b"NEW")
    monkeypatch.setattr(ra, "ffprobe_json", _good_probe)
    ok, fails = ra.validate_output(str(staged), row)
    assert ok and not fails


def test_failed_validation_leaves_PRODUCTION_UNTOUCHED(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    monkeypatch.setattr(ra, "ffprobe_json", lambda *_a, **_k: {"format": {"duration": "99.0"},
                                                               "streams": _good_probe()["streams"]})
    before = ra.sha256_file(row["media_path"])
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_v")
    assert out["status"] == "VALIDATION_FAILED"
    assert ra.sha256_file(row["media_path"]) == before          # production byte-identical
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"
    assert json.loads(Path(row["sidecar_path"]).read_text())["fp"] == "fpold"


def test_render_failure_leaves_production_untouched(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch, rc=1)
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_r")
    assert out["status"] == "RENDER_FAILED"
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"


# ================ 9 · ATOMIC COMMIT: coherent mp4 + sidecar =======================================

def test_commit_produces_a_COHERENT_mp4_and_sidecar(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_c")
    assert out["status"] == "MIGRATED"
    assert Path(row["media_path"]).read_bytes() == b"NEW-PIXELS"
    assert json.loads(Path(row["sidecar_path"]).read_text())["fp"] == "fpnew"
    assert ra.inspect_clip(d, row) == ra.COMMITTED
    assert not (d.staging / "clip_a.mp4").exists()               # staging consumed by os.replace


# ================ 10 · THE CRASH WINDOW: mp4 replaced, sidecar stale =============================

def test_torn_state_is_DETECTED_and_healed_not_guessed(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    ra.backup_clip(d, row)
    Path(row["media_path"]).write_bytes(b"NEW-PIXELS")           # simulate: crashed after os.replace(mp4)
    assert ra.inspect_clip(d, row) == ra.TORN
    _stub_render(monkeypatch)
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_t")
    assert out["status"] == "healed_sidecar"
    assert json.loads(Path(row["sidecar_path"]).read_text())["fp"] == "fpnew"
    assert ra.inspect_clip(d, row) == ra.COMMITTED


def test_torn_heal_write_is_ATOMIC_a_crash_leaves_the_sidecar_valid_and_still_healable(tmp_path, monkeypatch):
    """CID-2: the heal writes the sidecar via the shared controlio.write_json_atomic boundary (mkstemp temp
    -> os.replace, exactly like the commit), so a crash at the rename leaves the stale-but-VALID sidecar — the
    next resume still reads TORN (auto-healable), never a partial JSON that reads as None and downgrades to a
    manual-repair AMBIGUOUS. Mutation-proof: revert the heal to a bare write_text and it never calls os.replace,
    so pytest.raises(OSError) fails. (ra.os.replace is the shared os module, so the patch reaches controlio.)"""
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    ra.backup_clip(d, row)
    Path(row["media_path"]).write_bytes(b"NEW-PIXELS")           # simulate: crashed after os.replace(mp4) -> TORN
    assert ra.inspect_clip(d, row) == ra.TORN
    def _boom(*_a, **_k): raise OSError("crash at the atomic rename")
    monkeypatch.setattr(ra.os, "replace", _boom)
    with pytest.raises(OSError):
        ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_th")
    assert ra._stored_fp(Path(row["sidecar_path"])) == "fpold"   # final sidecar VALID (stale), not torn
    assert ra.inspect_clip(d, row) == ra.TORN                    # still auto-healable on the next resume
    sp = Path(row["sidecar_path"])
    assert not list(sp.parent.glob(sp.name + ".*.tmp"))          # controlio's mkstemp temp cleaned on the failed rename


def test_write_sidecar_atomic_delegates_to_controlio_and_leaves_no_orphan_temp(tmp_path, monkeypatch):
    """Post-review (CID-2): the sidecar write ROUTES THROUGH the repo's shared atomic-write boundary
    (controlio.write_json_atomic — unique mkstemp temp -> os.replace -> cleanup) rather than a hand-rolled
    copy, per the coding guideline, so it can't drift from the accounts.json/personas.json guarantees.
    ATOMICITY (never a torn file) is the load-bearing property; a lost sidecar is recovered by the TORN-heal,
    which is why the fsync the old hand-rolled copy did is not required. Assert it DELEGATES with the right
    payload, writes the fp, and a SUCCESSFUL write leaves no orphan temp."""
    side = tmp_path / "clip_a.render.json"
    calls = []
    real = ra.write_json_atomic
    monkeypatch.setattr(ra, "write_json_atomic", lambda p, raw: (calls.append((Path(p), raw)) or real(p, raw)))
    ra._write_sidecar_atomic(str(side), "fpnew")
    assert calls == [(side, {"fp": "fpnew"})]                    # delegated to the shared boundary, not hand-rolled
    assert ra._stored_fp(side) == "fpnew"
    assert not list(side.parent.glob(side.name + ".*.tmp"))      # mkstemp temp consumed by os.replace — no orphan


def test_ambiguous_state_STOPS_never_guesses(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    Path(row["media_path"]).write_bytes(b"WHO-KNOWS")            # not preimage, not staged, sidecar unknown
    Path(row["sidecar_path"]).write_text(json.dumps({"fp": "mystery"}))
    assert ra.inspect_clip(d, row) == ra.AMBIGUOUS
    _stub_render(monkeypatch)
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_a")
    assert out["status"] == "AMBIGUOUS"
    assert Path(row["media_path"]).read_bytes() == b"WHO-KNOWS"  # untouched: we did NOT invent a story


# ================ 11 · UNCHANGED PIXELS is not an error ==========================================

def test_unchanged_pixels_keeps_the_original_and_its_sidecar(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch, out_bytes=b"OLD-PIXELS")           # the reframe renders identical bytes
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_u")
    assert out["status"] == "UNCHANGED_PIXELS"
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"
    assert json.loads(Path(row["sidecar_path"]).read_text())["fp"] == "fpold"   # sidecar NOT bumped


# ================ 15 · NON-FRAMING DRIFT is refused ==============================================

def test_non_framing_drift_REFUSES(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    monkeypatch.setattr(clipmod, "_render_fingerprint_payload",
                        lambda *a, **k: {"src": a[0], "ass": "A DIFFERENT HOOK", "focus": [0.5, 0.4], "geom": 4})
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_d")
    assert out["status"] == "NON_FRAMING_DRIFT"
    assert "ass" in out["error"]
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"


def test_fingerprint_divergence_REFUSES_before_rendering(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    monkeypatch.setattr(clipmod, "fingerprint_of_payload", lambda _p: "NOT-fpnew")
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_f")
    assert out["status"] == "FINGERPRINT_DIVERGED"
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"


# ================ 17/18 · ROLLBACK restores the EXACT original bytes ==============================

def test_per_clip_rollback_restores_exact_hashes(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    before_mp4, before_side = ra.sha256_file(row["media_path"]), ra.sha256_file(row["sidecar_path"])
    assert ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_rb")["status"] == "MIGRATED"
    r = ra.rollback_clip(d, row)
    assert r["status"] == "ROLLED_BACK"
    assert ra.sha256_file(row["media_path"]) == before_mp4
    assert ra.sha256_file(row["sidecar_path"]) == before_side
    assert Path(row["media_path"]).read_bytes() == b"OLD-PIXELS"


def test_rollback_is_idempotent(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_i")
    assert ra.rollback_clip(d, row)["status"] == "ROLLED_BACK"
    assert ra.rollback_clip(d, row)["status"] == "ROLLBACK_NOOP"     # twice is safe


def test_rollback_REFUSES_a_corrupt_backup(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_cb")
    (d.backups / "clip_a.mp4").write_bytes(b"CORRUPTED-BACKUP")
    assert ra.rollback_clip(d, row)["status"] == "ROLLBACK_BACKUP_CORRUPT"
    assert Path(row["media_path"]).read_bytes() == b"NEW-PIXELS"     # NOT restored from a bad backup


# ================ 19 · RESUME is idempotent ======================================================

def test_resume_skips_an_already_committed_clip(tmp_path, monkeypatch):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    assert ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_s")["status"] == "MIGRATED"
    out = ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_s")
    assert out["status"] == "already_committed"
    assert Path(row["media_path"]).read_bytes() == b"NEW-PIXELS"     # not re-rendered, not double-backed-up


# ================ 20 · CLEANUP cannot run prematurely ============================================

def test_cleanup_refuses_while_the_lock_is_held(tmp_path):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg, "rf_cl")
    d.plan.write_text(json.dumps({"planned": 1, "clips": [row]}))
    lock = ra.MigrationLock(cfg, "rf_cl"); lock.acquire()
    try:
        assert "refused" in ra.cleanup_run(paths, "rf_cl")
    finally:
        lock.release()


def test_cleanup_refuses_while_a_clip_is_ambiguous(tmp_path):
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg, "rf_amb")
    d.plan.write_text(json.dumps({"planned": 1, "clips": [row]}))
    Path(row["media_path"]).write_bytes(b"???")
    Path(row["sidecar_path"]).write_text(json.dumps({"fp": "???"}))
    assert "AMBIGUOUS" in ra.cleanup_run(paths, "rf_amb").get("refused", "")


def test_cleanup_retains_backups_by_DEFAULT(tmp_path, monkeypatch):
    """Nothing in the apply path deletes a backup. Cleanup is the ONLY deleter, and it is a separate verb."""
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg, "rf_keep")
    _stub_render(monkeypatch)
    ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_keep")
    assert (d.backups / "clip_a.mp4").exists()                       # still there after a SUCCESSFUL migrate


# ================ 16/23 · THE LEDGER AND EVERYTHING ON IT IS UNTOUCHED ===========================

def test_ledger_diff_detects_any_change(tmp_path):
    before = {"clips": {"c1": "aaa"}, "posts": {"p1": "bbb"}, "_ledger_file_sha256": "z"}
    after_same = dict(before)
    assert ra.ledger_diff(before, after_same) == []
    after_caption = {"clips": {"c1": "aaa"}, "posts": {"p1": "CHANGED"}, "_ledger_file_sha256": "z"}
    assert ra.ledger_diff(before, after_caption) == ["posts.p1"]     # a caption/hook/schedule edit lives here
    after_file = {**before, "_ledger_file_sha256": "MOVED"}
    assert "_ledger_file_sha256" in ra.ledger_diff(before, after_file)


def test_apply_clip_NEVER_calls_render_moment(tmp_path, monkeypatch):
    """render_moment owns the ledger (mints Clips, sets MomentState, stamps artifacts). Routing an existing
    clip through it would rewrite the workflow state we are contractually required to preserve."""
    cfg, paths, src = _corpus(tmp_path); row = _row(cfg, src); d = _dirs(cfg)
    _stub_render(monkeypatch)
    called = []
    monkeypatch.setattr(clipmod, "render_moment", lambda *a, **k: called.append(1))
    ra.apply_clip(paths, d, _FakeLed(), row, run_id="rf_nm")
    assert called == [], "the migration must NEVER route a clip through render_moment"


# ================ 22 · runtime writes are confined ==============================================

def test_declared_write_violations_flags_an_undeclared_production_write(tmp_path):
    cfg, _p, _s = _corpus(tmp_path)
    diff = {"added": [str(cfg.clips / "clip_a.mp4"), str(cfg.control / "reframe.lock"),
                      str(cfg.control / "SOMETHING_ELSE.json")], "removed": [], "changed": []}
    bad = ra.declared_write_violations(diff, cfg, "rf_1", {"clip_a"})
    assert len(bad) == 1 and "SOMETHING_ELSE" in bad[0]


def test_declared_writes_accept_the_two_files_we_own(tmp_path):
    cfg, _p, _s = _corpus(tmp_path)
    diff = {"added": [], "removed": [],
            "changed": [str(cfg.clips / "clip_a.mp4"), str(cfg.clips / "clip_a.render.json")]}
    assert ra.declared_write_violations(diff, cfg, "rf_1", {"clip_a"}) == []


def test_an_unplanned_clip_is_an_undeclared_write(tmp_path):
    cfg, _p, _s = _corpus(tmp_path)
    diff = {"added": [], "removed": [], "changed": [str(cfg.clips / "clip_OTHER.mp4")]}
    bad = ra.declared_write_violations(diff, cfg, "rf_1", {"clip_a"})
    assert bad and "clip_OTHER" in bad[0]


# ================ 24/25 · THE CLI: mutation is explicit, dry-run stays read-only =================

class _Args:
    def __init__(self, **kw):
        for k in ("dry_run", "apply", "status", "resume", "rollback", "cleanup", "clip", "manifest",
                  "run_id", "source", "plan_only", "limit", "scratch", "json"):
            setattr(self, k, kw.get(k))


def test_cli_MUTATION_IS_NEVER_THE_DEFAULT(tmp_path, capsys):
    """No verb at all -> refuse. Not "helpfully" fall through to anything."""
    from fanops.cli import cmd_reframe
    assert cmd_reframe(Config(root=tmp_path), _Args()) == 2
    assert "EXACTLY ONE" in capsys.readouterr().err


def test_cli_dry_run_and_apply_are_MUTUALLY_EXCLUSIVE(tmp_path, capsys):
    from fanops.cli import cmd_reframe
    assert cmd_reframe(Config(root=tmp_path), _Args(dry_run=True, apply=True)) == 2
    assert "EXACTLY ONE" in capsys.readouterr().err


def test_cli_apply_REFUSES_a_partial_manifest(tmp_path, capsys):
    """A corpus mutation may not rest on a corpus-wide claim the dry-run itself declined to make."""
    from fanops.cli import cmd_reframe
    man = tmp_path / "m.json"; man.write_text(json.dumps({"partial": True, "clips": []}))
    rc = cmd_reframe(Config(root=tmp_path), _Args(apply=True, manifest=str(man), run_id="rf_p"))
    assert rc == 2 and "PARTIAL" in capsys.readouterr().err


def test_cli_apply_REFUSES_without_a_manifest(tmp_path, capsys):
    from fanops.cli import cmd_reframe
    assert cmd_reframe(Config(root=tmp_path), _Args(apply=True, run_id="rf_n")) == 2
    assert "--manifest" in capsys.readouterr().err


def test_journal_is_append_only(tmp_path):
    cfg, _p, src = _corpus(tmp_path); d = _dirs(cfg)
    ra.journal_append(d, {"phase": "a", "status": "FAILED"})
    ra.journal_append(d, {"phase": "b", "status": "MIGRATED"})
    recs = ra.journal_read(d)
    assert [r["phase"] for r in recs] == ["a", "b"]
    assert recs[0]["status"] == "FAILED", "history must never be rewritten to make a retry look clean"
