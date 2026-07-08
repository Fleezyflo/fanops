"""Unit tests for scripts/repo_sweep.py — the read-only full-repo mess reporter."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import repo_sweep as rs  # noqa: E402


def test_classify_pr_states():
    assert rs.classify_pr("CONFLICTING", "DIRTY") == "conflict"
    assert rs.classify_pr("MERGEABLE", "BEHIND") == "behind"
    assert rs.classify_pr("MERGEABLE", "BLOCKED") == "blocked"
    assert rs.classify_pr("MERGEABLE", "CLEAN") == "clean"
    assert rs.classify_pr("UNKNOWN", "UNSTABLE") == "unstable"


def test_is_artifact():
    assert rs.is_artifact("src/foo.py.orig") is True
    assert rs.is_artifact("a/b/merge.rej") is True
    assert rs.is_artifact(".env.production.bak") is True
    assert rs.is_artifact("notes.tmp") is True
    assert rs.is_artifact(".DS_Store") is True
    assert rs.is_artifact("src/fanops/models.py") is False
    assert rs.is_artifact("README.md") is False


def test_has_conflict_markers():
    conflicted = "line\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n"
    assert rs.has_conflict_markers(conflicted) is True
    assert rs.has_conflict_markers("clean file\nno markers\n") is False


def test_stale_branches_excludes_main_and_recent():
    now = 1_000_000_000
    refs = [
        ("origin/main", now - 999 * 86400),               # excluded (main)
        ("origin/cursor/old-thing", now - 60 * 86400),    # stale
        ("origin/cursor/fresh", now - 2 * 86400),         # recent
    ]
    out = rs.stale_branches(refs, now, days=30)
    assert out == ["origin/cursor/old-thing"]
