"""Unit tests for scripts/pr_collision_guard.py — the cross-open-PR hot-file collision detector.

Pins the pure logic: only hot files count, and a collision is any hot file this PR shares with another
open PR. The `gh` I/O is not exercised here (offline `--this-files`/`--others-json` cover the CLI).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import pr_collision_guard as pcg  # noqa: E402
import lane_guard  # noqa: E402


def _hot():
    return lane_guard.load_manifest(_ROOT / ".agents" / "lanes.json")["guard"]["hot_files"]


def test_hot_set_keeps_only_hot_files():
    hot = _hot()
    changed = ["src/fanops/models.py", "src/fanops/track.py", "tests/test_x.py", "docs/y.md"]
    assert pcg.hot_set(changed, hot) == {"src/fanops/models.py"}   # only the hot one survives


def test_find_collisions_flags_shared_hot_files():
    this_hot = {"src/fanops/crosspost.py", "src/fanops/models.py"}
    others = {
        7: {"src/fanops/crosspost.py"},          # collides on crosspost.py
        9: {"src/fanops/ledger.py"},             # no overlap
        12: {"src/fanops/models.py", "src/fanops/crosspost.py"},  # collides on both
    }
    col = pcg.find_collisions(this_hot, others)
    assert col == {
        "src/fanops/crosspost.py": [7, 12],
        "src/fanops/models.py": [12],
    }


def test_find_collisions_empty_when_no_overlap():
    assert pcg.find_collisions({"src/fanops/models.py"}, {3: {"src/fanops/ledger.py"}}) == {}
    assert pcg.find_collisions(set(), {3: {"src/fanops/models.py"}}) == {}


def test_cli_offline_no_hot_files_passes(capsys):
    rc = pcg.main(["--this-files", "docs/a.md,tests/test_b.py", "--others-json", '{"5":["src/fanops/models.py"]}'])
    assert rc == 0
    assert "no hot files" in capsys.readouterr().out


def test_cli_offline_collision_fails():
    rc = pcg.main(["--this-files", "src/fanops/models.py", "--others-json", '{"5":["src/fanops/models.py"]}'])
    assert rc == 1


def test_cli_offline_disjoint_passes():
    rc = pcg.main(["--this-files", "src/fanops/models.py", "--others-json", '{"5":["src/fanops/config.py"]}'])
    assert rc == 0
