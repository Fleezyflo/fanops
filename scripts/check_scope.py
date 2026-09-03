#!/usr/bin/env python3
"""Map changed src/tests .py paths -> scoped pytest files for scripts/check.sh.

Convention first (studio/, post/ subdirs, test_studio_* names), then a small override table
for modules whose tests use a different basename. Stdlib-only — safe to call from bash."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_OVERRIDES_PATH = Path(__file__).with_name("check_scope_overrides.json")


def _load_overrides() -> dict[str, tuple[str, ...]]:
    raw = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {k: tuple(v) for k, v in raw.items()}


_OVERRIDES: dict[str, tuple[str, ...]] = _load_overrides()


def _exists(rel: str) -> str | None:
    p = ROOT / rel
    return rel if p.is_file() else None


def _convention_candidates(src: str) -> list[str]:
    """Ordered candidate test paths for a changed src/fanops/... module."""
    p = Path(src)
    if len(p.parts) < 3 or p.parts[0] != "src" or p.parts[1] != "fanops":
        return []
    rel = Path(*p.parts[2:])  # fanops/...
    stem = rel.stem
    parts = rel.parts
    cands: list[str] = []
    if len(parts) == 1:
        cands.append(f"tests/test_{stem}.py")
    elif parts[0] == "studio":
        cands.append(f"tests/test_studio_{stem}.py")
        if stem.startswith("actions_"):
            cands.append(f"tests/test_{stem}.py")
        elif stem.startswith("app_routes_"):
            route = stem.removeprefix("app_routes_")
            cands.append(f"tests/test_studio_{route}.py")
        elif stem.startswith("views_"):
            view = stem.removeprefix("views_")
            cands.append(f"tests/test_studio_{view}.py")
    elif parts[0] == "post":
        cands.append(f"tests/test_post_{stem}.py")
        cands.append(f"tests/test_{stem}.py")
    out: list[str] = []
    for c in cands:
        hit = _exists(c)
        if hit and hit not in out:
            out.append(hit)
    return out


def resolve_tests(changed: list[str]) -> list[str]:
    """Return sorted unique pytest files to run for the given changed paths."""
    want: dict[str, None] = {}
    for f in changed:
        if f.startswith("tests/") and (ROOT / f).is_file():
            want[f] = None
            continue
        if not f.startswith("src/fanops/") or not f.endswith(".py"):
            continue
        hits = _convention_candidates(f)
        extra = [h for h in _OVERRIDES.get(f, ()) if _exists(h)]
        if not hits:
            hits = extra
        else:
            for h in extra:
                if h not in hits:
                    hits.append(h)
        for t in hits:
            want[t] = None
    return sorted(want)


def orphan_src_modules(changed: list[str]) -> list[str]:
    """Return changed src/fanops/*.py paths (excl __init__) with no scoped test mapping."""
    out: list[str] = []
    for f in changed:
        if not f.startswith("src/fanops/") or not f.endswith(".py"):
            continue
        if f.endswith("__init__.py"):
            continue
        if not resolve_tests([f]):
            out.append(f)
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if args and args[0] == "--orphans":
        for o in orphan_src_modules(args[1:]):
            print(o)
        return 0
    for t in resolve_tests(args):
        print(t)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
