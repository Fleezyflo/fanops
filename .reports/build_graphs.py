#!/usr/bin/env python3
"""Derive import/call graphs from structural_index.json (stdlib-only).

Usage:
  python3 .reports/build_graphs.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
INDEX = HERE / "structural_index.json"
IMPORT_GRAPH = HERE / "import_graph.json"
CALL_GRAPH = HERE / "call_graph.json"
UNREF = HERE / "unreferenced_candidates.json"

_SKIP = frozenset({"self", "cls", "super", "print", "len", "str", "int", "float", "bool",
                   "list", "dict", "set", "tuple", "type", "isinstance", "getattr", "setattr",
                   "hasattr", "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
                   "min", "max", "sum", "any", "all", "open", "Path", "Exception", "ValueError",
                   "KeyError", "RuntimeError", "TypeError", "json", "os", "sys", "time", "logging"})


def _resolve_import(mod: str, imp: dict, current: str) -> str | None:
    if imp["kind"] == "import":
        return imp["module"]
    base = imp["module"]
    if base.startswith("."):
        parts = current.split(".")
        level = len(base) - len(base.lstrip("."))
        rel = base.lstrip(".")
        parent = parts[: max(0, len(parts) - level)]
        if rel:
            return ".".join(parent + rel.split("."))
        return ".".join(parent) if parent else None
    return base


def _build_import_graph(modules: dict[str, dict]) -> dict:
    graph: dict[str, dict] = {}
    for name in modules:
        graph[name] = {"imports_from": [], "imported_by": []}
    for name, info in modules.items():
        seen: set[str] = set()
        for imp in info.get("imports", []):
            target = _resolve_import(name, imp, name)
            if not target:
                continue
            # map to a known module prefix
            candidates = [m for m in modules if m == target or m.startswith(target + ".")]
            for c in candidates:
                if c not in seen:
                    seen.add(c)
                    graph[name]["imports_from"].append(c)
                    graph[c]["imported_by"].append(name)
        graph[name]["imports_from"] = sorted(set(graph[name]["imports_from"]))
        graph[name]["imported_by"] = sorted(set(graph[name]["imported_by"]))
    return graph


def _callable_entries(modules: dict[str, dict]) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for mod, info in modules.items():
        for fn in info.get("functions", []):
            q = f"{mod}.{fn['name']}"
            entries[q] = {"module": mod, "name": fn["name"], "line": fn["line"],
                         "kind": "function", "calls": [], "called_by_in_repo": []}
            for c in fn.get("calls", []):
                base = c.split(".")[0]
                entries[q]["calls"].append(c)
        for cls in info.get("classes", []):
            cq = f"{mod}.{cls['name']}"
            entries[cq] = {"module": mod, "name": cls["name"], "line": cls["line"],
                          "kind": "class", "calls": [], "called_by_in_repo": []}
            for m in cls.get("methods", []):
                mq = f"{mod}.{cls['name']}.{m['name']}"
                entries[mq] = {"module": mod, "name": m["name"], "line": m["line"],
                              "kind": "method", "class": cls["name"],
                              "calls": [], "called_by_in_repo": []}
                for c in m.get("calls", []):
                    entries[mq]["calls"].append(c)
    return entries


def _resolve_call(caller_mod: str, callee: str, entries: dict[str, dict], modules: dict) -> list[str]:
    hits: list[str] = []
    if "." in callee:
        for q in entries:
            if q.endswith("." + callee.split(".")[-1]) and q.startswith(caller_mod.split(".")[0]):
                pass
        # exact / suffix match across repo
        for q in entries:
            if q == callee or q.endswith("." + callee):
                hits.append(q)
        if hits:
            return hits
    # same-module bare name
    local = f"{caller_mod}.{callee}"
    if local in entries:
        return [local]
    # any module exporting this bare name
    for q, meta in entries.items():
        if meta["name"] == callee and meta["kind"] in ("function", "method"):
            hits.append(q)
    return hits


def _build_call_graph(modules: dict[str, dict]) -> dict:
    entries = _callable_entries(modules)
    for q, meta in entries.items():
        mod = meta["module"]
        resolved: set[str] = set()
        for c in meta.get("calls", []):
            base = c.split(".")[0]
            if base in _SKIP or base[0].isupper() and "." not in c:
                continue
            for hit in _resolve_call(mod, c, entries, modules):
                resolved.add(hit)
        meta["calls"] = sorted(resolved)
    for q, meta in entries.items():
        for target in meta["calls"]:
            if target in entries:
                entries[target]["called_by_in_repo"].append(q)
    for meta in entries.values():
        meta["called_by_in_repo"] = sorted(set(meta["called_by_in_repo"]))
    return entries


def _unreferenced(entries: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for q, meta in sorted(entries.items()):
        if meta["kind"] not in ("function", "method"):
            continue
        if meta["name"].startswith("_") and meta["name"] not in ("__init__",):
            continue
        if not meta["called_by_in_repo"]:
            out.append({"qualified_name": q, "module": meta["module"], "name": meta["name"],
                        "line": meta["line"], "kind": meta["kind"]})
    return out


def main() -> None:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    modules = data["modules"]
    import_graph = _build_import_graph(modules)
    call_graph = _build_call_graph(modules)
    unref = _unreferenced(call_graph)
    IMPORT_GRAPH.write_text(json.dumps(import_graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    CALL_GRAPH.write_text(json.dumps(call_graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    UNREF.write_text(json.dumps({"count": len(unref), "candidates": unref}, indent=2) + "\n", encoding="utf-8")
    funcs = sum(1 for v in call_graph.values() if v["kind"] == "function")
    methods = sum(1 for v in call_graph.values() if v["kind"] == "method")
    print(f"modules={data['module_count']} callables={funcs + methods} "
          f"(functions={funcs} methods={methods}) unreferenced_leads={len(unref)}")


if __name__ == "__main__":
    main()
