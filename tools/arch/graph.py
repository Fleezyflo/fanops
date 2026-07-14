"""Graph derivation: SCCs, the condensation, layer levels, and lazy-edge classification.

Three graphs, three meanings. Conflating them is itself an architectural error, and Cycle 5
shipped that error once (`C5-SC-3`) before correcting it. This module keeps them apart by
construction:

  G1   compile-time import graph   — module-level imports. A cycle here is a HARD load-order
                                     constraint and can become an ImportError at process start.
                                     G1 is NOT a DAG (it has one non-trivial SCC).
  G1c  the SCC-CONDENSATION of G1  — a DAG *by construction*. `level` is defined on this and
                                     ONLY this. A condensation cannot contain a backward edge,
                                     so the metric is meaningful only for the LAZY edges.
  G2   compile UNION lazy          — a static OVER-APPROXIMATION of runtime dependency. A lazy
                                     import materializes only if its function is CALLED. An SCC
                                     here BOUNDS blast radius; it does not ESTABLISH it.

G3, the runtime call graph, is NOT derived here and no claim rests on it.
"""
from __future__ import annotations


def tarjan_scc(nodes: list[str], adj: dict[str, list[str]]) -> list[list[str]]:
    """Strongly-connected components. Deterministic: nodes and each component are sorted."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]

    for root in nodes:
        if root in index:
            continue
        # iterative Tarjan — the tree is small but recursion depth is not a thing to gamble on
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter[0]
                counter[0] += 1
                stack.append(v)
                on_stack[v] = True
            recursed = False
            succs = adj.get(v, [])
            for i in range(pi, len(succs)):
                w = succs[i]
                if w not in index:
                    work[-1] = (v, i + 1)
                    work.append((w, 0))
                    recursed = True
                    break
                if on_stack.get(w):
                    low[v] = min(low[v], index[w])
            if recursed:
                continue
            if low[v] == index[v]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp))
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])

    return sorted(out, key=lambda c: (len(c), c))


def condense(nodes: list[str], adj: dict[str, list[str]], sccs: list[list[str]]) -> tuple[dict[str, int], dict[int, set[int]]]:
    """Map each node to its SCC id, and build the condensation's edge set (a DAG)."""
    scc_of: dict[str, int] = {}
    for i, comp in enumerate(sccs):
        for m in comp:
            scc_of[m] = i
    cond: dict[int, set[int]] = {i: set() for i in range(len(sccs))}
    for v in nodes:
        for w in adj.get(v, []):
            if scc_of[v] != scc_of[w]:
                cond[scc_of[v]].add(scc_of[w])
    return scc_of, cond


def levels(sccs: list[list[str]], cond: dict[int, set[int]]) -> dict[str, int]:
    """level(SCC) = 0 if it depends on nothing, else 1 + max(level(targets)).

    Defined on the CONDENSATION, which is a DAG by construction — so this always terminates
    and is entry-order independent. (The cycle-cutting DFS Cycle 5 first used was NOT: it
    returned 0 for any already-seen node, which is order-dependent inside an SCC. That bug
    moved six module levels and the headline count from 106 to 107.)
    """
    memo: dict[int, int] = {}

    def lvl(i: int) -> int:
        if i in memo:
            return memo[i]
        memo[i] = 0  # provisional; the condensation is acyclic so this is never read back
        targets = cond.get(i, set())
        memo[i] = 1 + max((lvl(t) for t in targets), default=-1) if targets else 0
        return memo[i]

    for i in range(len(sccs)):
        lvl(i)
    return {m: memo[i] for i, comp in enumerate(sccs) for m in comp}


def build(edges: dict[str, dict[str, list[str]]]) -> dict:
    """Derive G1 / G1c / G2 and classify every lazy edge against the layer levels."""
    nodes = sorted(edges)
    compile_adj = {n: edges[n]["compile"] for n in nodes}
    lazy_adj = {n: edges[n]["lazy"] for n in nodes}

    # G1 — compile-time
    g1_sccs = tarjan_scc(nodes, compile_adj)
    scc_of, cond = condense(nodes, compile_adj, g1_sccs)
    lvl = levels(g1_sccs, cond)
    nontrivial = [c for c in g1_sccs if len(c) > 1]

    # G2 — compile UNION lazy (static potential dependency; a SUPERSET of the call graph)
    g2_adj = {n: sorted(set(compile_adj[n]) | set(lazy_adj[n])) for n in nodes}
    g2_sccs = tarjan_scc(nodes, g2_adj)
    g2_nontrivial = sorted((c for c in g2_sccs if len(c) > 1), key=lambda c: (-len(c), c))

    # lazy edges vs the layer levels — the ONLY place `level` carries information
    upward, lateral, downward = [], [], []
    for src in nodes:
        for dst in lazy_adj[src]:
            e = {"from": src, "to": dst, "from_level": lvl[src], "to_level": lvl[dst],
                 "jump": lvl[dst] - lvl[src], "same_scc": scc_of[src] == scc_of[dst]}
            if lvl[dst] > lvl[src]:
                upward.append(e)
            elif lvl[dst] == lvl[src]:
                lateral.append(e)
            else:
                downward.append(e)
    upward.sort(key=lambda e: (-e["jump"], e["from"], e["to"]))
    lateral.sort(key=lambda e: (e["from"], e["to"]))

    n_compile = sum(len(v) for v in compile_adj.values())
    n_lazy = sum(len(v) for v in lazy_adj.values())
    n_typing = sum(len(edges[n]["typing"]) for n in nodes)
    n_optional = sum(len(edges[n]["optional"]) for n in nodes)

    fan_in: dict[str, int] = {n: 0 for n in nodes}
    for n in nodes:
        for t in compile_adj[n]:
            fan_in[t] += 1

    by_level: dict[str, list[str]] = {}
    for m, L in sorted(lvl.items()):
        by_level.setdefault(str(L), []).append(m)

    return {
        "totals": {
            "modules": len(nodes),
            "compile_edges_G1": n_compile,
            "lazy_edges": n_lazy,
            "typing_only_edges": n_typing,
            "optional_edges": n_optional,
            "G1_sccs_total": len(g1_sccs),
            "G1_non_trivial_sccs": len(nontrivial),
            "G1c_levels": (max(lvl.values()) + 1) if lvl else 0,
            "lazy_edges_to_equal_or_higher_level": len(upward) + len(lateral),
            "lazy_edges_strictly_upward": len(upward),
            "lazy_edges_lateral": len(lateral),
            # BOTH are emitted, because "how many SCCs" is ambiguous and the ambiguity has already
            # bitten once: Cycle 5 reported `G2_module_sccs: 8`, which is the NON-TRIVIAL count,
            # while its enumerated list made that implicit. A derived number inherits the soundness
            # of its definition.
            "G2_module_sccs_total": len(g2_sccs),
            "G2_non_trivial_sccs": len(g2_nontrivial),
            "G2_largest_scc_size": max((len(c) for c in g2_sccs), default=0),
        },
        "G1_non_trivial_sccs": [sorted(c) for c in nontrivial],
        "G2_non_trivial_sccs": g2_nontrivial,
        "levels": {m: lvl[m] for m in nodes},
        "levels_by_level": by_level,
        "lazy_upward": upward,
        "lazy_lateral": lateral,
        "lazy_downward_count": len(downward),
        "fan_in_compile": dict(sorted(fan_in.items(), key=lambda kv: (-kv[1], kv[0]))),
        "fan_out_compile": dict(sorted(((n, len(compile_adj[n])) for n in nodes),
                                       key=lambda kv: (-kv[1], kv[0]))),
    }
