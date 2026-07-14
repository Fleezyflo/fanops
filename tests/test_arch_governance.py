# tests/test_arch_governance.py — Cycle 7: the architecture/implementation governance gate.
#
# This is the CI face of `python -m tools.arch`. It runs in the existing `unit` lane, so the gate
# lives exactly where this repo's discipline already lives — alongside the two AST ratchets
# (test_swallow_ratchet, test_internal_prints_routed) that prove FanOps CAN enforce a policy
# mechanically. Those two are the model; this generalizes them.
#
# NOTE ON READING THIS FILE: for every invariant a test here claims to protect, READ THE ASSERTION,
# NOT THE NAME. That instruction is not boilerplate — Cycle 4 found a GREEN test in this very repo
# that ASSERTED a data-loss outcome and called it correct (RC-5 / AR-03). The negative controls
# below exist precisely so that this file cannot become that.
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.arch import drift, policy, registries, selftest  # noqa: E402
from tools.arch.common import REPO  # noqa: E402
from tools.arch.generate import generate  # noqa: E402
from tools.arch.select import deep_required  # noqa: E402


# ── 1. generated artifacts are byte-reproducible and NOT hand-edited ────────────────────────
def test_derived_artifacts_are_not_stale():
    """The check that makes every other one trustworthy.

    If the committed derived/ artifacts differ from regeneration, then every claim downstream of
    them is a claim about a file somebody hand-edited.
    """
    stale = drift.stale_artifacts()
    assert stale == [], (
        "derived/ is STALE or HAND-EDITED. Run `python -m tools.arch regen` and commit.\n"
        + "\n".join(f"  {d.artifact}: {d.detail}\n    " + "\n    ".join(d.evidence[:6])
                    for d in stale))


def test_regeneration_is_deterministic(tmp_path):
    """Running the generator twice with no source change MUST produce byte-identical output.

    Non-deterministic regeneration would make every run a diff, reviewers would learn to ignore the
    diff, and the whole gate would be decorative. (This is also why nothing stamps a wall clock.)
    """
    a, b = tmp_path / "a", tmp_path / "b"
    generate(out=a)
    generate(out=b)
    for pa in sorted(a.glob("*.json")):
        pb = b / pa.name
        assert pa.read_text() == pb.read_text(), f"{pa.name} is not deterministic"


