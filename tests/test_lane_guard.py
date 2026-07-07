"""Unit tests for scripts/lane_guard.py — the mechanical lane file-ownership guard.

The guard is OPT-IN by branch prefix (a non-lane branch is ignored) and only restricts the
`hot_files` enumerated in .agents/lanes.json; every other path is unrestricted. These tests pin
that contract so the guard can be trusted at pre-push + CI without false positives.
"""
import sys, json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import lane_guard  # noqa: E402  (path insert must precede import)


def _manifest():
    return lane_guard.load_manifest(_ROOT / ".agents" / "lanes.json")


def test_shipped_manifest_is_valid_and_self_consistent():
    m = _manifest()
    assert "lanes" in m and "guard" in m
    lanes = set(m["lanes"])
    assert {"publish", "picking", "rfd", "ci"} <= lanes
    # every hot-file owner must be a declared lane
    for path, owner in m["guard"]["hot_files"].items():
        for lane in ([owner] if isinstance(owner, str) else owner):
            assert lane in lanes, f"{path} owned by unknown lane {lane!r}"
    # branch prefixes must be unique across lanes (no ambiguous mapping)
    seen = {}
    for name, cfg in m["lanes"].items():
        for p in cfg["branch_prefixes"]:
            assert p not in seen, f"prefix {p!r} claimed by {seen.get(p)} and {name}"
            seen[p] = name


def test_lane_for_branch_maps_prefixes_and_ignores_others():
    m = _manifest()
    assert lane_guard.lane_for_branch("publish/mol-128-x", m) == "publish"
    assert lane_guard.lane_for_branch("pick/mol-159-y", m) == "picking"
    assert lane_guard.lane_for_branch("picking/mol-159-y", m) == "picking"
    assert lane_guard.lane_for_branch("rfd/mol-166-z", m) == "rfd"
    assert lane_guard.lane_for_branch("ci/mol-190-sha-pin", m) == "ci"
    # non-lane branches -> None (guard becomes a no-op)
    assert lane_guard.lane_for_branch("cursor/whatever-655a", m) is None
    assert lane_guard.lane_for_branch("bycreamco/mol-181-ci-01", m) is None
    assert lane_guard.lane_for_branch("main", m) is None


def test_owned_hot_file_by_own_lane_is_allowed():
    m = _manifest()
    changed = ["src/fanops/post/run.py", "tests/test_publish.py", "docs/RUNBOOK.md"]
    lane, strays = lane_guard.evaluate(changed, "publish/mol-128-x", m)
    assert lane == "publish"
    assert strays == []


def test_straying_onto_another_lanes_hot_file_is_flagged():
    m = _manifest()
    # publish lane editing a picking-owned hot file
    lane, strays = lane_guard.evaluate(["src/fanops/models.py"], "publish/mol-1-x", m)
    assert lane == "publish"
    assert strays == ["src/fanops/models.py"]


def test_non_hot_files_are_never_restricted():
    m = _manifest()
    # track.py is not a hot file -> any lane may touch it
    lane, strays = lane_guard.evaluate(["src/fanops/track.py", "src/fanops/meta_graph.py"], "publish/mol-1", m)
    assert strays == []


def test_shared_hot_file_allows_each_of_its_owners():
    m = _manifest()
    # moments.py is shared by picking + rfd
    assert lane_guard.evaluate(["src/fanops/moments.py"], "picking/mol-1", m)[1] == []
    assert lane_guard.evaluate(["src/fanops/moments.py"], "rfd/mol-1", m)[1] == []
    # ...but publish does not own it
    assert lane_guard.evaluate(["src/fanops/moments.py"], "publish/mol-1", m)[1] == ["src/fanops/moments.py"]


def test_ci_lane_is_flagged_if_it_touches_a_source_hot_file():
    m = _manifest()
    changed = [".github/workflows/ci.yml", "pyproject.toml", "src/fanops/config.py"]
    lane, strays = lane_guard.evaluate(changed, "ci/mol-190-sha-pin", m)
    assert lane == "ci"
    assert strays == ["src/fanops/config.py"]   # config.py is publish-owned


def test_non_lane_branch_short_circuits_to_no_strays():
    m = _manifest()
    lane, strays = lane_guard.evaluate(["src/fanops/models.py"], "cursor/env-655a", m)
    assert lane is None
    assert strays == []


def test_lane_override_forces_a_lane_regardless_of_branch():
    m = _manifest()
    lane, strays = lane_guard.evaluate(["src/fanops/ledger.py"], "cursor/x", m, lane_override="picking")
    assert lane == "picking"
    assert strays == []   # picking owns ledger.py


def test_manifest_round_trips_as_json():
    # guard must never choke on the shipped file
    raw = (_ROOT / ".agents" / "lanes.json").read_text()
    json.loads(raw)
