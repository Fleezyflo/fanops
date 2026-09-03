# tests/test_machine_health_channel_ratchet.py — MOL-965 WP4: channel registry + soft-lie ban.
"""CI AST/registry so multi-channel soft-green cannot return.

Gates (product brief Enforcement):
1. Closed-world CALLER gate — every FunctionDef that Calls build_health_report / doctor_report
   must be named in `_ALLOWED_HEALTH_CONSTRUCTOR_CALLERS` (new cmd_foo in cli.py fails until listed).
   File allowlist alone is not enough — it is a secondary net, not the root control.
2. Soft-lie check shape — ok+warn dict keys without severity cannot grow (baseline shrink-only).
3. Single constructor — Studio must not import doctor_report; doctor_report stays a thin wrapper;
   build_health_report / doctor_report FunctionDefs stay in their owner modules.

/healthz and fanops up are NOT machine-health owners — listed only so the contract stays honest.
"""
from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "fanops"

_CONSTRUCTOR_CALL_NAMES = frozenset({"build_health_report", "doctor_report"})

# ---------------------------------------------------------------------------
# 1) Closed-world CALLER registry — root enforcement.
#    Any FunctionDef/AsyncFunctionDef under src/fanops whose body Calls
#    build_health_report / doctor_report must appear here. New cmd_foo → CI fail.
#    Do NOT "fix" by only widening _ALLOWED_HEALTH_CONSTRUCTOR_FILES.
# ---------------------------------------------------------------------------
_ALLOWED_HEALTH_CONSTRUCTOR_CALLERS = frozenset({
    ("src/fanops/cli.py", "cmd_doctor"),                     # PRIMARY
    ("src/fanops/cli.py", "cmd_health"),                     # alias — same report + exit
    ("src/fanops/doctor.py", "doctor_report"),               # thin as_dict wrapper
    ("src/fanops/health_model.py", "render_prometheus_metrics"),
    ("src/fanops/init_flow.py", "run_init"),
    ("src/fanops/autopilot.py", "autopilot"),                # via doctor_report
    ("src/fanops/studio/views_golive.py", "golive_status"),
})

# Secondary net: files that may contain the callers above (shrink when files vanish).
# Never widen this as a Band-Aid for an unlisted FunctionDef — list the caller instead.
_ALLOWED_HEALTH_CONSTRUCTOR_FILES = frozenset({
    "src/fanops/health_model.py",   # render_prometheus_metrics
    "src/fanops/doctor.py",         # doctor_report → build_health_report only
    "src/fanops/cli.py",            # cmd_doctor / cmd_health
    "src/fanops/init_flow.py",      # run_init
    "src/fanops/autopilot.py",      # autopilot via doctor_report
    "src/fanops/studio/views_golive.py",   # golive projectors
})

# Operator-facing surfaces (symbol inventory — may include non-callers / indirect exits).
_ALLOWED_OPERATOR_HEALTH_SURFACES = frozenset({
    ("src/fanops/cli.py", "cmd_doctor"),          # PRIMARY
    ("src/fanops/cli.py", "cmd_health"),          # alias / deps focus — same exit
    ("src/fanops/cli.py", "cmd_autopilot"),       # exit tracks report_is_healthy
    ("src/fanops/cli.py", "cmd_init"),            # readiness via init_flow
    ("src/fanops/init_flow.py", "run_init"),
    ("src/fanops/autopilot.py", "autopilot"),
    ("src/fanops/studio/views.py", "build_system_strip"),
    ("src/fanops/studio/views_golive.py", "golive_status"),
    ("src/fanops/studio/views.py", "daemon_health"),
    ("src/fanops/studio/views.py", "daemon_health_strip"),
    ("src/fanops/studio/app.py", "metrics"),      # Prometheus projection
    ("src/fanops/health_model.py", "render_prometheus_metrics"),
})

# Explicit non-owners (must NOT call build_health_report / doctor_report).
_NON_MACHINE_HEALTH_SURFACES = frozenset({
    ("src/fanops/studio/app.py", "healthz"),      # process liveness only
    ("src/fanops/cli.py", "cmd_up"),              # bring-up plane
    ("src/fanops/cli.py", "cmd_status"),          # backlog counters — not a third healthy
})

# Soft-lie ok+warn dict baseline — shrink-only. Empty after WP1 (_check emits severity).
_SOFT_LIE_OK_WARN_BASELINE: dict[str, int] = {}


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        return _call_name(func.func)
    return None


def _dict_str_keys(node: ast.Dict) -> set[str]:
    keys: set[str] = set()
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.add(k.value)
    return keys