def test_generated_artifacts_are_a_pure_function_of_the_source_tree(tmp_path):
    """Regenerate from a COPY of src/ that lives OUTSIDE any git repository.

    The output must be byte-identical to what is committed. This is the strongest statement the
    system can make about itself: the artifacts depend on the SOURCE TREE and on nothing else —
    not the git commit, not the absolute path, not the machine, not the user, not the clock.

    It exists because the generator FAILED it. Every artifact carried
    `repository_commit: <git rev-parse --short HEAD>`, defended in a comment as "provenance is the
    COMMIT, which is deterministic". It is not deterministic in the only sense that matters here,
    and it is SELF-INVALIDATING: committing the artifact moves HEAD, so CI regenerates a different
    SHA, the byte-compare goes RED, and regenerating to fix it moves HEAD again. The gate could
    never have been green on any commit — including the one that introduced it. Nothing else in the
    suite caught it, because every other check ran at the same HEAD the artifacts were built at.

    A copy under tmp_path has no `.git` above it, so anything reaching for git state resolves
    differently here than in the repo, and the byte-compare fails. Which is the point.
    """
    src_copy = tmp_path / "tree" / "src" / "fanops"
    src_copy.parent.mkdir(parents=True)
    shutil.copytree(REPO / "src" / "fanops", src_copy,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert not (tmp_path / ".git").exists()

    out = tmp_path / "derived"
    generate(src=src_copy, out=out)

    committed = REPO / ".reports" / "architecture" / "derived"
    for produced in sorted(out.glob("*.json")):
        want = (committed / produced.name).read_text(encoding="utf-8")
        got = produced.read_text(encoding="utf-8")
        assert got == want, (
            f"{produced.name} differs when generated from a copy of src/ outside a git repo — the "
            f"artifact depends on something that is NOT the source tree (git state, an absolute "
            f"path, the username, the clock). Generated artifacts must be a pure function of the "
            f"source.")


# ── 2. the policy set holds ─────────────────────────────────────────────────────────────────
def test_no_blocking_policy_findings():
    findings = policy.check()
    blocking = policy.blocking(findings)
    assert blocking == [], (
        "BLOCKING architecture/implementation policy finding(s):\n"
        + "\n".join(f"  [{f.rule}] {f.title}\n    {f.detail}\n    "
                    + "\n    ".join(f.evidence[:6]) for f in blocking))


def test_every_rule_is_reachable():
    """A rule with no check behind it is DECORATION that manufactures confidence.

    Every rule in RULES must be exercised by at least one negative control. This is the structural
    guard against the exact failure NC-15 caught: `IMPL-007` was in the policy set, was reported in
    the docs, and SILENTLY DID NOT FIRE (its parser read the number out of a prose sentence and got
    nothing back). It looked enforced. It was not.
    """
    # ARCH-006 is enforced by byte-comparison (drift), not by a policy Finding — NC-08 covers the
    # derived/ artifacts and NC-23 covers the generated doc. Both name it, so no exemption is
    # needed here: delete either control and this test correctly goes red.
    covered = {c.expect_rule for c in selftest.CONTROLS}
    uncovered = sorted(set(policy.RULES) - covered)
    assert uncovered == [], (
        f"rule(s) with no negative control: {uncovered}. A rule nobody has tried to fool is a rule "
        f"nobody should trust — add a control to tools/arch/selftest.py::CONTROLS.")


# ── 3. the registries are governed ──────────────────────────────────────────────────────────
def test_registries_are_valid():
    errs = registries.validate()
    assert errs == [], "invalid exception/unknown registry entries:\n  " + "\n  ".join(errs)


def test_unknowns_do_not_grow_without_approval():
    open_, ceiling = registries.unknown_growth()
    assert open_ <= ceiling, (
        f"UNKNOWNs grew to {open_}, above the approved ceiling of {ceiling}. Raising the ceiling is "
        f"a deliberate act: it is a statement that the system is LESS understood than it was, and "
        f"that should be hard to do quietly. Edit governance/unknowns.json with a rationale.")


# ── 4. path selection is explicit AND tested (and fails OPEN) ───────────────────────────────
@pytest.mark.parametrize("changed,expected", [
    (None, True),                                            # unknown -> FAIL OPEN
    ([], False),
    (["src/fanops/clip.py"], False),                          # source only: fast gate suffices
    (["README.md"], False),
    (["tools/arch/policy.py"], True),                         # the validators changed
    (["tests/test_swallow_ratchet.py"], True),                # a ratchet baseline changed
    ([".reports/architecture/kb/subsystems.json"], True),     # canonical DECLARED changed
    ([".reports/architecture/governance/baselines.json"], True),
    (["src/fanops/clip.py", "tools/arch/graph.py"], True),    # any hit wins
])
def test_deep_gate_selection(changed, expected):
    got, why = deep_required(changed)
    assert got is expected, f"deep_required({changed!r}) -> {got} ({why})"


def test_selection_fails_open_not_closed():
    """A selection rule that fails CLOSED silently skips the check that proves the system works."""
    assert deep_required(None)[0] is True


# ── 5. the negative controls — the proof the validators are not decorative ──────────────────
@pytest.mark.slow
@pytest.mark.parametrize("control", selftest.CONTROLS, ids=lambda c: c.id)
def test_negative_control_is_detected(control):
    """Inject exactly one defect; assert the named rule fires with evidence that was ABSENT before.

    Demanding NEW evidence is what makes this rigorous. The live tree carries real findings, so a
    control that merely asserted "the rule fires" would pass on pre-existing noise and prove
    nothing. This proves the validator DISCRIMINATES.
    """
    with selftest.fixture() as (root, paths):
        before = selftest._sig(selftest._run(paths))
        selftest._inject(control.id, root, paths)

        if control.id == "NC-08":
            stale = drift.stale_artifacts(paths["DERIVED"])
            assert any(d.artifact == "modules.json" for d in stale), \
                "a HAND-EDITED generated artifact went undetected"
            return

        after = selftest._run(paths)
        new = [e for f in after for e in f.evidence
               if f.rule == control.expect_rule and (f.rule, e) not in before]
        assert new, (
            f"{control.expect_rule} did NOT fire on an injected `{control.defect}`. "
            f"The rule is DECORATIVE: it is claimed in the policy set but does not detect the "
            f"defect it names. That is worse than having no rule, because it manufactures "
            f"confidence.")
