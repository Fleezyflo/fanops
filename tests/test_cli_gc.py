# tests/test_cli_gc.py
# content-lifecycle Phase 1 (wipe-safety): gc refuses keep_days < 1. keep_days=0 sets cutoff=now and
# sweeps EVERY retired/analyzed .mp4 regardless of age — a one-keystroke wipe of reusable renders
# (cross-account reuse may still need them). Negative is nonsense. Clean exit 2, no deletion.
import pytest

from fanops.config import Config
from fanops.cli import cmd_gc
from fanops.ledger import Ledger
from fanops.models import PostState

def test_gc_rejects_zero_keep_days(tmp_path):
    assert cmd_gc(Config(root=tmp_path), 0) == 2

def test_gc_rejects_negative_keep_days(tmp_path):
    assert cmd_gc(Config(root=tmp_path), -5) == 2

def test_gc_accepts_valid_keep_days(tmp_path):
    # a positive keep_days runs normally (empty ledger -> 0 removed, exit 0)
    assert cmd_gc(Config(root=tmp_path), 30) == 0

# ---- content-lifecycle Phase 3: gc default from cfg.gc_keep_days + 05_scheduled cleanup ----
def test_gc_cli_default_uses_cfg_keep_days(monkeypatch, tmp_path):
    # `fanops gc` with NO --keep-days resolves to cfg.gc_keep_days (not the old hardcoded 30).
    from fanops.cli import main
    monkeypatch.setenv("FANOPS_GC_KEEP_DAYS", "7")
    monkeypatch.setattr("fanops.cli.Config", lambda: Config(root=tmp_path))   # main() builds Config() with cwd
    captured = {}
    def _fake_gc(cfg, keep_days):
        captured["keep_days"] = keep_days; return 0
    monkeypatch.setattr("fanops.cli.cmd_gc", _fake_gc)
    main(["gc"])
    assert captured["keep_days"] == 7
    captured.clear()
    main(["gc", "--keep-days", "14"])    # explicit wins
    assert captured["keep_days"] == 14

def test_gc_cleans_scheduled_payloads(tmp_path):
    # content-lifecycle Phase 3: gc removes OLD 05_scheduled/*.json dryrun payloads (older than cutoff),
    # keeps recent ones, and NEVER touches 06_published/ (the durable archive).
    import os, time, json
    cfg = Config(root=tmp_path)
    cfg.scheduled.mkdir(parents=True, exist_ok=True); cfg.published.mkdir(parents=True, exist_ok=True)
    old = cfg.scheduled / "old.json"; old.write_text(json.dumps({"x": 1}))
    new = cfg.scheduled / "new.json"; new.write_text(json.dumps({"x": 2}))
    keeper = cfg.published / "2026-06-01" / "p.json"; keeper.parent.mkdir(parents=True, exist_ok=True); keeper.write_text("{}")
    old_t = time.time() - 40 * 86400; os.utime(old, (old_t, old_t))    # 40 days old -> swept at keep_days=30
    assert cmd_gc(cfg, 30) == 0
    assert not old.exists() and new.exists()                          # old removed, recent kept
    assert keeper.exists()                                            # published archive untouched


# ---- T3.9: the clip sweep reclaims by DERIVED disposition, with the live-post pin carried in ----
# gc used to key on the STORED clip label. With the write-time copy-down gone, a clip under a retired
# moment reads `queued` forever, so its file would be permanently unreclaimable — hence Ledger.is_suppressed.
# The pin is _LIVE_POST_STATES only (may-be-on-platform). MOL-818: failed/error deliberately do NOT pin —
# oversize shrink cannot reach a suppressed clip's file (_refuse_retired before apply_shrink_to_post).
def _gc_fixture(tmp_path, *, clip_state, moment_state, post_state=None, days_old=60):
    """One suppressed-or-live lineage with one on-disk clip file older than the cutoff. Returns (cfg, file)."""
    import os, time
    from fanops.models import Clip, Moment, Platform, Post
    cfg = Config(root=tmp_path)
    f = cfg.clips / f"{clip_state.value}-{moment_state.value}.mp4"
    f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"X")
    stamp = time.time() - days_old * 86400; os.utime(f, (stamp, stamp))
    led = Ledger.load(cfg)
    led.add_moment(Moment(id="m1", parent_id="s1", state=moment_state, start=0.0, end=8.0, reason="r"))
    led.add_clip(Clip(id="c1", parent_id="m1", path=str(f), state=clip_state))
    if post_state is not None:
        # `published`/`analyzed` are terminal-success states the model gates on a real permalink (R1).
        led.add_post(Post(id="p1", parent_id="c1", account="a", account_id="1", platform=Platform.instagram,
                          caption="c", state=post_state, public_url="https://example.test/p/1"))
    led.save()
    return cfg, f

