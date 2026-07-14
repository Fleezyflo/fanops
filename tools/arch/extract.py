"""Deterministic architecture extraction — the ONLY producer of DERIVED_FROM_CODE facts.

Stdlib only. One AST pass over `src/fanops`. Every number this module emits is a fact about
the code at a given commit; nothing here is a judgement.

Two rules govern this file:

1. NEVER SILENTLY OMIT AN UNSUPPORTED CONSTRUCT. A dynamic import, a computed env key, a
   route path built from a variable — each is recorded in `unsupported` with evidence, not
   dropped. A census is only as good as its query (Cycle-2 method note, and the thing that
   made Cycle 5 report 39 network sites when the real number was 15).

2. THE RATCHET TESTS ARE CANONICAL, NOT THIS FILE. `tests/test_swallow_ratchet.py` and
   `tests/test_internal_prints_routed.py` already enforce the swallow/print budgets in CI.
   This module reproduces their *measurement* so a third party can compare the test's declared
   baseline against reality — it does not replace them.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

GENERATOR_VERSION = "arch/1.0.0"

# ── what counts as a side effect ────────────────────────────────────────────────────────────
_SUBPROCESS_FNS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
_REQUESTS_VERBS = frozenset({"get", "post", "put", "delete", "patch", "head", "options", "request"})
_ENV_READ_FNS = frozenset({"getenv"})           # os.getenv("K")
_LOCK_FNS = frozenset({"flock", "lock", "_file_lock"})
_ROUTE_DECORATORS = frozenset({"route", "get", "post", "put", "delete", "patch"})
_MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


def _dotted(node: ast.AST) -> str | None:
    """Resolve an attribute/name chain to a dotted string ('os.environ.get'), else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _literal_str(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


# ── the per-module visitor ──────────────────────────────────────────────────────────────────
@dataclass
class ModuleFacts:
    name: str
    path: str
    imports: list[dict] = field(default_factory=list)        # {target_raw, names, position, line}
    subprocess_sites: list[dict] = field(default_factory=list)
    network_sites: list[dict] = field(default_factory=list)
    txn_sites: list[dict] = field(default_factory=list)
    lock_sites: list[dict] = field(default_factory=list)
    mkdtemp_sites: list[dict] = field(default_factory=list)
    rmtree_sites: list[dict] = field(default_factory=list)
    env_reads: list[dict] = field(default_factory=list)       # {var, line}
    env_writes: list[dict] = field(default_factory=list)      # {var, line}
    routes: list[dict] = field(default_factory=list)
    cli_verbs: list[dict] = field(default_factory=list)
    print_calls: list[int] = field(default_factory=list)
    silent_broad_excepts: list[int] = field(default_factory=list)
    enums: list[dict] = field(default_factory=list)
    models: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)


