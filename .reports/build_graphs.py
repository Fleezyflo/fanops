#!/usr/bin/env python3
"""Derive import/call graphs + unreferenced candidates from structural_index.json — stdlib only."""
from __future__ import annotations
import json, re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_INDEX = _HERE / "structural_index.json"


def _load_index() -> dict:
    return json.loads(_INDEX.read_text(encoding="utf-8"))


def _resolve_import(imp: str, current: str) -> str | None:
    if not imp or imp.startswith("."):
        return None
    return imp


def build_import_graph(modules: list[dict]) -> dict:
    by_mod = {m["module"]: m for m in modules if m.get("module")}
    graph: dict[str, dict] = {}
    for mod, m in by_mod.items():
        imports_from = []
        for imp in m.get("imports_from", []):
            target = _resolve_import(imp, mod)
            if target and (target in by_mod or target.startswith("fanops")):
                imports_from.append(target)
        graph[mod] = {"imports_from": sorted(set(imports_from)), "imported_by": []}
    for mod, data in graph.items():
        for imp in data["imports_from"]:
            if imp in graph:
                graph[imp]["imported_by"].append(mod)
    for mod in graph:
        graph[mod]["imported_by"] = sorted(set(graph[mod]["imported_by"]))
    return graph


def _callable_id(mod: str, name: str) -> str:
    return f"{mod}.{name}" if mod else name


def build_call_graph(modules: list[dict]) -> dict:
    index: dict[str, dict] = {}
    for m in modules:
        mod = m.get("module", "")
        if not mod:
            continue
        for fn in m.get("functions", []):
            cid = _callable_id(mod, fn["name"])
            index[cid] = {"module": mod, "name": fn["name"], "line": fn["line"], "kind": "function",
                          "calls": [], "called_by_in_repo": []}
        for cls in m.get("classes", []):
            for meth in cls.get("methods", []):
                cid = _callable_id(mod, f"{cls['name']}.{meth['name']}")
                index[cid] = {"module": mod, "name": f"{cls['name']}.{meth['name']}", "line": meth["line"],
                              "kind": "method", "calls": [], "called_by_in_repo": []}
    name_to_ids: dict[str, list[str]] = {}
    for cid in index:
        name_to_ids.setdefault(cid.rsplit(".", 1)[-1], []).append(cid)
    for m in modules:
        mod = m.get("module", "")
        if not mod:
            continue
        callers: list[tuple[str, list[str]]] = []
        for fn in m.get("functions", []):
            callers.append((_callable_id(mod, fn["name"]), fn.get("calls", [])))
        for cls in m.get("classes", []):
            for meth in cls.get("methods", []):
                callers.append((_callable_id(mod, f"{cls['name']}.{meth['name']}"), meth.get("calls", [])))
        for caller, calls in callers:
            if caller not in index:
                continue
            resolved = []
            for c in calls:
                for tgt in name_to_ids.get(c, []):
                    if tgt != caller:
                        resolved.append(tgt)
                        if caller not in index[tgt]["called_by_in_repo"]:
                            index[tgt]["called_by_in_repo"].append(caller)
            index[caller]["calls"] = sorted(set(resolved))
    for cid in index:
        index[cid]["called_by_in_repo"] = sorted(index[cid]["called_by_in_repo"])
    return {"callable_count": len(index), "callables": index}


def build_unreferenced(call_graph: dict) -> list[dict]:
    skip = re.compile(r"^(__|test_)")
    out = []
    for cid, meta in call_graph["callables"].items():
        name = meta["name"].split(".")[-1]
        if skip.search(name) or name in {"main"}:
            continue
        if not meta["called_by_in_repo"]:
            out.append({"id": cid, "module": meta["module"], "name": meta["name"], "line": meta["line"],
                        "kind": meta["kind"]})
    return sorted(out, key=lambda x: x["id"])


def main() -> int:
    data = _load_index()
    modules = data.get("modules", [])
    import_graph = build_import_graph(modules)
    call_graph = build_call_graph(modules)
    unreferenced = build_unreferenced(call_graph)
    (_HERE / "import_graph.json").write_text(json.dumps(import_graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (_HERE / "call_graph.json").write_text(json.dumps(call_graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (_HERE / "unreferenced_candidates.json").write_text(json.dumps(unreferenced, indent=2) + "\n", encoding="utf-8")
    print(f"modules={len(modules)} callables={call_graph['callable_count']} unreferenced={len(unreferenced)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