def _fn_body_calls_constructor(fn: ast.AST) -> bool:
    """True if fn's own body Calls a constructor (nested FunctionDef/ClassDef scanned separately)."""
    stack = list(getattr(fn, "body", []))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Call) and _call_name(n.func) in _CONSTRUCTOR_CALL_NAMES:
            return True
        stack.extend(ast.iter_child_nodes(n))
    return False


def _scan_constructor_callers() -> set[tuple[str, str]]:
    """(relpath, FunctionDef.name) under src/fanops whose body Calls build_health_report/doctor_report."""
    out: set[tuple[str, str]] = set()
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _fn_body_calls_constructor(node):
                out.add((rel, node.name))
    return out


def _scan_constructor_call_files() -> set[str]:
    """Files under src/fanops that Call build_health_report or doctor_report."""
    return {rel for rel, _ in _scan_constructor_callers()}


def _soft_lie_ok_warn_counts() -> dict[str, int]:
    """Count dict literals with both 'ok' and 'warn' keys (legacy soft-lie check shape)."""
    out: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        n = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = _dict_str_keys(node)
                if "ok" in keys and "warn" in keys and "severity" not in keys:
                    n += 1
        if n:
            out[rel] = n
    return out


def _check_dicts_missing_severity() -> list[tuple[str, int]]:
    """Doctor/health_model check-shaped dicts (label+ok) must carry severity."""
    offenders: list[tuple[str, int]] = []
    for rel in ("src/fanops/doctor.py", "src/fanops/health_model.py"):
        path = _ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = _dict_str_keys(node)
            if "label" in keys and "ok" in keys and "severity" not in keys:
                offenders.append((rel, node.lineno))
    return offenders


