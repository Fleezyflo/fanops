#!/usr/bin/env python3
"""Aggregate pytest --durations output by module; find 50% threshold."""
from __future__ import annotations
import re
import sys
from collections import defaultdict
from pathlib import Path


DUR_RE = re.compile(r"^(\d+\.\d+)s\s+call\s+(.+)$")
SUMMARY_RE = re.compile(r"(\d+) passed.*in ([\d.]+)s")


def parse_durations(text: str) -> tuple[list[tuple[float, str]], float | None]:
    lines = text.splitlines()
    durations: list[tuple[float, str]] = []
    total: float | None = None
    in_slow = False
    for line in lines:
        m = SUMMARY_RE.search(line)
        if m:
            total = float(m.group(2))
        if "slowest" in line and "durations" in line:
            in_slow = True
            continue
        if in_slow:
            m2 = DUR_RE.match(line.strip())
            if m2:
                durations.append((float(m2.group(1)), m2.group(2)))
            elif line.strip() == "":
                in_slow = False
    return durations, total


def module_of(nodeid: str) -> str:
    # nodeid like tests/test_foo.py::TestBar::test_baz or tests/test_foo.py::test_baz
    part = nodeid.split("::")[0]
    return part


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/unit-durations.txt")
    text = path.read_text()
    durations, total = parse_durations(text)
    if not durations:
        print("No durations parsed", file=sys.stderr)
        sys.exit(1)
    by_mod: dict[str, float] = defaultdict(float)
    for t, node in durations:
        by_mod[module_of(node)] += t
    ranked_mod = sorted(by_mod.items(), key=lambda x: -x[1])
    sum_all = sum(t for t, _ in durations)
    print(f"Parsed {len(durations)} duration lines; sum(call)={sum_all:.1f}s pytest_total={total}")
    print("\nTop modules (from duration sample):")
    cum = 0.0
    for mod, sec in ranked_mod[:30]:
        pct = 100 * sec / sum_all if sum_all else 0
        cum += sec
        print(f"  {sec:7.2f}s ({pct:5.1f}%)  {mod}")
    print("\nTop individual tests:")
    for t, node in durations[:25]:
        print(f"  {t:7.2f}s  {node}")
  # 50% threshold from full duration list would need --durations=0; estimate from sample
    half = sum_all / 2
    cum = 0.0
    mods_50 = []
    for mod, sec in ranked_mod:
        cum += sec
        mods_50.append((mod, sec))
        if cum >= half:
            break
    print(f"\nModules reaching ~50% of sampled call time ({half:.1f}s):")
    for mod, sec in mods_50:
        print(f"  {sec:7.2f}s  {mod}")


if __name__ == "__main__":
    main()