class _Visitor(ast.NodeVisitor):
    def __init__(self, facts: ModuleFacts) -> None:
        self.f = facts
        self._func_depth = 0
        self._typing_depth = 0
        self._optional_depth = 0

    # -- position tracking -------------------------------------------------------------------
    def _position(self) -> str:
        if self._func_depth > 0:
            return "lazy"          # in-function: materializes only if the function is CALLED
        if self._typing_depth > 0:
            return "typing"        # if TYPE_CHECKING: no runtime dependency at all
        if self._optional_depth > 0:
            return "optional"      # try/except ImportError: may legitimately be absent
        return "compile"           # module level: a HARD load-order constraint

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Studio routes are declared INSIDE factory functions (`register_review_routes(app, cfg)`),
        # so route decorators must be harvested here, not only at module level.
        self._decorated(node)
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_If(self, node: ast.If) -> None:
        # `if TYPE_CHECKING:` / `if t.TYPE_CHECKING:` — the body is typing-only.
        test = _dotted(node.test) or ""
        is_tc = test == "TYPE_CHECKING" or test.endswith(".TYPE_CHECKING")
        if is_tc:
            self._typing_depth += 1
            for child in node.body:
                self.visit(child)
            self._typing_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        catches_import = any(
            (_dotted(h.type) or "").endswith("ImportError")
            or (_dotted(h.type) or "").endswith("ModuleNotFoundError")
            or (isinstance(h.type, ast.Tuple)
                and any((_dotted(e) or "").endswith(("ImportError", "ModuleNotFoundError")) for e in h.type.elts))
            for h in node.handlers
        )
        if catches_import:
            self._optional_depth += 1
            for child in node.body:
                self.visit(child)
            self._optional_depth -= 1
            for h in node.handlers:
                self.visit(h)
            for child in node.orelse + node.finalbody:
                self.visit(child)
            return
        self.generic_visit(node)

    # -- imports -----------------------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        pos = self._position()
        for alias in node.names:
            self.f.imports.append({"raw": alias.name, "names": [], "position": pos,
                                   "line": node.lineno, "relative": 0})
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        pos = self._position()
        names = [a.name for a in node.names]
        if any(n == "*" for n in names):
            self.f.unsupported.append({
                "kind": "star_import", "line": node.lineno,
                "evidence": f"from {node.module or '.'} import *",
                "why": "a star-import's edge set is not statically enumerable per-name",
            })
        self.f.imports.append({"raw": node.module or "", "names": sorted(names), "position": pos,
                               "line": node.lineno, "relative": node.level or 0})
        self.generic_visit(node)

    # -- calls: side effects, env, routes, verbs ---------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        dotted = _dotted(node.func) or ""
        tail = dotted.rsplit(".", 1)[-1] if dotted else ""
        head = dotted.split(".", 1)[0] if dotted else ""

        # dynamic import — a real edge this extractor cannot resolve
        if dotted in ("importlib.import_module", "__import__"):
            self.f.unsupported.append({
                "kind": "dynamic_import", "line": node.lineno, "evidence": dotted,
                "why": "the import target is computed at runtime; no static edge is derivable",
            })

        if head == "subprocess" and tail in _SUBPROCESS_FNS:
            self.f.subprocess_sites.append({"call": dotted, "line": node.lineno})

        if head == "requests" and tail in _REQUESTS_VERBS:
            self.f.network_sites.append({"call": dotted, "line": node.lineno})

        if tail == "transaction":
            self.f.txn_sites.append({"call": dotted, "line": node.lineno})

        if tail in _LOCK_FNS and dotted not in ("threading.lock",):
            self.f.lock_sites.append({"call": dotted, "line": node.lineno})

        if dotted == "tempfile.mkdtemp":
            self.f.mkdtemp_sites.append({"call": dotted, "line": node.lineno})
        if dotted == "shutil.rmtree":
            self.f.rmtree_sites.append({"call": dotted, "line": node.lineno})

        # env reads: os.getenv("K") / os.environ.get("K")
        if (head == "os" and tail in _ENV_READ_FNS) or dotted == "os.environ.get":
            key = _literal_str(node.args[0]) if node.args else None
            if key is not None:
                self.f.env_reads.append({"var": key, "line": node.lineno})
            elif node.args:
                self.f.unsupported.append({
                    "kind": "computed_env_key", "line": node.lineno, "evidence": dotted,
                    "why": "env key is not a string literal; the variable name is not statically known",
                })

        if dotted == "print":
            self.f.print_calls.append(node.lineno)

        if tail == "add_parser":
            verb = _literal_str(node.args[0]) if node.args else None
            if verb is not None:
                self.f.cli_verbs.append({"verb": verb, "line": node.lineno})
            else:
                self.f.unsupported.append({
                    "kind": "computed_cli_verb", "line": node.lineno, "evidence": dotted,
                    "why": "argparse subcommand name is not a string literal",
                })
        self.generic_visit(node)

    # -- env writes: os.environ["K"] = v -----------------------------------------------------
    def _record_env_write(self, target: ast.AST, line: int) -> None:
        if isinstance(target, ast.Subscript) and _dotted(target.value) == "os.environ":
            key = _literal_str(target.slice)
            self.f.env_writes.append({"var": key if key is not None else "<dynamic>", "line": line})

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            self._record_env_write(t, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_env_write(node.target, node.lineno)
        self.generic_visit(node)

    # -- routes + models ---------------------------------------------------------------------
    def _decorated(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # The Studio uses Flask 2.x VERB SHORTHAND (`@app.get(...)`, `@app.post(...)`). There is
        # NOT ONE `@app.route(...)` in the tree — a census that only matched `.route` would report
        # zero routes and call the Studio surface empty. Match the shorthands too.
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            d = _dotted(dec.func) or ""
            tail = d.rsplit(".", 1)[-1]
            if tail not in _ROUTE_DECORATORS:
                continue
            path = _literal_str(dec.args[0]) if dec.args else None
            if tail == "route":
                methods = ["GET"]
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        lit = [_literal_str(e) for e in kw.value.elts]
                        methods = sorted(m for m in lit if m)
            else:
                methods = [tail.upper()]
            if path is None:
                self.f.unsupported.append({
                    "kind": "computed_route_path", "line": dec.lineno, "evidence": d,
                    "why": "route path is not a string literal; the URL is not statically known",
                })
                continue
            self.f.routes.append({"path": path, "methods": methods, "decorator": d,
                                  "handler": node.name, "line": node.lineno,
                                  "mutating": bool(set(methods) & _MUTATING_METHODS)})

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [(_dotted(b) or "") for b in node.bases]
        if any(b.endswith("Enum") for b in bases):
            members = [t.id for s in node.body if isinstance(s, ast.Assign)
                       for t in s.targets if isinstance(t, ast.Name)]
            self.f.enums.append({"name": node.name, "members": sorted(members), "line": node.lineno})
        if any(b.endswith("BaseModel") for b in bases):
            fields = [s.target.id for s in node.body
                      if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)]
            self.f.models.append({"name": node.name, "fields": sorted(fields), "line": node.lineno})
        self.generic_visit(node)


# ── swallow-ratchet measurement (mirrors tests/test_swallow_ratchet.py exactly) ─────────────
def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        return _call_name(func.func)
    return None


def _handler_non_silent(body: list[ast.stmt]) -> bool:
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not stmt:
                continue
            if isinstance(sub, ast.Raise):
                return True
            if isinstance(sub, ast.With):
                for item in sub.items:
                    ctx = item.context_expr
                    if isinstance(ctx, ast.Call) and _call_name(ctx.func) == "fail_open":
                        return True
            if isinstance(sub, ast.Call):
                f = sub.func
                n = _call_name(f)
                if n in ("fail_open", "getLogger", "get_logger", "warning"):
                    return True
                if isinstance(f, ast.Call) and _call_name(f.func) == "get_logger":
                    return True
    return False


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return False
    if isinstance(t, ast.Name):
        return t.id in ("Exception", "BaseException")
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in ("Exception", "BaseException") for e in t.elts)
    return False


