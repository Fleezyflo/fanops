# tests/test_cli_gc.py
# content-lifecycle Phase 1 (wipe-safety): gc refuses keep_days < 1. keep_days=0 sets cutoff=now and
# sweeps EVERY retired/analyzed .mp4 regardless of age — a one-keystroke wipe of reusable renders
# (cross-account reuse may still need them). Negative is nonsense. Clean exit 2, no deletion.
from fanops.config import Config
from fanops.cli import cmd_gc

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
