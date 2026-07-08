#!/usr/bin/env python3
"""AST-based structural index for FanOps (stdlib-only, deterministic).

Usage:
  python3 scripts/codemap_extract/ast_extract.py src > /tmp/structural_index.json
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def _module_name(pkg_root: Path, py: Path) -> str:
    rel = py.relative_to(pkg_root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts) if parts else pkg_root.name


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


class _Extractor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[dict] = []
        self.functions: list[dict] = []
        self.classes: list[dict] = []
        self.module_calls: list[str] = []
        self._class_stack: list[str] = []
        self._func_stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append({"kind": "import", "module": alias.name, "line": node.lineno})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if node.level:
            mod = ("." * node.level) + mod
        for alias in node.names:
            self.imports.append({"kind": "from", "module": mod, "name": alias.name, "line": node.lineno})

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        calls = self._collect_calls(node)
        if self._class_stack:
            return
        self.functions.append({"name": node.name, "line": node.lineno, "calls": sorted(calls)})
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        methods: list[dict] = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append({"name": item.name, "line": item.lineno,
                                "calls": sorted(self._collect_calls(item))})
        self.classes.append({"name": node.name, "line": node.lineno, "methods": methods})
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if not self._func_stack and not self._class_stack:
            name = _call_name(node.func)
            if name:
                self.module_calls.append(name)
        self.generic_visit(node)

    def _collect_calls(self, node: ast.AST) -> set[str]:
        out: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = _call_name(child.func)
                if name:
                    out.add(name)
        return out


def extract_module(src_root: Path, py: Path) -> dict:
    text = py.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(py))
    ex = _Extractor()
    ex.visit(tree)
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return {
        "path": str(py.relative_to(src_root.parent)),
        "lines": lines,
        "imports": ex.imports,
        "functions": ex.functions,
        "classes": ex.classes,
        "module_calls": sorted(set(ex.module_calls)),
    }


def main() -> None:
    src_root = Path(sys.argv[1] if len(sys.argv) > 1 else "src")
    if not src_root.exists():
        print(f"root not found: {src_root}", file=sys.stderr)
        sys.exit(1)
    pkg_root = src_root / "fanops" if (src_root / "fanops").is_dir() else src_root
    modules: dict[str, dict] = {}
    errors: list[str] = []
    for py in sorted(pkg_root.rglob("*.py")):
        name = _module_name(pkg_root, py)
        mod_prefix = pkg_root.name
        qname = f"{mod_prefix}.{name}" if name else mod_prefix
        try:
            modules[qname] = extract_module(src_root, py)
        except SyntaxError as e:
            errors.append(f"{py}: {e}")
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    payload = {"root": str(src_root), "module_count": len(modules), "modules": modules}
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
