"""tools.ci CLI — three validation modes with deterministic, actionable diagnostics.

  static     registry <-> workflow implementation (DC-1/2/4/5/6). No network. Local + PR.
  deployed   registry <-> live GitHub protection (DC-3). Explicit read-only probe.
             --require-live => a probe failure FAILS (the designated authenticated job).
             Otherwise a probe failure is an explicit NON-AUTHORITATIVE SKIP — never a false pass.
  reconcile  all three planes together (static + deployed).
  selftest   run the negative controls (each blocking condition fires on an injected defect).

Exit 0 = clean (or explicitly skipped, non-authoritative); 1 = blocking divergence; 2 = usage.
"""
from __future__ import annotations

import sys

from . import checks, selftest
from .common import PROSE_DOCS
from .live import probe_protection, required_contexts
from .registry import load_registry, shape_findings
from .workflows import discover_jobs


def _emit(title, findings) -> int:
    print(f"== {title} ==")
    if not findings:
        print("  (no findings)")
    for f in findings:
        print("  " + f.render())
    blocking = [f for f in findings if f.blocking and not f.skipped]
    return 1 if blocking else 0


def cmd_static() -> int:
    reg = load_registry()
    jobs = discover_jobs()
    findings = shape_findings(reg) + checks.run_static(reg, jobs, PROSE_DOCS)
    return _emit("static (registry <-> workflows)", findings)


def cmd_deployed(require_live: bool) -> int:
    reg = load_registry()
    data, err = probe_protection()
    live = required_contexts(data) if data else []
    findings = checks.run_deployed(reg, live, live_error=err)
    rc = _emit("deployed-state (registry <-> live GitHub)", findings)
    if err and require_live:
        print(f"  [FAIL] DC-3 · - : --require-live set but the live probe failed ({err})")
        return 1
    return rc


def cmd_reconcile(require_live: bool) -> int:
    a = cmd_static()
    b = cmd_deployed(require_live)
    return 1 if (a or b) else 0


def cmd_selftest() -> int:
    ok = True
    print("== selftest (negative controls) ==")
    for ctrl in selftest.CONTROLS:
        fired, detail = selftest.detect(ctrl)
        print(f"  [{'ok' if fired else 'FAIL'}] {ctrl.id} ({ctrl.expect_dc}): {detail}")
        ok = ok and fired
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    require_live = "--require-live" in argv
    argv = [a for a in argv if a != "--require-live"]
    verb = argv[0] if argv else "static"
    if verb == "static":
        return cmd_static()
    if verb == "deployed":
        return cmd_deployed(require_live)
    if verb in ("reconcile", "full"):
        return cmd_reconcile(require_live)
    if verb == "selftest":
        return cmd_selftest()
    print(f"unknown verb {verb!r}; use: static | deployed [--require-live] | reconcile | selftest")
    return 2
