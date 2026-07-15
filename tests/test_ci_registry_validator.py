# tests/test_ci_registry_validator.py — the CI face of `python -m tools.ci selftest`.
#
# READ THE ASSERTION, NOT THE NAME. Every invariant this file claims to protect is in the assertion.
# It DELEGATES to tools.ci (selftest.detect + checks.run_static — the same implementations the CLI
# verbs run) so the pytest gate and the CLI can never report different results on the same commit —
# the drift tools/arch was bitten by. It proves the DCs DISCRIMINATE (fire on an injected defect)
# AND — now the Phase-D remediation has landed — that the committed tree is static-clean: DC-1/2/4/5/6
# find no blocking registry<->workflow divergence. (DC-3, deployed-state vs live GitHub, is scheduled
# and out of this offline gate.)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.ci import checks, selftest  # noqa: E402
from tools.ci.common import PROSE_DOCS  # noqa: E402
from tools.ci.registry import load_registry, shape_findings  # noqa: E402
from tools.ci.workflows import discover_jobs  # noqa: E402


def test_registry_is_shape_valid():
    """The committed registry conforms to its schema shape (fields present, enums valid)."""
    findings = shape_findings(load_registry())
    assert findings == [], "registry shape errors:\n  " + "\n  ".join(f.render() for f in findings)


def test_static_planes_have_no_blocking_divergence():
    """The tree-clean gate. With the Phase-D remediation landed, the static planes are reconciled:
    the committed registry conforms to shape AND the registry<->workflow static checks
    (DC-1/2/4/5/6) produce no BLOCKING finding. Runs the SAME code the CLI `static` verb runs
    (shape_findings + checks.run_static), so the pytest gate and the CLI can never disagree. A future
    PR that reintroduces a static divergence — a renamed required context (DC-1), an untracked job or
    phantom control (DC-2), prose calling a required context advisory (DC-4), a duplicate_group naming
    an unknown control (DC-5), or a job that drops its timeout / SHA-pin (DC-6) — reddens the required
    `unit` lane here. No network (DC-3 is deployed-state, scheduled, out of this gate)."""
    reg = load_registry()
    findings = shape_findings(reg) + checks.run_static(reg, discover_jobs(), PROSE_DOCS)
    blocking = [f for f in findings if f.blocking and not f.skipped]
    assert blocking == [], ("static registry<->workflow divergence (DC-1/2/4/5/6):\n  "
                            + "\n  ".join(f.render() for f in blocking))


def test_every_blocking_condition_has_a_negative_control():
    """Each DC that can block must be exercised by at least one negative control — a check nobody
    has tried to fool is a check nobody should trust (the tools/arch NC-15 lesson)."""
    covered = {c.expect_dc for c in selftest.CONTROLS}
    assert {"DC-1", "DC-2", "DC-3", "DC-4", "DC-5", "DC-6"} <= covered, f"uncovered DCs: {sorted({'DC-1','DC-2','DC-3','DC-4','DC-5','DC-6'} - covered)}"


@pytest.mark.parametrize("control", selftest.CONTROLS, ids=lambda c: c.id)
def test_negative_control_fires(control):
    """Inject one defect; the named DC must fire with evidence absent before."""
    fired, detail = selftest.detect(control)
    assert fired, (f"{control.expect_dc} did NOT fire on an injected `{control.defect}` ({detail}). "
                   f"The check is DECORATIVE — it manufactures confidence.")
