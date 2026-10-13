"""docs/CODEMAPS/partition.json assigns every tracked src/fanops module to exactly one of C1–C10, and the
full-trace-index.md coverage table is that map rendered — a module added without a cluster fails here."""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CODEMAPS = _ROOT / "docs" / "CODEMAPS"
_CLUSTERS = [f"C{i}" for i in range(1, 11)]


def _live_modules() -> set[str]:
    out = subprocess.run(["git", "ls-files", "src/fanops/*.py", "src/fanops/**/*.py"],
                         cwd=_ROOT, capture_output=True, text=True, check=True).stdout
    return set(out.split())


def _partition() -> dict:
    return json.loads((_CODEMAPS / "partition.json").read_text(encoding="utf-8"))


def test_live_partition_covers_every_module():
    live = _live_modules()
    part = _partition()
    assert list(part["clusters"]) == _CLUSTERS
    seen: dict[str, str] = {}
    for cid, cluster in part["clusters"].items():
        for mod in cluster["modules"]:
            assert mod not in seen, f"{mod} in both {seen[mod]} and {cid}"
            seen[mod] = cid
    assert sorted(set(seen) - live) == [], "partition names modules that are not tracked"
    assert sorted(live - set(seen)) == [], "tracked modules with no cluster in docs/CODEMAPS/partition.json"
    assert part["live_py_count"] == len(live)


def test_trace_index_coverage_table_matches_partition():
    part = _partition()
    text = (_CODEMAPS / "full-trace-index.md").read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if re.match(r"^\| C\d+ \|", ln) and ln.count("|") == 6]
    table: dict[str, tuple[list[str], int]] = {}
    for row in rows:
        cells = [c.strip() for c in row.split("|")]
        m = re.fullmatch(r"(.*) \((\d+)\)", cells[3])
        assert m, row
        table[cells[1]] = ([s.strip() for s in m.group(1).split(",")], int(m.group(2)))
    assert list(table) == _CLUSTERS
    for cid, (stems, count) in table.items():
        want = [p[len("src/fanops/"):-len(".py")] for p in part["clusters"][cid]["modules"]]
        assert stems == want, cid
        assert count == len(want), cid
    assert sum(count for _, count in table.values()) == part["live_py_count"]
