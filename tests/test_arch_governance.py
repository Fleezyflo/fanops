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

import re
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.arch import drift, impact, policy, registries, selftest  # noqa: E402
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
    # derived/ artifacts (the only generated surface since the governance doc's deletion, 2026-07).
    # It names the rule, so no exemption is needed here: delete it and this test correctly goes red.
    covered = {c.expect_rule for c in selftest.CONTROLS}
    uncovered = sorted(set(policy.RULES) - covered)
    assert uncovered == [], (
        f"rule(s) with no negative control: {uncovered}. A rule nobody has tried to fool is a rule "
        f"nobody should trust — add a control to tools/arch/selftest.py::CONTROLS.")


# ── 3. the registries are governed ──────────────────────────────────────────────────────────
_SIX = ("authoritative_source", "regeneration_command", "validation_mechanism",
        "owner", "permitted_manual_edits", "conflict_resolution")
_CLASSES = {"DERIVED_FROM_CODE", "DERIVED_FROM_CONTRACT", "DECLARED_AND_ENFORCED",
            "GENERATED_FROM_SCHEMA", "HUMAN_DECISION", "UNKNOWN"}


def test_field_authority_declares_all_six_attributes():
    """Every governed artifact must say WHERE its truth lives, and by what right.

    The Cycle 7 closure audit found `authoritative_source` absent from all ten entries, and no
    regeneration path on the four DECLARED artifacts. Both were *inferable* — from the field class,
    from `source_inputs` inside the derived artifacts — and "inferable" is precisely what this
    system exists to stop accepting. A fact a machine cannot read is a fact that rots.

    This also pins the classification vocabulary: an artifact whose fields use a class outside the
    six is a field nobody has actually decided the authority of.
    """
    import json
    arts = json.loads((REPO / ".reports/architecture/governance/field_authority.json")
                      .read_text(encoding="utf-8"))["artifacts"]
    assert arts, "the field-authority map governs NOTHING — it has no artifact entries"

    for e in arts:
        missing = [k for k in _SIX if not str(e.get(k, "")).strip()]
        assert not missing, (
            f"{e.get('artifact', '<unnamed>')} does not declare {missing}. Every governed artifact "
            f"must state where its truth lives, how to regenerate it (or that it cannot be), how it "
            f"is validated, who owns it, what may be hand-edited, and who wins on conflict.")

        for field, cls in e["fields"].items():
            # The class name may be wrapped in emphasis and followed by prose
            # (`*** DECLARED_AND_ENFORCED — why ***`), so match the NAME anywhere in the value
            # rather than assuming it is the first token. Splitting on whitespace and taking [0]
            # yields "***" for that shape — a parser that reports "unclassified" for a field that
            # is, in fact, classified. Same failure mode as IMPL-007's backtick.
            assert any(re.search(rf"\b{c}\b", str(cls)) for c in _CLASSES), (
                f"{e['artifact']} field {field!r} is classified {str(cls)[:60]!r}, which names none "
                f"of the six authority classes {sorted(_CLASSES)}. An unclassified field has no "
                f"owner and no conflict rule.")


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

    This DELEGATES to `selftest.detect` rather than re-deriving the check. It used to carry its own
    copy — including its own `if control.id == "NC-08"` special case — and when NC-23 was added,
    only the CLI copy learned about it: `python -m tools.arch selftest` reported 23/23 green while
    this test failed NC-23, on the very same commit. One behavior, one implementation.
    """
    ok, detail = selftest.detect(control)
    assert ok, (
        f"{control.expect_rule} did NOT fire on an injected `{control.defect}` ({detail}). "
        f"The rule is DECORATIVE: it is claimed in the policy set but does not detect the "
        f"defect it names. That is worse than having no rule, because it manufactures "
        f"confidence.")


# ── 6. `impact --strict` is CLEARABLE by declaration (and only by declaration) ───────────────
#
# The gate used to fail on ANY BREAKING_CHANGE with no way to clear it, which made it the ONLY
# rule here without a declaration slot (ARCH-002 has approved_terminal_post_writers, ARCH-004
# approved_compile_cycles, ARCH-007 must_stay_lazy — each fires on the DELTA from what is
# declared). Absolute-fail cannot tell a deliberate deletion from an accidental one, and an
# unclearable red trains merge-past-red until the signal is worth nothing. These tests pin the
# discrimination: the gate must fail on what you did NOT say, and clear on what you did.
_B = "[BREAKING_CHANGE] "
_REP = {"reasons": [_B + "module fanops.gone was REMOVED",
                    "[COMPATIBLE_CHANGE] new route POST /kept",
                    _B + "CLI verb REMOVED: `migrate`"]}


def test_undeclared_breaking_lists_only_breaking_reasons():
    """A COMPATIBLE reason must never reach the fail list — the gate would fail on growth."""
    assert impact.undeclared_breaking(_REP, approved=[]) == [
        "module fanops.gone was REMOVED", "CLI verb REMOVED: `migrate`"]


def test_partial_declaration_fails_on_exactly_the_undeclared_one():
    """The discrimination that matters: saying one thing must not clear the OTHER thing."""
    assert impact.undeclared_breaking(_REP, approved=["CLI verb REMOVED: `migrate`"]) == [
        "module fanops.gone was REMOVED"]


def test_full_declaration_clears_the_gate():
    assert impact.undeclared_breaking(_REP, approved=[
        "module fanops.gone was REMOVED", "CLI verb REMOVED: `migrate`"]) == []


def test_a_reworded_reason_fails_safe():
    """Declarations match the tool's own printed string VERBATIM. If a reason is reworded, the old
    declaration stops matching and the break goes UNdeclared again — the author must re-affirm it.
    Failing open here would let a stale line vouch for a fact nobody re-read."""
    assert impact.undeclared_breaking(_REP, approved=["CLI verb REMOVED: migrate"]) != []


def test_unknown_impact_is_not_declarable(monkeypatch):
    """UNKNOWN is routed around the declaration entirely: cli.cmd_impact returns 1 BEFORE asking
    what was declared. A blast radius that could not be COMPUTED is not one anybody can vouch for —
    that is the whole reason the class exists, and a declaration must never wave it through.

    Driven through cmd_impact rather than read out of the source. The first version of this test
    grepped cli.py and split on the string "undeclared_breaking" — which also appears in the COMMENT
    above the UNKNOWN check, so the split landed mid-comment and the test failed on correct code. A
    test that a prose edit can break was testing the prose."""
    import argparse
    from tools.arch import cli

    rep = {"classification": "UNKNOWN_IMPACT", "base": "x", "changed_files": [],
           "reasons": ["[UNKNOWN_IMPACT] could not regenerate the derived architecture at base"],
           "architecture": {}, "implementation": {}}
    monkeypatch.setattr(cli.impact_mod, "report", lambda base: rep)
    # The exact refutation: pretend EVERYTHING is declared. If UNKNOWN were routed through the
    # declaration like BREAKING is, this would return 0 — a declaration would have waved through a
    # diff nobody could analyse.
    monkeypatch.setattr(cli.impact_mod, "undeclared_breaking", lambda r, approved=None: [])
    assert cli.cmd_impact(argparse.Namespace(base="x", strict=True)) == 1


def test_live_baseline_declares_nothing_it_has_not_reviewed():
    """The shipped declaration is a REVIEWED list, not a blanket. Every entry must be a real
    breaking-reason line — never a wildcard, a regex, or an empty string that would match away
    an unrelated break."""
    import json
    base = json.loads((REPO / ".reports" / "architecture" / "governance"
                       / "baselines.json").read_text(encoding="utf-8"))
    declared = base["approved_breaking_changes"]
    assert isinstance(declared, list)
    for entry in declared:
        assert isinstance(entry, str) and entry.strip() == entry and len(entry) > 20
        assert "*" not in entry and not entry.startswith("[")


def test_baseline_accept_carries_the_declaration_forward():
    """`approved_breaking_changes` is the ONE author-declared key in a file of derived ones. If
    `baseline --accept` rebuilt it from the tree like the others, every re-accept would silently
    wipe the declarations and re-arm the gate against already-reviewed breaks."""
    src = (REPO / "tools" / "arch" / "baseline.py").read_text(encoding="utf-8")
    body = src.split("def build", 1)[1].split("\ndef ", 1)[0]
    assert "approved_breaking_changes" in body
    assert 'get("approved_breaking_changes"' in body     # READ from the existing file...
    assert '"approved_breaking_changes": approved_breaking' in body   # ...and written back
