# tests/test_metrics_path_ratchet.py — tier-c-metrics-ratchet: meta_graph stays off the metrics pull path.
"""Binary AST gate: zero `meta_graph` imports on the published-post metrics pull path.

Published-post metrics route through Postiz/Zernio (`fanops.post.metrics`). Meta Graph is operator
diagnostics only (verify-live, map-media, imported-media insights). This ratchet keeps Graph from
creeping back onto pull_metrics / learn_pass.

Scanned surfaces (no baseline, no comment-allowlist):
- `src/fanops/post/metrics/*.py` — whole module
- `track._metrics_client_for` / `track._default_list_posts` — pull_metrics fetch path only
- `cli_run.learn_pass` — learning pass must not import Graph for metrics

Deliberately NOT scanned (must stay green when they import meta_graph):
- `track.pull_imported_insights` — sole-source Graph read for ImportedMedia rows
- operator/doctor/verify-live paths elsewhere in the tree

Removal condition: permanent by policy — the gate is the product, not a temporary ratchet.
"""
from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_METRICS_DIR = _ROOT / "src" / "fanops" / "post" / "metrics"
_TRACK = _ROOT / "src" / "fanops" / "track.py"
_CLI_RUN = _ROOT / "src" / "fanops" / "cli_run.py"
_TRACK_PULL_FNS = ("_metrics_client_for", "_default_list_posts")
_FIX = "metrics pull uses Postiz/Zernio (fanops.post.metrics), not meta_graph"


def _imports_meta_graph(node: ast.AST) -> list[int]:
    """Line numbers of Import/ImportFrom nodes that reach meta_graph."""
    lines: list[int] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Import):
            for alias in n.names:
                if alias.name == "meta_graph" or alias.name.endswith(".meta_graph"):
                    lines.append(n.lineno)
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if mod == "meta_graph" or mod.endswith(".meta_graph"):
                lines.append(n.lineno)
            elif mod == "fanops" and any(a.name == "meta_graph" for a in n.names):
                lines.append(n.lineno)
    return lines


def _fn_by_name(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _scan_metrics_modules() -> list[tuple[str, int]]:
    offenders: list[tuple[str, int]] = []
    for path in sorted(_METRICS_DIR.glob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno in _imports_meta_graph(tree):
            offenders.append((rel, lineno))
    return offenders


def _scan_track_pull_path() -> list[tuple[str, int]]:
    rel = _TRACK.relative_to(_ROOT).as_posix()
    tree = ast.parse(_TRACK.read_text(encoding="utf-8"), filename=str(_TRACK))
    offenders: list[tuple[str, int]] = []
    for fn_name in _TRACK_PULL_FNS:
        fn = _fn_by_name(tree, fn_name)
        assert fn is not None, f"missing {fn_name} in track.py"
        for lineno in _imports_meta_graph(fn):
            offenders.append((f"{rel}:{fn_name}", lineno))
    return offenders


def _scan_learn_pass() -> list[tuple[str, int]]:
    rel = _CLI_RUN.relative_to(_ROOT).as_posix()
    tree = ast.parse(_CLI_RUN.read_text(encoding="utf-8"), filename=str(_CLI_RUN))
    fn = _fn_by_name(tree, "learn_pass")
    assert fn is not None, "missing learn_pass in cli_run.py"
    return [(f"{rel}:learn_pass", lineno) for lineno in _imports_meta_graph(fn)]


def _scan() -> list[tuple[str, int]]:
    return _scan_metrics_modules() + _scan_track_pull_path() + _scan_learn_pass()


def test_zero_meta_graph_imports_on_metrics_path():
    """Binary: zero forbidden meta_graph imports on the metrics pull path. Permanent by policy."""
    offenders = _scan()
    assert offenders == [], (
        "meta_graph import on metrics pull path: "
        + ", ".join(f"{loc}:{lineno}" for loc, lineno in sorted(offenders))
        + f" — {_FIX}"
    )


def test_negative_control_planted_import_is_detected():
    """Negative control: a planted meta_graph import must FAIL the check (count > 0).

    Without this, a finder that always returns 0 would pass green forever. The plant is a source
    string — not committed product code — so the tree stays clean while the detector is proven.
    """
    planted = (
        "def _metrics_client_for(cfg, backend, submission_ids):\n"
        "    from fanops import meta_graph\n"
        "    return meta_graph.list_posts\n"
    )
    fn = _fn_by_name(ast.parse(planted), "_metrics_client_for")
    assert fn is not None
    assert _imports_meta_graph(fn) == [2]


def test_pull_imported_insights_is_out_of_ratchet_scope():
    """Boundary proof: Graph on imported-media insights is allowed — not part of pull_metrics fetch."""
    planted = (
        "def pull_imported_insights(led, cfg):\n"
        "    from fanops import meta_graph\n"
        "    return meta_graph.media_insights(cfg, 'm1', 'REELS')\n"
        "def _metrics_client_for(cfg, backend, submission_ids):\n"
        "    from fanops.post.metrics import PostizMetricsClient\n"
        "    return PostizMetricsClient(cfg).list_posts\n"
    )
    tree = ast.parse(planted)
    insights = _fn_by_name(tree, "pull_imported_insights")
    pull = _fn_by_name(tree, "_metrics_client_for")
    assert insights is not None and pull is not None
    assert _imports_meta_graph(insights) == [2]
    assert _imports_meta_graph(pull) == []
