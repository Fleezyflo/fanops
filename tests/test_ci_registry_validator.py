# tests/test_ci_registry_validator.py — the CI face of `python -m tools.ci selftest`.
#
# READ THE ASSERTION, NOT THE NAME. Every invariant this file claims to protect is in the assertion.
# It DELEGATES to tools.ci.selftest.detect (the same implementation the CLI verb runs) so the pytest
# gate and the CLI can never report different results on the same commit — the drift tools/arch was
# bitten by. It proves the DCs DISCRIMINATE (fire on an injected defect); it deliberately does NOT
# assert the current tree is clean (known remediation is still pending in later slices).
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.ci import selftest  # noqa: E402
from tools.ci.registry import load_registry, shape_findings  # noqa: E402


def test_registry_is_shape_valid():
    """The committed registry conforms to its schema shape (fields present, enums valid)."""
    findings = shape_findings(load_registry())
    assert findings == [], "registry shape errors:\n  " + "\n  ".join(f.render() for f in findings)


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
