"""Shared delta helpers for drift, impact, and baseline assembly.

One place for set-difference evidence strings and dependency-edge extraction so parallel
implementations cannot drift apart.
"""
from __future__ import annotations


def compile_edges(deps: dict) -> set[tuple[str, str]]:
    return {(s, t) for s, d in deps.get("edges", {}).items() for t in d["compile"]}


def lazy_only_edges(deps: dict) -> list[tuple[str, str]]:
    """Edges pinned as lazy that are not already module-level compile imports."""
    pinned = {(e["from"], e["to"]) for e in deps.get("lazy_upward", []) + deps.get("lazy_lateral", [])}
    return sorted(pinned - compile_edges(deps))


def set_delta(label: str, old: set, new: set) -> list[str]:
    return ([f"NEW {label}: {x}" for x in sorted(new - old)]
            + [f"REMOVED {label}: {x}" for x in sorted(old - new)])


def totals_delta(old_totals: dict | None, new_totals: dict | None) -> list[str]:
    old_totals = old_totals or {}
    new_totals = new_totals or {}
    ev: list[str] = []
    for k, ov in old_totals.items():
        nv = new_totals.get(k)
        if nv != ov:
            ev.append(f"{k}: {ov} -> {nv}")
    return ev


def route_pairs(surfaces: dict) -> set[tuple[str, tuple[str, ...]]]:
    return {(r["path"], tuple(r["methods"])) for r in surfaces.get("routes", [])}
