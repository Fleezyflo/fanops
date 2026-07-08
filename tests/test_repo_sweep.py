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


def test_artifact_paths_filters_and_dedupes():
    paths = ["a.orig", "src/x.py", "a.orig", "b.rej", "README.md", ".DS_Store"]
    assert rs.artifact_paths(paths) == [".DS_Store", "a.orig", "b.rej"]


def test_stale_branches_excludes_main_and_recent():
    now = 1_000_000_000
    refs = [
        ("origin/main", now - 999 * 86400),               # excluded (main)
        ("origin/cursor/old-thing", now - 60 * 86400),    # stale
        ("origin/cursor/fresh", now - 2 * 86400),         # recent
    ]
    out = rs.stale_branches(refs, now, days=30)
    assert out == ["origin/cursor/old-thing"]


def _clean_rep(**over):
    rep = {"open_prs": [], "conflicts": [], "behind": [], "stale_branches": [],
           "artifacts": [], "unresolved_conflicts": []}
    rep.update(over)
    return rep


def test_is_done_true_only_when_landed_and_pristine():
    assert rs.is_done(_clean_rep()) is True
    assert rs.is_done(_clean_rep(open_prs=[{"number": 398}])) is False        # unlanded work
    assert rs.is_done(_clean_rep(unresolved_conflicts=["a.py"])) is False     # unresolved merge
    assert rs.is_done(_clean_rep(stale_branches=["origin/x"])) is False
    assert rs.is_done(_clean_rep(artifacts=["a.orig"])) is False


def test_outstanding_lists_reasons():
    reasons = rs.outstanding(_clean_rep(open_prs=[{"number": 1}], artifacts=["a.orig"]))
    assert any("open PR" in r for r in reasons)
    assert any("artifact" in r for r in reasons)
    assert rs.outstanding(_clean_rep()) == []


def test_require_pristine_exit_codes():
    assert rs._require_pristine_exit(_clean_rep()) == 0                       # done -> 0
    assert rs._require_pristine_exit(_clean_rep(open_prs=[{"number": 1}])) == 3   # not done -> 3
