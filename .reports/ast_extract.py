#!/usr/bin/env python3
"""AST-parsed structural index for src/fanops — stdlib only, deterministic."""
from __future__ import annotations
import ast, json, sys
from pathlib import Path

_SKIP = {"__pycache__", ".venv"}


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts) if parts else ""


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _imports(tree: ast.Module) -> list[str]:
    out: list[str] = []
    for n in tree.body:
        if isinstance(n, ast.Import):
            for a in n.names:
                out.append(a.name)
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if n.level:
                mod = "." * n.level + mod
            out.append(mod)
    return sorted(set(out))


def _calls_in(node: ast.AST) -> list[str]:
    out: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _dotted_name(child.func)
            if name:
                out.append(name.split(".")[-1])
    return out


def _extract(path: Path, fanops: Path, root: Path) -> dict:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    mod = _module_name(path, fanops)
    functions: list[dict] = []
    classes: list[dict] = []
    module_calls: list[str] = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({"name": n.name, "line": n.lineno, "calls": sorted(set(_calls_in(n)))})
        elif isinstance(n, ast.ClassDef):
            methods = []
            for item in n.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({"name": item.name, "line": item.lineno, "calls": sorted(set(_calls_in(item)))})
            classes.append({"name": n.name, "line": n.lineno, "methods": methods})
        else:
            module_calls.extend(_calls_in(n))
    rel = path.relative_to(fanops)
    display = f"fanops/{rel}" if root.name == "src" else str(path.relative_to(root))
    return {
        "path": display.replace("\\", "/"),
        "module": mod,
        "line_count": src.count("\n") + (1 if src and not src.endswith("\n") else 0),
        "imports_from": _imports(tree),
        "functions": functions,
        "classes": classes,
        "module_level_calls": sorted(set(module_calls)),
    }


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "src").resolve()
    fanops = root / "fanops" if (root / "fanops").is_dir() else root
    modules: list[dict] = []
    for path in sorted(fanops.rglob("*.py")):
        if any(p in _SKIP for p in path.parts):
            continue
        try:
            modules.append(_extract(path, fanops, root))
        except SyntaxError as e:
            modules.append({"path": str(path), "module": "", "error": str(e)})
    payload = {
        "root": str(fanops),
        "module_count": len(modules),
        "function_count": sum(len(m.get("functions", [])) for m in modules),
        "method_count": sum(len(c.get("methods", [])) for m in modules for c in m.get("classes", [])),
        "class_count": sum(len(m.get("classes", [])) for m in modules),
        "modules": modules,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