# ── module naming ───────────────────────────────────────────────────────────────────────────
def module_name(pkg_root: Path, py: Path) -> str:
    """`src/fanops/post/run.py` -> `fanops.post.run`; `src/fanops/__init__.py` -> `fanops`."""
    rel = py.relative_to(pkg_root.parent)
    parts = list(rel.parts)
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve(raw: str, names: list[str], relative: int, current: str, known: set[str]) -> list[str]:
    """Resolve one import statement to zero or more internal fanops module targets."""
    if relative:
        pkg = current.rsplit(".", 1)[0] if "." in current else current
        # a package's own __init__ is its own package root
        base_parts = current.split(".") if current in known and _is_pkg(current, known) else pkg.split(".")
        up = relative - 1
        base = ".".join(base_parts[: len(base_parts) - up]) if up else ".".join(base_parts)
        raw = f"{base}.{raw}" if raw else base

    if not raw.startswith("fanops"):
        return []

    out: set[str] = set()
    if names:
        # `from P import n` ALWAYS depends on P: Python must execute P/__init__.py before it can
        # resolve n. So the edge to the PACKAGE is real, and it is in addition to the edge to the
        # submodule P.n when n is itself a module. Dropping the package edge understates the
        # load-order graph by exactly the package-import edges (20 compile + 15 lazy here).
        out.add(raw)
        for n in names:
            if n == "*":
                continue
            cand = f"{raw}.{n}"
            if cand in known:
                out.add(cand)
    else:
        out.add(raw)
    return sorted(t for t in out if t in known and t != current)


def _is_pkg(mod: str, known: set[str]) -> bool:
    prefix = mod + "."
    return any(k.startswith(prefix) for k in known)


# ── the public entry point ──────────────────────────────────────────────────────────────────
def extract(src_root: Path) -> dict:
    """Extract every DERIVED_FROM_CODE fact from `src_root` (e.g. `src/fanops`)."""
    files = sorted(src_root.rglob("*.py"))
    per_module: dict[str, ModuleFacts] = {}

    for py in files:
        name = module_name(src_root, py)
        text = py.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(py))
        facts = ModuleFacts(name=name, path=py.as_posix())
        _Visitor(facts).visit(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad_except(node) \
                    and not _handler_non_silent(node.body):
                facts.silent_broad_excepts.append(node.lineno)
        per_module[name] = facts

    known = set(per_module)

    edges: dict[str, dict[str, set[str]]] = {
        k: {"compile": set(), "lazy": set(), "typing": set(), "optional": set()} for k in known
    }
    external: dict[str, set[str]] = {"compile": set(), "lazy": set(), "typing": set(), "optional": set()}

    for name, f in per_module.items():
        for imp in f.imports:
            pos = imp["position"]
            targets = _resolve(imp["raw"], imp["names"], imp["relative"], name, known)
            if targets:
                edges[name][pos].update(targets)
            else:
                root = (imp["raw"] or "").split(".", 1)[0]
                if root and not root.startswith("fanops") and imp["relative"] == 0:
                    external[pos].add(root)

    return {
        "modules": sorted(known),
        "module_facts": per_module,
        "edges": {k: {p: sorted(v) for p, v in d.items()} for k, d in sorted(edges.items())},
        "external": {p: sorted(v) for p, v in external.items()},
    }