def _studio_doctor_report_imports() -> list[tuple[str, int]]:
    """Studio must obtain machine health via build_health_report / projectors — not doctor_report."""
    offenders: list[tuple[str, int]] = []
    studio = _SRC / "studio"
    for path in sorted(studio.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "doctor" in (node.module or ""):
                for alias in node.names:
                    if alias.name == "doctor_report" or alias.name == "*":
                        offenders.append((rel, node.lineno))
            if isinstance(node, ast.Call) and _call_name(node.func) == "doctor_report":
                offenders.append((rel, node.lineno))
    return offenders


def _function_def_owners(name: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                found.append((rel, node.lineno))
    return found


def _doctor_report_is_thin_wrapper() -> bool:
    """doctor_report must only call build_health_report (no parallel assembly)."""
    path = _ROOT / "src" / "fanops" / "doctor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "doctor_report":
            calls = {_call_name(sub.func) for sub in ast.walk(node) if isinstance(sub, ast.Call)}
            return "build_health_report" in calls and "_assemble_doctor_checks" not in calls
    return False


def _named_fn_calls_constructor(rel: str, name: str) -> bool:
    """Whether a named FunctionDef in rel Calls build_health_report / doctor_report."""
    path = _ROOT / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if _fn_body_calls_constructor(node):
                return True
    return False


def _healthz_calls_machine_health() -> bool:
    """/healthz must stay process-only — no build_health_report / doctor_report."""
    return _named_fn_calls_constructor("src/fanops/studio/app.py", "healthz")


def _surface_symbols_exist() -> list[str]:
    """Registry rows must name real functions (prevents dead-inventory folklore)."""
    missing: list[str] = []
    by_file: dict[str, set[str]] = {}
    for rel, sym in (
        _ALLOWED_OPERATOR_HEALTH_SURFACES
        | _NON_MACHINE_HEALTH_SURFACES
        | _ALLOWED_HEALTH_CONSTRUCTOR_CALLERS
    ):
        by_file.setdefault(rel, set()).add(sym)
    for rel, syms in by_file.items():
        path = _ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for sym in sorted(syms):
            if sym not in defined:
                missing.append(f"{rel}:{sym}")
    return missing


def test_health_constructor_callers_are_closed_world():
    """Root gate: new FunctionDef calling build_health_report/doctor_report fails until listed."""
    actual = _scan_constructor_callers()
    new_callers = sorted(actual - _ALLOWED_HEALTH_CONSTRUCTOR_CALLERS)
    assert new_callers == [], (
        "new machine-health constructor caller(s) outside closed-world registry — "
        "add to _ALLOWED_HEALTH_CONSTRUCTOR_CALLERS + docs/MACHINE_HEALTH.md only if intentional: "
        + ", ".join(f"{p}:{s}" for p, s in new_callers)
    )
    vanished = sorted(_ALLOWED_HEALTH_CONSTRUCTOR_CALLERS - actual)
    assert vanished == [], (
        "allowlisted constructor caller(s) no longer Call build_health_report/doctor_report — "
        "shrink _ALLOWED_HEALTH_CONSTRUCTOR_CALLERS: "
        + ", ".join(f"{p}:{s}" for p, s in vanished)
    )


def test_health_constructor_call_sites_stay_on_allowlist():
    """Secondary file net — do not widen as Band-Aid for an unlisted FunctionDef."""
    actual = _scan_constructor_call_files()
    new_files = sorted(actual - _ALLOWED_HEALTH_CONSTRUCTOR_FILES)
    assert new_files == [], (
        "new machine-health constructor call site(s) outside file registry — "
        "prefer listing the FunctionDef in _ALLOWED_HEALTH_CONSTRUCTOR_CALLERS; "
        "only then add the file if needed: " + ", ".join(new_files)
    )
    vanished = sorted(_ALLOWED_HEALTH_CONSTRUCTOR_FILES - actual)
    assert vanished == [], (
        "allowlisted health constructor file(s) no longer call build_health_report/doctor_report — "
        "shrink the registry: " + ", ".join(vanished)
    )


def test_non_machine_health_surfaces_do_not_call_constructor():
    """cmd_status / cmd_up / healthz must not Call build_health_report / doctor_report."""
    offenders = [
        f"{rel}:{sym}"
        for rel, sym in sorted(_NON_MACHINE_HEALTH_SURFACES)
        if _named_fn_calls_constructor(rel, sym)
    ]
    assert offenders == [], (
        "non-machine-health surface(s) must not call the constructor: " + ", ".join(offenders)
    )


def test_soft_lie_ok_warn_dict_count_does_not_grow():
    actual = _soft_lie_ok_warn_counts()
    baseline = _SOFT_LIE_OK_WARN_BASELINE
    new_files = sorted(set(actual) - set(baseline))
    assert new_files == [], f"new ok+warn soft-lie check dict file(s): {new_files}"
    regressions = {f: (actual[f], baseline[f]) for f in baseline if actual.get(f, 0) > baseline[f]}
    assert regressions == {}, f"ok+warn soft-lie count grew: {regressions}"


def test_doctor_health_model_check_dicts_require_severity():
    offenders = _check_dicts_missing_severity()
    assert offenders == [], (
        "check-shaped dict (label+ok) without severity: "
        + ", ".join(f"{p}:{n}" for p, n in offenders)
    )


def test_studio_obtains_health_via_build_health_report_not_doctor_report():
    offenders = _studio_doctor_report_imports()
    assert offenders == [], (
        "Studio must use build_health_report / health_model projectors, not doctor_report: "
        + ", ".join(f"{p}:{n}" for p, n in offenders)
    )


def test_build_health_report_and_doctor_report_have_single_owners():
    builds = _function_def_owners("build_health_report")
    assert len(builds) == 1 and builds[0][0] == "src/fanops/health_model.py", (
        f"build_health_report forks forbidden: {builds}"
    )
    docs = _function_def_owners("doctor_report")
    assert len(docs) == 1 and docs[0][0] == "src/fanops/doctor.py", (
        f"doctor_report forks forbidden: {docs}"
    )
    assert _doctor_report_is_thin_wrapper(), (
        "doctor_report must thin-wrap build_health_report (no _assemble_doctor_checks fork)"
    )


def test_healthz_is_process_only():
    assert not _healthz_calls_machine_health(), (
        "/healthz must remain process-only — do not call build_health_report there"
    )


def test_operator_health_surface_registry_symbols_exist():
    missing = _surface_symbols_exist()
    assert missing == [], f"channel registry names missing symbols: {missing}"


def test_closed_world_caller_gate_detects_unlisted_cmd_foo():
    """Negative control: unlisted FunctionDef calling build_health_report must trip the finder."""
    tree = ast.parse(
        "def cmd_foo(cfg):\n"
        "    return build_health_report(cfg)\n"
        "def cmd_status(cfg):\n"
        "    return 0\n"
    )
    found = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _fn_body_calls_constructor(n)
    }
    assert found == {"cmd_foo"}
    assert "cmd_status" not in found


def test_soft_lie_shape_is_rejected_by_detector():
    """Regression shape: ok+warn without severity must trip the soft-lie scan."""
    tree = ast.parse('c = {"label": "x", "ok": True, "warn": True, "hint": "soft"}')
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = _dict_str_keys(node)
            if "ok" in keys and "warn" in keys and "severity" not in keys:
                n += 1
    assert n == 1
    # severity present → not a soft-lie under this gate
    tree2 = ast.parse('c = {"label": "x", "ok": True, "warn": True, "severity": "warn"}')
    n2 = sum(
        1 for node in ast.walk(tree2)
        if isinstance(node, ast.Dict)
        and "ok" in _dict_str_keys(node)
        and "warn" in _dict_str_keys(node)
        and "severity" not in _dict_str_keys(node)
    )
    assert n2 == 0
