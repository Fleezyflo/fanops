"""tools.ci CLI — three validation modes with deterministic, actionable diagnostics.

  static     registry <-> workflow implementation (DC-1/2/4/6/7). No network. Local + PR.
  deployed   registry <-> live GitHub settings (DC-3 branch protection, DC-8 workflow enablement,
             DC-9 repository security settings). Explicit read-only probes; nothing here mutates.
             --require-live => a failure of a probe this job CAN authenticate FAILS (DC-8).
             An unreadable DC-3 or DC-9 stays a non-authoritative SKIP. A protection payload that
             was read and whose contexts differ from required_contexts, or whose strict is not
             true, fails. Otherwise a probe failure is an explicit NON-AUTHORITATIVE SKIP — never
             a false pass.
  reconcile  all three planes together (static + deployed).
  selftest   run the negative controls (each blocking condition fires on an injected defect).
  regen      rewrite the GENERATED schema from tools/ci/schema.py. The unit lane byte-compares it,
             so a hand-edited schema is a failure, not a silent divergence.

Exit 0 = clean (or explicitly skipped, non-authoritative); 1 = blocking divergence; 2 = usage.
"""
from __future__ import annotations

import sys

from . import checks, schema, selftest
from .common import PROSE_DOCS, SCHEMA
from .live import probe_protection, probe_security, probe_workflows
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
    states, wf_err = probe_workflows()
    settings, sec_err = probe_security()
    findings = checks.run_deployed(reg, data, live_error=err, workflow_states=states,
                                   workflow_error=wf_err, security_settings=settings,
                                   security_error=sec_err)
    rc = _emit("deployed-state (registry <-> live GitHub)", findings)
    # --require-live escalates an unreadable probe to a FAILURE only where the read failing is
    # itself the defect. DC-8 needs `actions: read`, which the job's token is granted — an
    # unreadable workflow list means that grant broke, and fails.
    #
    # DC-3 (branch protection) and DC-9 (`security_and_analysis`) are not escalated. A workflow
    # GITHUB_TOKEN is never granted read on either, so their read failing measures the token, not
    # the setting: it is a SKIP — non-authoritative, never a pass. What DC-3 judges is a payload it
    # actually read — contexts that differ from `required_contexts`, or `strict` not true, fail in
    # the findings emitted above.
    hard = [("DC-8", wf_err)]
    failed = [(dc, e) for dc, e in hard if e]
    if failed and require_live:
        for dc, e in failed:
            print(f"  [FAIL] {dc} · - : --require-live set but the live probe failed ({e})")
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


def cmd_regen() -> int:
    changed = schema.write()
    print(f"== regen ==\n  {'rewritten' if changed else 'unchanged'}  {SCHEMA}")
    return 0


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
    if verb == "regen":
        return cmd_regen()
    print(f"unknown verb {verb!r}; use: static | deployed [--require-live] | reconcile | selftest | regen")
    return 2
