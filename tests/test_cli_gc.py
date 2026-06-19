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