def test_gc_reclaims_a_clip_suppressed_only_by_its_moment(tmp_path):
    # The case that becomes UNREACHABLE without the swap: the clip's own label never says `retired`.
    from fanops.models import ClipState, MomentState
    cfg, f = _gc_fixture(tmp_path, clip_state=ClipState.queued, moment_state=MomentState.retired)
    assert cmd_gc(cfg, 30) == 0
    assert not f.exists()

@pytest.mark.parametrize("live", Ledger._LIVE_POST_STATES)
def test_gc_never_touches_a_clip_a_live_post_points_at(tmp_path, live):
    # Parametrised over the tuple ITSELF — self-adjusting if _LIVE_POST_STATES ever changes.
    from fanops.models import ClipState, MomentState
    cfg, f = _gc_fixture(tmp_path, clip_state=ClipState.queued, moment_state=MomentState.retired, post_state=live)
    assert cmd_gc(cfg, 30) == 0
    assert f.exists()

def test_gc_pin_is_scoped_to_live_states_not_to_any_post(tmp_path):
    # Negative control for the pin: a post that is NOT in _LIVE_POST_STATES must not protect the file.
    from fanops.models import ClipState, MomentState, PostState
    cfg, f = _gc_fixture(tmp_path, clip_state=ClipState.queued, moment_state=MomentState.retired,
                         post_state=PostState.rejected)
    assert cmd_gc(cfg, 30) == 0
    assert not f.exists()

@pytest.mark.parametrize("dead", (PostState.failed, PostState.error))
def test_gc_reclaims_failed_or_error_under_suppressed_lineage(tmp_path, dead):
    # MOL-818 Branch A: failed/error do not join the pin; media under suppressed lineage is reclaimable
    # because oversize retry is structurally refused before shrink (_refuse_retired / can_promote).
    from fanops.models import ClipState, MomentState
    cfg, f = _gc_fixture(tmp_path, clip_state=ClipState.queued, moment_state=MomentState.retired,
                         post_state=dead)
    assert cmd_gc(cfg, 30) == 0
    assert not f.exists()

def test_gc_still_reclaims_analyzed_clips(tmp_path):
    # Unchanged arm: an `analyzed` clip is swept on its own label, live moment and all.
    from fanops.models import ClipState, MomentState
    cfg, f = _gc_fixture(tmp_path, clip_state=ClipState.analyzed, moment_state=MomentState.clipped)
    assert cmd_gc(cfg, 30) == 0
    assert not f.exists()

def test_gc_still_reclaims_a_retired_clip_with_no_live_post(tmp_path):
    from fanops.models import ClipState, MomentState
    cfg, f = _gc_fixture(tmp_path, clip_state=ClipState.retired, moment_state=MomentState.clipped)
    assert cmd_gc(cfg, 30) == 0
    assert not f.exists()

def test_gc_keeps_a_retired_clip_whose_post_is_still_live(tmp_path):
    # DISCLOSED NARROWING (this ticket, not parity): today a stored-`retired` clip's file is swept
    # regardless of its posts, and adjust.retire() produces exactly this shape — a measured loser's clip
    # retired while its post sits `analyzed`. The file is now kept until the post leaves the live states.
    from fanops.models import ClipState, MomentState, PostState
    cfg, f = _gc_fixture(tmp_path, clip_state=ClipState.retired, moment_state=MomentState.clipped,
                         post_state=PostState.analyzed)
    assert cmd_gc(cfg, 30) == 0
    assert f.exists()

def test_gc_leaves_a_live_lineage_alone(tmp_path):
    from fanops.models import ClipState, MomentState
    cfg, f = _gc_fixture(tmp_path, clip_state=ClipState.queued, moment_state=MomentState.clipped)
    assert cmd_gc(cfg, 30) == 0
    assert f.exists()
