#!/usr/bin/env python3
"""Machine-checkable codemap drift detector (stdlib-only).

Runs the committed AST extractors, compares counts in docs/CODEMAPS/full-trace-index.md,
and flags known-stale semantic claims. Exit 0 = no drift, 1 = drift detected, 2 = error.

Used by:
  - .github/workflows/codemap-sync-trigger.yml (cheap preflight before billing a cloud agent)
  - fanops-codemap-sync subagent (idempotent no-op gate)
  - tests/test_codemap_drift.py
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_EXTRACT = _ROOT / "scripts" / "codemap_extract"
_INDEX_MD = _ROOT / "docs" / "CODEMAPS" / "full-trace-index.md"
_LEDGER = _ROOT / "src" / "fanops" / "ledger.py"
_CACHE = _ROOT / ".codemap-cache"

# Semantic rot: substrings that must NOT appear as live claims (historical anchors).
_FORBIDDEN_CLAIMS = (
    ("crosspost.py:269", "Post birth site moved to crosspost.py:228-232 (P11)"),
    ("moment_casting_prompt", "LLM casting gate removed P11/MOL-152"),
    ("request_moment_casting", "LLM casting gate removed P11/MOL-152"),
    ("ingest_moment_casting", "LLM casting gate removed P11/MOL-152"),
    ("AccountSelection", "durable AccountSelection removed P11/MOL-152 (except historical notes)"),
)

# Files where forbidden strings are OK (historical snapshots).
_HISTORICAL = frozenset({
    "docs/CODEMAPS/lifecycle-full-picture.md",
})


def _read_schema_version() -> int | None:
    text = _LEDGER.read_text(encoding="utf-8")
    m = re.search(r"^SCHEMA_VERSION\s*=\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else None


def _parse_index_counts(text: str) -> dict:
    """Extract module/callable counts claimed in full-trace-index.md."""
    out: dict[str, int] = {}
    m = re.search(r"(\d[\d,]*)\s+callables", text)
    if m:
        out["callable_count"] = int(m.group(1).replace(",", ""))
    m = re.search(r"Files scanned:\s*(\d+)/(\d+)", text)
    if m:
        out["module_count_scanned"] = int(m.group(1))
        out["module_count_total"] = int(m.group(2))
    m = re.search(r"(\d+)/(\d+)\s+modules covered", text)
    if m:
        out["modules_covered"] = int(m.group(1))
    return out


def run_extractors(cache_dir: Path) -> dict:
    """Run ast_extract + build_graphs into cache_dir; return live summary."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_path = cache_dir / "structural_index.json"
    ast_py = _EXTRACT / "ast_extract.py"
    graphs_py = _EXTRACT / "build_graphs.py"
    proc = subprocess.run([sys.executable, str(ast_py), "src"],
                          capture_output=True, text=True, cwd=_ROOT, timeout=120, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ast_extract failed: {proc.stderr.strip() or proc.stdout.strip()}")
    index_path.write_text(proc.stdout, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(graphs_py), "--index", str(index_path),
                           "--out-dir", str(cache_dir)],
                          capture_output=True, text=True, cwd=_ROOT, timeout=120, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"build_graphs failed: {proc.stderr.strip() or proc.stdout.strip()}")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    call_graph = json.loads((cache_dir / "call_graph.json").read_text(encoding="utf-8"))
    funcs = sum(1 for v in call_graph.values() if v.get("kind") == "function")
    methods = sum(1 for v in call_graph.values() if v.get("kind") == "method")
    return {"module_count": data["module_count"], "callable_count": funcs + methods}


def find_forbidden_claims(codemaps_dir: Path) -> list[dict]:
    """Return forbidden substrings found outside historical snapshot files."""
    hits: list[dict] = []
    for md in sorted(codemaps_dir.rglob("*.md")):
        rel = str(md.relative_to(_ROOT))
        if rel in _HISTORICAL:
            continue
        text = md.read_text(encoding="utf-8")
        for needle, reason in _FORBIDDEN_CLAIMS:
            if needle not in text:
                continue
            if needle == "AccountSelection":
                for m in re.finditer(re.escape(needle), text):
                    ctx = text[max(0, m.start() - 80): m.end() + 80].lower()
                    if any(w in ctx for w in ("removed", "gone", "deleted", "legacy", "pre-p11")):
                        continue
                    hits.append({"file": rel, "needle": needle, "reason": reason})
                    break
            else:
                hits.append({"file": rel, "needle": needle, "reason": reason})
    return hits


def check_schema_drift(codemaps_dir: Path, live_version: int | None) -> list[str]:
    if live_version is None:
        return ["could not read SCHEMA_VERSION from ledger.py"]
    issues: list[str] = []
    for name in ("data.md", "subsystem-traces/C1_data_model.md"):
        path = codemaps_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        claimed = {int(m.group(1)) for m in re.finditer(r"SCHEMA_VERSION\s*[=:]?\s*(\d+)", text)}
        stale = {v for v in claimed if v != live_version}
        if stale:
            issues.append(f"{name} claims SCHEMA_VERSION {sorted(stale)}; live is {live_version}")
    return issues


def detect_drift(*, cache_dir: Path | None = None) -> dict:
    """Pure drift report. `drift` is True when any check fails."""
    cache = cache_dir or _CACHE
    codemaps = _ROOT / "docs" / "CODEMAPS"
    reasons: list[str] = []
    live = run_extractors(cache)
    index_text = _INDEX_MD.read_text(encoding="utf-8") if _INDEX_MD.is_file() else ""
    claimed = _parse_index_counts(index_text)
    doc_modules = claimed.get("module_count_total") or claimed.get("modules_covered")
    doc_callables = claimed.get("callable_count")
    if doc_modules is not None and doc_modules != live["module_count"]:
        reasons.append(f"module count: docs={doc_modules} live={live['module_count']}")
    if doc_callables is not None and doc_callables != live["callable_count"]:
        reasons.append(f"callable count: docs={doc_callables} live={live['callable_count']}")
    forbidden = find_forbidden_claims(codemaps)
    for hit in forbidden:
        reasons.append(f"stale claim {hit['needle']!r} in {hit['file']} ({hit['reason']})")
    schema_issues = check_schema_drift(codemaps, _read_schema_version())
    reasons.extend(schema_issues)
    return {"drift": bool(reasons), "reasons": reasons, "live": live, "claimed": claimed,
            "forbidden_hits": forbidden}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="codemap drift detector")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cache-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    try:
        rep = detect_drift(cache_dir=args.cache_dir)
    except Exception as exc:
        print(f"[codemap-drift] error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep, indent=2))
    elif rep["drift"]:
        print("[codemap-drift] DRIFT detected:")
        for r in rep["reasons"]:
            print(f"  - {r}")
    else:
        print(f"[codemap-drift] OK — docs match live counts "
              f"(modules={rep['live']['module_count']} callables={rep['live']['callable_count']})")
    return 1 if rep["drift"] else 0


if __name__ == "__main__":
  raise SystemExit(main())
