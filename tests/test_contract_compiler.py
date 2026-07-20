# tests/test_contract_compiler.py — ADR-0105 Phase 3: the change-contract compiler and verifier.
#
# This is the CI face of `python -m tools.contract`. It runs in the existing `unit` lane, beside
# test_arch_governance.py and test_ci_registry_validator.py, which is where this repository's
# mechanical governance already lives. No new marker, no new job, no new workflow — ADR-0105 §9
# leaves enforcement to Phase 6, and this file enforces nothing about other people's pull requests.
#
# NOTE ON READING THIS FILE: for every invariant a test here claims to protect, READ THE ASSERTION,
# NOT THE NAME. That is not boilerplate. Cycle 4 found a GREEN test in this repository that asserted
# a data-loss outcome and called it correct (RC-5 / AR-03), and `IMPL-007` sat in the policy set
# reporting nothing because its parser read a number out of prose. The negative controls exercised
# below exist so this file cannot become either of those.
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.contract import classify, derive, model, parse, report, selftest  # noqa: E402
from tools.contract.decide import RULE_IDS, decide  # noqa: E402

FIXTURES = _ROOT / "tests" / "fixtures" / "contracts"
BOOTSTRAP = _ROOT / "docs" / "contracts" / "CC-2026-07-18-change-contract-compiler.md"


# ── 1. the negative controls ────────────────────────────────────────────────────────────────
def test_every_negative_control_detects_its_defect():
    """AC-1. A MISSED control means the rule it names is decoration that manufactures confidence."""
    missed = []
    for c in selftest.CONTROLS:
        try:
            ok, detail = selftest.detect(c)
        except BaseException as exc:              # noqa: BLE001 — a control that cannot run proves nothing
            ok, detail = False, f"ERRORED {type(exc).__name__}: {exc}"
        if not ok:
            missed.append(f"  {c.id}  {c.defect}\n      -> {c.expect_rule or c.expect_code}: {detail}")
    assert missed == [], ("negative control(s) did not detect their injected defect:\n"
                          + "\n".join(missed))


def test_every_rule_has_a_firing_control():
    """AC-2. A rule nobody has tried to fool is a rule nobody should trust."""
    covered = {c.expect_rule for c in selftest.CONTROLS if c.expect_rule}
    uncovered = sorted((set(RULE_IDS) | {"OK"}) - covered)
    assert uncovered == [], (f"rule(s) with no negative control: {uncovered}. Add one to "
                             f"tools/contract/selftest.py::CONTROLS.")


def test_every_unsupported_construct_has_a_control():
    """AC-9. Twelve constructs are rejected by name; each must have a control that names its code."""
    from tools.contract.parse import _UNSUPPORTED
    declared = {code for code, _ in _UNSUPPORTED} | {"UNSUP-CRLF", "UNSUP-MULTIDOC", "DUP-KEY"}
    covered = {c.expect_code for c in selftest.CONTROLS if c.expect_code}
    assert declared - covered == set(), f"unsupported construct(s) with no control: {declared - covered}"


def test_control_ids_are_unique():
    ids = [c.id for c in selftest.CONTROLS]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert dupes == [], f"duplicate control id(s): {dupes}"


# ── 2. the decision function is pure, total and deterministic ───────────────────────────────
def _decision_input(**kw):
    d = parse.parse(selftest.build())
    base = {"declaration": d, "derived": model.Derived(), "gates": model.Gates(), "state": "draft",
            "diagnostics": (), "phase": "at-head"}
    return model.DecisionInput(**{**base, **kw})


def test_decide_is_deterministic():
    """AC-3. The same frozen input must produce a byte-identical verdict, every time."""
    di = _decision_input()
    first = decide(di)
    for _ in range(50):
        again = decide(di)
        assert (again.outcome, again.rule, again.why) == (first.outcome, first.rule, first.why)


def test_decide_is_total_over_every_rule_and_phase():
    """AC-4. Exactly one of six outcomes for every input, in every phase, and it never raises."""
    for phase in ("pre-implementation", "at-head", "merge-gate"):
        for tier in ("none", "cross-system", "governance", "live"):
            for unauth in ((), ("src/fanops/x.py",)):
                for codes in ((), ("GS-1",), ("I2",), ("AUTH-BLOB-MOVED",), ("UNFALSIFIABLE",)):
                    diags = tuple(model.Diagnostic(model.SEMANTIC, c, c) for c in codes)
                    di = _decision_input(phase=phase, diagnostics=diags,
                                         derived=model.Derived(risk_tier=tier, unauthorized=unauth))
                    out = decide(di)
                    assert out.outcome in model.DECISIONS
                    assert out.rule in set(RULE_IDS) | {"OK"}


def test_decide_imports_no_io():
    """The determinism proof is only worth asserting if `decide` genuinely has no hidden inputs."""
    src = (_ROOT / "tools" / "contract" / "decide.py").read_text(encoding="utf-8")
    banned = ("import subprocess", "import os", "import json", "from pathlib",
              "import urllib", "import time", "import datetime", "from .adapters",
              "from tools.arch", "from tools.ci")
    found = [b for b in banned if b in src]
    assert found == [], (f"decide.py must import no I/O — found {found}. Any of these would give a "
                         f"decision a hidden input, and AC-3/AC-4 would stop meaning what they say.")


def test_no_failure_path_yields_continue():
    """AC-14. Unavailable is never authorized: an unresolved input cannot produce `continue`.

    The claim is about the OUTCOME, in every phase. Pinning a specific rule id here would be a
    stricter assertion than the property being tested, and it would fail whenever some other rule
    legitimately matched first — which is exactly what happened: at `at-head`, `ST-3` (no approval
    at the current `D`) precedes `ST-7`, and both correctly say `stop`. That `ST-7` is itself
    reachable is proven separately, below and by `NC-C13p`.
    """
    for phase in ("pre-implementation", "at-head", "merge-gate"):
        di = _decision_input(phase=phase,
                             derived=model.Derived(unverifiable=("modules.json absent",)))
        assert decide(di).outcome != model.CONTINUE, phase


def test_st7_fires_when_nothing_higher_applies():
    """`ST-7` must be REACHABLE, not merely present: an unresolved input is its own halt."""
    gates = model.Gates(content_approval="satisfied", merge_authorization="satisfied")
    clean = _decision_input(gates=gates, derived=model.Derived())
    assert decide(clean).rule == "OK", "the baseline must be clean, or the next assertion proves nothing"
    di = _decision_input(gates=gates,
                         derived=model.Derived(unverifiable=("derived/modules.json absent",)))
    out = decide(di)
    assert (out.rule, out.outcome) == ("ST-7", model.STOP)


def test_exit_two_is_reserved_and_carries_no_decision():
    """AC-15. Exit 2 must never be readable as advisory success."""
    assert all(model.EXIT_CLASS[d] != model.EXIT_UNTRUSTWORTHY for d in model.DECISIONS)
    body = json.loads(report.as_json(report.untrustworthy("gh unavailable", "injected")))
    assert "decision" not in body
    assert body["exit_class"] == 2


# ── 3. the byte split and the digest ────────────────────────────────────────────────────────
def test_lifecycle_append_never_changes_the_digest():
    """AC-7. This is the mechanism that removes ADR-0105's circularity. It is byte-level."""
    raw = selftest.build()
    before = parse.parse(raw).digest
    grown = raw + b"| 2026-07-18T11:00:00Z | binding | pr=1 |\n"
    assert parse.parse(grown).digest == before


def test_any_declaration_byte_changes_the_digest():
    """AC-8. Editing an approved declaration must void its approval, and `D` is how."""
    before = parse.parse(selftest.build()).digest
    after = parse.parse(selftest.build(
        decl_mutate=lambda d: d.replace("Prove the compiler", "Prove  the compiler", 1))).digest
    assert after != before


def test_digest_matches_the_adr_reference_implementation():
    """The ADR's own two-line snippet is the authority for the byte range. Reproduce it exactly."""
    import hashlib
    raw = selftest.build()
    want = "sha256:" + hashlib.sha256(raw.split(b"\n## Lifecycle\n", 1)[0]).hexdigest()
    assert parse.parse(raw).digest == want


@pytest.mark.parametrize("value", ["true", "false", "null", "~", "yes", "no", "on", "off",
                                   "2026-07-18", "0x10", "1_000", "NaN"])
def test_no_implicit_typing(value):
    """AC-10. Every bare scalar is its literal text. A YAML library would coerce all twelve."""
    d = parse.parse(selftest.build(
        decl_mutate=lambda x: x.replace("id: CC-2026-07-18-example", f"id: {value}", 1)))
    assert d.value("id") == value


def test_the_field_set_is_closed_and_complete():
    """ADR-0105 §3.1: eighteen fields, plus `supersedes`. Nineteen slots, no more, no fewer."""
    assert len(model.ALL_FIELDS) == 19
    assert len(set(model.ALL_FIELDS)) == 19
    assert len(model.FRONTMATTER_FIELDS) == 8
    assert len(model.PROSE_FIELDS) == 3
    assert len(model.TABLE_FIELDS) == 8
    assert set(model.TABLE_COLUMNS) == set(model.TABLE_FIELDS)


def test_traits_may_be_empty_but_no_other_mandatory_field_may():
    """ADR-0105 §5.1: `contained` IS the empty trait set, so `traits: []` is valid, not missing."""
    assert model.EMPTY_ALLOWED_FIELDS == ("traits",)
    assert set(model.EMPTY_ALLOWED_FIELDS) <= set(model.MANDATORY_FIELDS)


# ── 4. the derivations (G2, G3, G4) ─────────────────────────────────────────────────────────
def _derived(name: str) -> dict:
    p = _ROOT / ".reports" / "architecture" / "derived" / f"{name}.json"
    if not p.exists():
        pytest.skip(f"derived/{name}.json is absent")
    return json.loads(p.read_text(encoding="utf-8"))


def test_path_to_module_reproduces_the_canonical_set():
    """AC-11 (gap G2). The transform is checkable against its own output because the map is total."""
    data = _derived("modules")
    assert derive.totality_holds(data), "the canonical subsystem partition is not total"
    src = _ROOT / "src" / "fanops"
    got = {classify.module_of(f"src/fanops/{p.relative_to(src)}")
           for p in src.rglob("*.py") if "__pycache__" not in p.parts}
    assert got - {None} == set(data["modules"])
    assert classify.module_of("src/fanops/post/__init__.py") == "fanops.post"
    assert classify.module_of("docs/CONFIG.md") is None


def test_blast_radius_matches_an_independent_closure():
    """AC-12 (gap G3). Compared against a fixed point written HERE, not the same BFS twice."""
    deps = _derived("dependencies")
    edges = deps["edges"]
    seed = "fanops.models" if "fanops.models" in edges else sorted(edges)[0]
    frontier, want = {seed}, set()
    while frontier:
        nxt = {s for s, k in edges.items() if set(k.get("compile", ())) & frontier} - want - {seed}
        want |= nxt
        frontier = nxt
    assert set(derive.blast_radius([seed], deps)) == want


def test_obligations_are_monotone_over_every_trait_subset():
    """AC-5. No trait may ever remove an obligation another trait imposes (ADR-0105 §5.1)."""
    traits = ("cross-system", "governance", "live")
    for i in range(8):
        sub = frozenset(t for j, t in enumerate(traits) if i >> j & 1)
        smaller, _ = derive.obligations(None, sub)
        for t in traits:
            larger, _ = derive.obligations(None, sub | {t})
            assert derive.obligations_are_monotone(smaller, larger), f"{sorted(sub)} + {t}"


def test_no_verifymap_predicate_is_dead():
    """AC-16 (gap G4). A requirement whose dimension nothing ever writes cannot arm — it is decoration."""
    from tools.arch import impact, verifymap
    src = Path(impact.__file__).read_text(encoding="utf-8")
    dead = [r.trigger for r in verifymap.REQUIREMENTS if src.count(f'"{r.trigger}"') < 2]
    assert dead == [], (f"requirement(s) whose impact dimension is initialized and never written: "
                        f"{dead}. ADR-0105 §9 records this exact defect as gap G4.")
    triggers = {r.trigger for r in verifymap.REQUIREMENTS}
    assert "changed_enums" in triggers
    assert not ({"changed_state_machines", "changed_rollback"} & triggers)


def test_changed_enums_arms_only_on_an_enum_delta():
    """AC-17. It must fire on the thing it names, and stay silent otherwise."""
    from tools.arch import verifymap
    armed = {r.trigger for r in verifymap.required_for(
        {"architecture": {"changed_enums": ["PostState: +holding"]}, "implementation": {}})}
    idle = {r.trigger for r in verifymap.required_for(
        {"architecture": {"changed_enums": []}, "implementation": {}})}
    assert "changed_enums" in armed and "changed_enums" not in idle


def test_changed_enums_is_ceilinged_at_compatible():
    """AC-27. Arming a requirement must not silently become an ENFORCEMENT change.

    `impact --strict` fails only on BREAKING_CHANGE / UNKNOWN_IMPACT, so the new dimension must
    never bump past COMPATIBLE — otherwise adding a verification requirement would start failing
    pull requests that used to pass, which is a different decision from the one that was approved.
    """
    src = (_ROOT / "tools" / "arch" / "impact.py").read_text(encoding="utf-8")
    block = src.split("changed_enums", 2)[2].split("── configuration")[0]
    for bad in ("bump(BREAKING", "bump(MIGRATION", "bump(UNKNOWN"):
        assert bad not in block, f"the changed_enums block must never {bad}: it would change `--strict`"


def test_rollback_obligation_is_reachable_at_both_levels():
    """AC-18. `changed_rollback` was retired; both of its replacement homes must actually fire."""
    assert "rollback" in model.MANDATORY_FIELDS
    live, _ = derive.obligations(None, frozenset({"live"}))
    contained, _ = derive.obligations(None, frozenset())
    assert "OB-ROLLBACK-REHEARSAL" in {o for o, _ in live}
    assert "OB-ROLLBACK-REHEARSAL" not in {o for o, _ in contained}


# ── 5. T3 completeness and the ADR pin ──────────────────────────────────────────────────────
def test_the_t3_pin_matches_the_adr_body():
    """AC-23. A transcription of an authority that can drift from it is a SECOND authority."""
    adr = _ROOT / "docs" / "adr" / "0105-reusable-change-contract-architecture.md"
    declared = classify.adr_t3_patterns(adr.read_text(encoding="utf-8"))
    assert declared, "the T3 predicate could not be parsed out of ADR-0105"
    assert set(declared) == set(classify.T3_PATTERNS)
    assert classify.adr_body_digest(adr.read_bytes()) == classify.ADR_0105_DIGEST
    assert classify.ADR_0105_DIGEST in adr.read_text(encoding="utf-8").split("\n---\n", 1)[0]


def test_tools_contract_is_a_governance_surface():
    """AC-19. The package that judges every other change must itself be judged."""
    assert classify.any_match("tools/contract/decide.py", classify.T3_PATTERNS)
    assert "tools/contract/**" in classify.T3_PATTERNS


def test_an_ordinary_package_is_not_a_governance_surface():
    """AC-19, the POSITIVE half. ADR-0105 §1: the uncontracted path must stay free."""
    assert not classify.any_match("src/fanops/newfeature/__init__.py", classify.T3_PATTERNS)
    findings = classify.governance_surface_findings(["src/fanops/newfeature/__init__.py"],
                                                    base_has=lambda p: False)
    assert findings == []


def test_a_new_tools_package_outside_t3_is_detected():
    """AC-20. The one structural signal, and it never consults a filename."""
    findings = classify.governance_surface_findings(["tools/newgov/__init__.py"],
                                                    base_has=lambda p: False)
    assert [f.code for f in findings] == ["GS-1"]


def test_gs2_does_not_fire_on_tests_or_on_a_contract_file():
    """The audit's DEF-1, pinned. GS-2 flagged four paths ADR-0105 and the design put OUTSIDE T3.

    ADR-0105 §3.6 makes `docs/contracts/**` conditionally outside `T3` — creating a contract does
    not trigger it — so flagging a governance contract's own file contradicts the ADR directly.
    The design's §19.2 says the same of `tests/**`. GS-2 firing on either produced a `stop` on a
    correct change, and it was invisible until an operator approval was simulated.
    """
    findings = classify.governance_surface_findings(
        [], base_has=lambda p: True,
        declared_governance_paths=("tests/test_contract_compiler.py",
                                   "tests/fixtures/contracts/valid_full.md",
                                   "docs/contracts/CC-2026-07-18-change-contract-compiler.md",
                                   "docs/governance/AGENT_CHANGE_SYSTEM_ROADMAP.md",
                                   "tools/arch/impact.py", "tools/contract/decide.py"))
    assert findings == [], f"GS-2 false positive on {[f.path for f in findings]}"


def test_gs2_still_catches_a_validator_location_outside_t3():
    """The corrected GS-2 must not be dead. This is ADR-0105 §1's named false negative verbatim:
    *"a new validator added under a path not enumerated"* — a single file that creates no package
    and so trips no `GS-1` signal."""
    findings = classify.governance_surface_findings(
        [], base_has=lambda p: True, declared_governance_paths=("tools/newvalidator.py",))
    assert [f.code for f in findings] == ["GS-2"]


def test_every_code_a_decision_rule_reads_is_produced_by_a_control():
    """The audit's DEF-3 — the METRIC HOLE, which is why DEF-1 shipped green.

    `AC-2` measured rule ids. `ST-8` looked covered because `NC-C23` names it, while `GS-2` — the
    other half of `ST-8`'s predicate — was never produced by any control. Rule-level coverage is
    strictly weaker than it appears whenever a rule reads more than one code.
    """
    ok, detail = selftest.detect(next(c for c in selftest.CONTROLS if c.id == "NC-C31"))
    assert ok, detail


# ── 6. the siblings, and the dependency direction ───────────────────────────────────────────
def test_neither_sibling_imports_tools_contract():
    """AC-24. `tools/ci/__init__.py:5` states the invariant; this is what proves it."""
    ok, detail = selftest.detect(next(c for c in selftest.CONTROLS if c.id == "NC-C28"))
    assert ok, detail


# ── 7. the fixtures and the bootstrap contract ──────────────────────────────────────────────
def _verify(path: Path, *extra: str) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, "-m", "tools.contract", "--json", "verify", str(path),
                        "--base", "origin/main", *extra],
                       cwd=_ROOT, capture_output=True, text=True, timeout=120)
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.returncode, {"stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:]}


@pytest.mark.parametrize("name", ["valid_minimal.md", "valid_full.md"])
def test_the_independent_fixtures_parse_with_no_structural_diagnostics(name):
    """D-6: independent fixtures, so self-validation is not the only evidence the compiler works."""
    d = parse.parse((FIXTURES / name).read_bytes(), path=f"tests/fixtures/contracts/{name}")
    bad = [x for x in d.diagnostics
           if x.kind in (model.MALFORMED, model.UNSUPPORTED, model.UNKNOWN)]
    assert bad == [], "\n".join(f"{x.code} at {x.located()}: {x.detail}" for x in bad)
    for f in model.MANDATORY_FIELDS:
        assert d.present(f), f"{name} is missing mandatory field `{f}`"


def test_the_full_fixture_exercises_every_slot_and_state():
    d = parse.parse((FIXTURES / "valid_full.md").read_bytes())
    assert {f.name for f in d.fields} == set(model.ALL_FIELDS)
    assert d.traits == frozenset(model.TRAITS)
    kinds = {e.kind for e in d.events}
    assert {"created", "approved", "binding", "implementation_started", "head_proposed", "merged",
            "accepted"} <= kinds


def test_an_incomplete_acceptance_event_is_malformed():
    """AC + D-3. `merged` never implies `accepted`, and an unauditable acceptance is not one."""
    from tools.contract import lifecycle
    raw = selftest.build(extra="| 2026-07-18T12:00:00Z | accepted | merge_sha=abc |\n")
    diags = lifecycle.validate_events(parse.parse(raw).events, main_blob=None, decl_bytes=b"",
                                      life_bytes=b"")
    assert "ACCEPT-INCOMPLETE" in {d.code for d in diags}


def test_merged_never_implies_accepted():
    """ADR-0105 §4.3/§4.3a. Neither direction: merge does not imply acceptance, and neither does a row."""
    from tools.contract import lifecycle
    d = parse.parse(selftest.build())
    for gates, expect in ((model.Gates(), "merged_unauthorized"),
                          (model.Gates(merge_authorization="satisfied"), "merged")):
        st = lifecycle.state(d, d.events, gates, merged=True, ci_green=False,
                             proposal_bound=False, pr_open=False, mandatory_ok=True)
        assert st == expect, f"merged with {gates.merge_authorization!r} derived {st!r}"
        assert st != "accepted"


def test_an_accepted_row_alone_never_derives_accepted():
    """§4.3a. The row used to BE the proof; now it is only the claim the proof is about."""
    from tools.contract import lifecycle
    raw = selftest.build(extra="| 2026-07-20T00:00:00Z | accepted | merge_sha=abc; decision=a; "
                               "evidence=e; date=2026-07-20; operator=operator; check_runs=1 |\n")
    d = parse.parse(raw)
    for acc in ("not_sought", "claimed", "unknown"):
        st = lifecycle.state(d, d.events, model.Gates(acceptance=acc), merged=True, ci_green=False,
                             proposal_bound=False, pr_open=False, mandatory_ok=True)
        assert st == "acceptance_claimed", f"acceptance={acc!r} derived {st!r}"
    st = lifecycle.state(d, d.events, model.Gates(acceptance="satisfied"), merged=True,
                         ci_green=False, proposal_bound=False, pr_open=False, mandatory_ok=True)
    assert st == "accepted", "a VERIFIED acceptance must still reach `accepted`"


def test_the_acceptance_gate_is_read_by_a_rule():
    """A gate no rule consumes is documentation, not enforcement — the defect §4.3a also fixed.

    `gates.acceptance` was computed and reported but read by NO rule, so its wrong value could not
    have been observed. This asserts the reader exists, which is what makes the corrected predicate
    matter.

    READ THE WHOLE MODULE, NOT `inspect.getsource(lambda)`. For a lambda spanning two lines
    `getsource` returns only the first, so the read on line two was invisible and this test reported
    `readers=[]` about a rule that plainly reads the gate — a false negative that would have been
    "fixed" by deleting the rule. The AST sees the entire expression regardless of layout.
    """
    from tools.contract import decide

    tree = ast.parse((_ROOT / "tools" / "contract" / "decide.py").read_text(encoding="utf-8"))
    readers = set()
    for call in (n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Rule"):
        rid = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else ""
        for node in ast.walk(call):
            # `<anything>.gates.acceptance` — the attribute CHAIN, so a bare local named
            # `acceptance` or a string mentioning it cannot pass for a read of the gate.
            if (isinstance(node, ast.Attribute) and node.attr == "acceptance"
                    and isinstance(node.value, ast.Attribute) and node.value.attr == "gates"):
                readers.add(rid)
    assert "ST-10" in readers, f"no registered rule reads `.gates.acceptance`; readers={sorted(readers)}"

    # A gate read by a rule that can never be reached is still decoration. `ST-7` (unavailable) MUST
    # be evaluated first, or an unreadable platform would be reported as an acceptance that failed to
    # verify — unavailability wearing the costume of a finding, which is the §4.3a defect exactly.
    order = [r.id for r in decide.RULES]
    assert order.index("ST-7") < order.index("ST-10"), (
        f"ST-7 must precede ST-10 in first-match-wins order; got {order}")


def test_post_merge_authorization_is_rederived_against_the_pre_merge_head():
    """§4.3a. A squash makes the authorized parent a non-ancestor; asking the squash is asking wrong.

    ASSERTED BEHAVIOURALLY, ON PURPOSE. The first version of this test asserted that the string
    `tree_of` appeared inside `_rederive_post_merge`, which is a claim about where a line of code
    lives rather than about what the tool does — and it went red for the RIGHT change, when the tree
    reads were correctly hoisted to where a failed read can still reach `Derived.unverifiable`. A
    test that forbids the fix it was written to protect is worse than no test.

    The invariant has three halves and all three are checked here, because each one alone is
    satisfiable by a defect: only-unavailable would pass if nothing ever compared trees, only-unequal
    would pass if unavailability were reported as a mismatch, and the ORDER matters because a read
    that completes after `Derived` is frozen produces a diagnostic no rule consumes.
    """
    from tools.contract import selftest as st

    # 1. Two readable, UNEQUAL trees are a FINDING: the landed content differs from the authorized
    #    content, which `merged_unverified` names exactly. Never `ST-7` — nothing was unavailable.
    raw, kw = st._landed(rows="", trees={"h" * 40: "a" * 40, st.MERGE_SHA: "b" * 40})
    dec, ctx = st._run(raw, _trees=kw.pop("trees"), **kw)
    assert ctx["state"] == "merged_unverified", (
        f"two readable unequal trees must be a FINDING, got {ctx['state']!r} via {dec.rule}")
    assert dec.rule != "ST-7", "an unequal comparison is a disagreement, never unavailability"

    # 2. An UNREADABLE tree is NOT a mismatch. It must stop at `ST-7`, and the only way `ST-7` can
    #    see it is if the read completed before `Derived` froze `unverifiable` into a tuple — a read
    #    landing after the freeze would produce a diagnostic no rule consumes, a silent fail-open.
    raw, kw = st._landed(rows="")
    kw.pop("trees", None)
    dec, ctx = st._run(raw, repo_fail="tree", **kw)
    assert dec.rule == "ST-7", f"an unresolvable tree must be UNAVAILABLE, got {dec.rule}"
    assert ctx["state"] != "merged_unverified", (
        "a tree that could not be read was reported as a tree that did not match")

    # 3. The carrier itself, asserted rather than inferred: the failed read is IN the frozen tuple.
    carried = ctx["derived"].unverifiable
    assert any("could not be read" in u for u in carried), (
        f"the failed tree read must be carried in Derived.unverifiable, got {carried}")


def test_the_bootstrap_contract_is_structurally_valid():
    """D-6 step 4, mechanized. The contract that governs this change must itself parse cleanly."""
    d = parse.parse(BOOTSTRAP.read_bytes(), path=f"docs/contracts/{BOOTSTRAP.name}")
    bad = [x for x in d.diagnostics
           if x.kind in (model.MALFORMED, model.UNSUPPORTED, model.UNKNOWN)]
    assert bad == [], "\n".join(f"{x.code} at {x.located()}: {x.detail}" for x in bad)
    assert d.id == BOOTSTRAP.stem, "ADR-0105 §6: the id IS the filename stem in docs/contracts/"
    for f in model.MANDATORY_FIELDS:
        assert d.present(f) and (d.value(f) or f in model.EMPTY_ALLOWED_FIELDS)


def test_the_bootstrap_contract_grants_only_design_and_implement():
    """ADR-0105 §10: partial authorization is not full authorization. Merge is a separate grant."""
    d = parse.parse(BOOTSTRAP.read_bytes())
    assert set(d.value("authorized_actions")) == {"design", "implement"}


def _contract_refs(parsed, default_head="HEAD"):
    """The two commits a contract's change actually spans, read OFF THE ARTIFACT.

    A contract governs ONE change: a fixed diff between two fixed commits. Both are recorded in its
    own lifecycle — `created.base_sha`, and once it lands, `merged`/`accepted`'s `merge_sha`. Any
    formulation with a moving end is wrong in one direction or the other, and both were live
    defects: `origin/main...HEAD` empties the diff the moment the change lands, and `base_sha...HEAD`
    grows it the moment ANYTHING ELSE lands. Reading both ends from the artifact is the only version
    that stays true for the whole life of the contract, including forever after acceptance.
    """
    base = next((e.get("base_sha") for e in parsed.events
                 if e.kind == "created" and e.get("base_sha")), "origin/main")
    head = next((e.get("merge_sha") for e in reversed(parsed.events)
                 if e.kind in ("accepted", "merged") and e.get("merge_sha")), default_head)
    return base, head


def test_the_bootstrap_contract_declares_this_exact_patch():
    """AC-28 / ADR-0105 §5.3. The declared surfaces must be the surfaces, with nothing extra."""
    parsed = parse.parse(BOOTSTRAP.read_bytes())
    declared = {r["path"] for r in parsed.value("expected_surfaces")}
    base, head = _contract_refs(parsed)
    r = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], cwd=_ROOT,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        pytest.skip(f"{base}...{head} is not resolvable in this checkout")
    changed = {f for f in r.stdout.split() if f}
    if not changed:
        pytest.skip("no diff against origin/main (the change has already landed)")
    assert changed - declared == set(), (
        f"UNAUTHORIZED surface(s) — in the diff, not in the contract: {sorted(changed - declared)}")


def test_ac21_passes_once_the_operator_approval_is_simulated():
    """AC-21, and the guard that stops the audit's finding from recurring unnoticed.

    `ST-3` (no approval naming `D`) is row 10 and was MASKING row 15, `ST-8`. Every decision the
    verifier returned before approval was correct, and every one of them hid a live defect behind
    it. The only way to see past an operator gate is to simulate passing it — so this test does,
    in memory, and asserts the two things that matter: that a lifecycle append leaves `D` untouched,
    and that with approval the contract reaches `continue` at its own head.

    Nothing is written. The repository is not modified and no approval is granted.
    """
    from tools.contract.adapters import RepoPort
    from tools.contract.__main__ import Ports, run as run_pipeline

    p = f"docs/contracts/{BOOTSTRAP.name}"
    real = RepoPort()
    if real.resolve("origin/main") is None:
        pytest.skip("origin/main is not resolvable in this checkout")
    raw = real.blob("HEAD", p)
    if raw is None:
        pytest.skip("the contract has not landed at HEAD in this checkout")

    parsed = parse.parse(raw)
    d = parsed.digest

    # BOTH ENDS COME OFF THE ARTIFACT — see `_contract_refs`. This test has now assumed the world
    # would hold still three times: a hardcoded timestamp, then `origin/main` as the base (which
    # emptied the diff once the change landed, deriving no traits and correctly answering `CL-2`),
    # then a moving `HEAD` (which GREW the diff the moment any later change landed, correctly
    # answering `ST-1` on files this contract never claimed). Each fix was right about its own case
    # and wrong about the class. A contract's change is a fixed diff between two fixed commits, and
    # both of them are written down in its lifecycle; nothing else is stable enough to assert on.
    base_ref, head_ref = _contract_refs(parsed)
    for ref in (base_ref, head_ref):
        if real.resolve(ref) is None:
            pytest.skip(f"the contract's recorded ref {ref[:12]} is not in this checkout")

    # Two states must both work, and the first version of this test only handled one. It appended a
    # HARDCODED timestamp, which was fine while the last event was `created` — and went red the
    # moment the operator's real approval landed later in the day, because the append was then
    # non-monotone and the verifier correctly answered `A5`. The product was right; the test had
    # assumed the lifecycle would stand still. Derive the instant, and skip the simulation entirely
    # once the real approval is on record.
    if any(e.kind == "approved" and e.get("digest") == d for e in parsed.events):
        approved = raw
    else:
        from datetime import datetime, timedelta, timezone
        last = max((e.timestamp for e in parsed.events if re.match(r"^\d{4}-\d\d-\d\dT", e.timestamp)),
                   default="2026-07-18T00:00:00Z")
        nxt = (datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
               + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        approved = raw + f"| {nxt} | approved | digest={d}; token=APPROVE |\n".encode()
        assert parse.parse(approved).digest == d, "a lifecycle append must never change `D` (ADR §3)"

    # ISOLATION. AC-21 asks ONE question: with the operator's content approval on record, does the
    # contract reach `continue` at its own head? Everything the contract accumulated AFTER that point
    # — `merged`, `accepted` — is later history that this assertion is not about, and leaving it in
    # is what let unrelated machinery answer in its place. It did: the acceptance-evidence
    # requirement turned the trailing `accepted` row into a permanent `ST-10`, and before that a
    # structural-completeness check turned it into `A5`, both of them masking AC-21 entirely.
    #
    # So the simulated lifecycle is TRUNCATED at the first post-merge event and the approval is
    # appended there. The fixture is complete and internally consistent at that point, and the
    # injected merge facts agree with it (this PR is not merged in the simulated world), so no gate
    # is left comparing the record against a platform that contradicts it.
    decl_b, _, life_b = approved.partition(parse.BOUNDARY)
    lines = life_b.decode().splitlines(keepends=True)
    kept, stop = [], False
    for x in lines:
        if x.startswith("| 20"):
            if stop or re.match(r"^\|[^|]*\|\s*(merged|accepted)\s*\|", x):
                stop = True
                continue
            kept.append(x)
        else:
            kept.append(x)
    approved = decl_b + parse.BOUNDARY + "".join(kept).encode()
    assert parse.parse(approved).digest == d, "truncating the lifecycle must not move `D`"
    sim = parse.parse(approved)
    assert not [e for e in sim.events if e.kind in ("merged", "accepted")], (
        f"the isolated fixture must carry no post-merge event; got "
        f"{[e.kind for e in sim.events]}")

    class _Approved:
        """The simulated blob at EVERY ref, `origin/main` included.

        Returning it only for the head would make the head diverge from the landed record and fire
        `LIFECYCLE-REWRITTEN` — a true finding about a fixture this test invented, not about the
        product. Both ends move together, so the append-only check still bites on anything else.
        """
        def __init__(self, i): self.i = i
        def blob(self, r, q):
            b = self.i.blob(r, q)
            return approved if (q == p and b is not None) else b
        def blob_sha(self, r, q): return self.i.blob_sha(r, q)
        def diff_names(self, b, h): return self.i.diff_names(b, h)
        def contains(self, r, q): return self.i.contains(r, q)
        def resolve(self, r): return self.i.resolve(r)
        def is_ancestor(self, a, b): return self.i.is_ancestor(a, b)
        def tree_of(self, r): return self.i.tree_of(r)

    # The platform is INJECTED, never reached. `pr=None` is deliberate — it exercises the ordinary
    # path that resolves the governed PR from `binding` — and a unit test must not depend on a token.
    ports = Ports(repo=_Approved(real), merge_facts=selftest.FakeMergeFacts(
        merged=False, merge_sha="", merged_at="", pr_head=""))
    got = {}
    for phase in ("pre-implementation", "at-head", "merge-gate"):
        dec, _ = run_pipeline(ports, p, base=base_ref, head=head_ref, pr=None, phase=phase)
        got[phase] = (dec.outcome, dec.rule)

    assert got["pre-implementation"] == ("continue", "OK"), got
    assert got["at-head"] == ("continue", "OK"), (
        f"AC-21: with approval granted the contract must reach `continue` at its own head; got "
        f"{got['at-head']}. If this is `ST-8`, GS-2 has regressed to the over-broad form.")
    # The merge gate is a SEPARATE grant and must not fall out of the content approval. With no
    # operator `merge_approved` on record there is no authorization at all, so `ST-9` is the honest
    # verdict — the correction made the gate REACHABLE by one operator, not automatic.
    if not any(e.kind == "merge_approved" for e in parse.parse(approved).events):
        assert got["merge-gate"][1] == "ST-9", (
            f"with no merge authorization recorded the gate must still stop; got {got['merge-gate']}")


# ── ADR-0105 §4.1a — parent-binding and single-operator authorization ───────────────────────
#
# The original exact-head gate was UNSATISFIABLE here: it admitted only a non-author `APPROVED` PR
# review, `Fleezyflo` is the sole account with push access AND the author of every PR, and GitHub
# refuses self-approval. The same self-reference (a record cannot name the commit computed over it)
# also made `head_proposed`, and therefore the `implemented` state, unreachable in EVERY repository.
# The correction removes the second person entirely: this repository has ONE human operator, and a
# gate requiring a second is unsatisfiable rather than strict. These tests hold the corrected gate to
# both halves of its claim — the operator alone can authorize, and every binding check still bites.
_P = "docs/contracts/CC-2026-07-18-change-contract-compiler.md"
_PARENT, _HEAD = "a" * 40, "b" * 40


def _approval(*, parent=None, digest=None, pr=1, operator="solo", phrase="APPROVE THE MERGE",
              drop=()):
    """One operator `merge_approved` row carrying every value ADR-0105 §4.1a requires."""
    kv = [("parent_sha", parent or _PARENT), ("digest", digest), ("pr", pr), ("operator", operator),
          ("token", phrase)]
    body = "; ".join(f"{k}={v}" for k, v in kv if k not in drop)
    return f"| 2026-07-19T10:00:00Z | merge_approved | {body} |\n"


class _Repo:
    """Exactly enough git to answer the four §4.1a checks, so each one can be failed in isolation."""

    def __init__(self, parent_raw, head_raw, changed=(_P,), ancestor=True):
        self.parent_raw, self.head_raw = parent_raw, head_raw
        self.changed, self.ancestor = list(changed), ancestor

    def blob(self, ref, path): return self.parent_raw if ref == _PARENT else self.head_raw

    def diff_names(self, base, head): return sorted(self.changed)

    def is_ancestor(self, a, b): return self.ancestor


def _appended(parent: bytes, row: str | None = None) -> bytes:
    """`parent` plus ONE operator authorization naming the digest `parent` actually has."""
    return parent + (row or _approval(digest=parse.parse(parent).digest)).encode()


def _gate(parent, head, *, changed=(_P,), ancestor=True, pr=1):
    """The gate, with NO review and NO principal parameter — because none exists to pass."""
    from tools.contract import lifecycle
    d = parse.parse(head)
    return lifecycle.gates(d, d.events, head_sha=_HEAD, pr=pr, main_has_contract=False,
                           repo=_Repo(parent, head, changed, ancestor), path=_P, raw=head)


def test_a_single_operator_can_authorize_a_merge_with_zero_reviews():
    """The whole correction in one assertion.

    The prior model required a non-author `APPROVED` PR review. This repository has one human
    operator, so that gate could be waited on forever and never cleared — ADR-0105 §4.1a already
    named that outcome INOPERATIVE rather than safe. The operator's own parent-bound authorization
    now satisfies it, and no review is read to get there.
    """
    parent = selftest.build()
    g = _gate(parent, _appended(parent))
    assert g.merge_authorization == "satisfied", g.detail
    assert g.approved_head == _PARENT, "the gate must report WHICH parent was authorized"
    assert any("OPERATOR merge authorization accepted" in x for x in g.detail), g.detail


def test_the_gate_signature_cannot_accept_review_or_principal_evidence():
    """Absence proven STRUCTURALLY, not behaviourally.

    A behavioural test can only show that one input did not change one verdict. This shows the
    inputs do not exist: `gates()` has no parameter to pass them through, so no caller, flag or
    future edit can reintroduce the dependency without changing this signature and going red.
    """
    import inspect
    from tools.contract import lifecycle
    params = set(inspect.signature(lifecycle.gates).parameters)
    assert not params & {"reviews", "principals"}, f"second-person inputs are back: {params}"
    for gone in ("read_reviews", "read_principals", "WITNESSED", "UNWITNESSED"):
        assert not hasattr(lifecycle, gone), f"{gone} still exists"
    from tools.contract import adapters
    assert not hasattr(adapters, "ReviewPort"), "ReviewPort still exists"


def test_no_authorization_module_reads_a_review_or_a_principal_census():
    """`NC-SO-11`'s CI face. Prose may NAME what was removed; executable code may not contain it."""
    banned = ("approvals(", "write_principals", "read_reviews", "read_principals", "ReviewPort",
              "reviewDecision", "collaborators")
    root = Path(__file__).resolve().parents[1] / "tools" / "contract"
    hits = []
    for mod in ("lifecycle.py", "decide.py", "adapters.py", "__main__.py", "report.py", "model.py"):
        for line in (root / mod).read_text(encoding="utf-8").splitlines():
            st = line.lstrip()
            if st.startswith("#") or st.startswith('"') or st.startswith("'"):
                continue
            hits += [f"{mod}: {st[:60]}" for b in banned if b in line]
    assert not hits, f"second-person reads remain in the authorization path: {hits}"


def test_st_4_is_deleted_and_not_recreated_under_another_identifier():
    """`ST-4` required a second person. It is gone, and nothing may carry its predicate forward."""
    from tools.contract import decide
    ids = [r.id for r in decide.RULES]
    assert "ST-4" not in ids, "ST-4 is still registered"
    assert len(ids) == len(set(ids)), f"duplicate rule ids: {ids}"
    # SCANNED ACROSS EVERY MODULE, not just this one. Scoping it to `decide.py` let a live crash
    # ship: `cmd_state` in `__main__.py` kept reading `g.exact_head_approval` after the rename and
    # exited 2 on every invocation. A rename guard that checks one file proves one file.
    from tools.contract import adapters, lifecycle, report
    import tools.contract.__main__ as main_mod
    for mod in (decide, lifecycle, adapters, main_mod, report, model):
        for i, line in enumerate(Path(mod.__file__).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            assert "exact_head_approval" not in line, \
                f"{Path(mod.__file__).name}:{i} still reads the deleted gate field"


def test_every_cli_verb_reads_only_fields_that_exist():
    """A VERB WITH NO TEST IS A VERB THAT CAN SHIP BROKEN — and one did.

    `cmd_state` had zero callers besides the dispatcher, so nothing exercised it and a stale field
    read survived into `main`, exiting 2 on every invocation. The selftest calls `run()` directly
    and never reaches the verb wrappers, which is exactly the gap this closes.

    Checked by SOURCE rather than by invocation: the verbs need a repository and a network, and a
    test that needs those proves the sandbox, not the code. Every gate attribute each verb reads must
    be a real field of `Gates`.
    """
    import ast
    import dataclasses
    import tools.contract.__main__ as main_mod
    fields = {f.name for f in dataclasses.fields(model.Gates)}
    tree = ast.parse(Path(main_mod.__file__).read_text(encoding="utf-8"))
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name.startswith("cmd_")]:
        gate_vars = {t.id for a in ast.walk(fn) if isinstance(a, ast.Assign)
                     for t in a.targets if isinstance(t, ast.Name)
                     and isinstance(a.value, ast.Subscript)
                     and getattr(getattr(a.value.slice, "value", None), "__eq__", None)
                     and getattr(a.value.slice, "value", None) == "gates"}
        for node in ast.walk(fn):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in gate_vars):
                assert node.attr in fields, \
                    f"{fn.name} reads `Gates.{node.attr}`, which does not exist; fields={sorted(fields)}"


def test_an_authorization_for_a_different_contract_or_pr_does_not_bind():
    """`D` and the PR number are part of the authorization, not decoration."""
    parent = selftest.build()
    d = parse.parse(parent).digest
    wrong_d = _appended(parent, _approval(digest="sha256:" + "0" * 64))
    assert _gate(parent, wrong_d).merge_authorization != "satisfied"
    wrong_pr = _appended(parent, _approval(digest=d, pr=999))
    assert _gate(parent, wrong_pr).merge_authorization != "satisfied"


def test_an_authorization_naming_no_operator_or_token_does_not_bind():
    """The agent may transcribe an operator's token; it may never author one. A row quoting nothing
    records that something was authorized without recording WHAT."""
    parent = selftest.build()
    d = parse.parse(parent).digest
    for drop in (("token",), ("operator",)):
        head = _appended(parent, _approval(digest=d, drop=drop))
        g = _gate(parent, head)
        assert g.merge_authorization != "satisfied", f"dropping {drop} still authorized: {g.detail}"


def test_an_unreadable_repository_leaves_authorization_unknown_not_satisfied():
    """Fail closed. Git being unreadable must never be the reason a merge becomes authorized."""
    from tools.contract import lifecycle
    parent = selftest.build()
    head = _appended(parent)
    d = parse.parse(head)
    g = lifecycle.gates(d, d.events, head_sha=_HEAD, pr=1, main_has_contract=False,
                        repo=None, path=_P, raw=head)
    assert g.merge_authorization == "unknown", g.detail


@pytest.mark.parametrize("mutate,why", [
    (dict(changed=(_P, "src/fanops/publish.py")), "code rode in behind the approval"),
    (dict(ancestor=False), "the approved commit is not an ancestor of the head"),
])
def test_parent_binding_rejects_a_head_that_moved_for_any_other_reason(mutate, why):
    parent = selftest.build()
    g = _gate(parent, _appended(parent), **mutate)
    assert g.merge_authorization != "satisfied", f"{why}: {g.detail}"


def test_parent_binding_rejects_a_declaration_edited_inside_the_one_permitted_path():
    """The check the path-level test CANNOT do. Without it the delta proof has a hole exactly the
    size of the contract file, which is the one file the approval permits to move."""
    parent = selftest.build()
    head = _appended(selftest.build(decl_mutate=lambda d: d.replace("Prove the", "Proved the", 1)))
    g = _gate(parent, head)
    assert g.merge_authorization != "satisfied", g.detail
    assert any("declaration changed" in x for x in g.detail), g.detail


def test_parent_binding_rejects_a_rewritten_lifecycle():
    parent = selftest.build(extra="| 2026-07-18T11:00:00Z | binding | pr=1 |\n")
    g = _gate(parent, _appended(selftest.build()))
    assert g.merge_authorization != "satisfied", g.detail


def test_a_merge_approval_naming_no_commit_is_a_malformed_lifecycle():
    """`NC-C57`'s CI face. An approval that names no commit approves nothing in particular."""
    from tools.contract import lifecycle
    raw = selftest.build(extra="| 2026-07-19T10:00:00Z | merge_approved | operator=solo |\n")
    diags = lifecycle.validate_events(parse.parse(raw).events, main_blob=None, decl_bytes=b"",
                                      life_bytes=b"")
    assert "PARENT-BIND-INCOMPLETE" in {d.code for d in diags}


def test_implemented_is_reachable_now_that_head_proposed_binds_to_its_parent():
    """The SECOND instance of the same defect, and the one with no maintainer-count involvement.

    §4.3 required `head_proposed` to name the CURRENT head. Appending the event is itself the commit,
    so the event would have to carry a hash computed over its own bytes. No repository of any size
    could satisfy that, and no control covered it — the only fixture used the placeholder `deadbeef`,
    which can never equal a real head.
    """
    from tools.contract import lifecycle
    row = f"| 2026-07-19T09:00:00Z | head_proposed | parent_sha={_PARENT}; ci=green |\n"
    parent = selftest.build()
    head = _appended(parent, row)
    d = parse.parse(head)
    proposal = [e for e in d.events if e.kind == "head_proposed"][-1]
    bound, why = lifecycle.parent_binds(proposal, repo=_Repo(parent, head), path=_P,
                                        head_sha=_HEAD, raw=head)
    assert bound, why
    assert lifecycle.state(d, d.events, model.Gates(), merged=False, ci_green=True,
                           proposal_bound=bound, pr_open=True, mandatory_ok=True) == "implemented"

    # And the original test could not have passed: the event's own commit is what moved the head.
    assert not lifecycle.parent_binds(proposal, repo=_Repo(parent, head), path=_P,
                                      head_sha="", raw=head)[0]


def test_the_bootstrap_contract_prohibits_what_phase_3_must_not_touch():
    """The prohibitions are load-bearing: ADR-0105 §9 says Phase 3 adds NO CI job."""
    prohibited = {r["glob"] for r in parse.parse(BOOTSTRAP.read_bytes()).value("prohibited_scope")}
    for must in (".github/workflows/**", ".github/ci-control-registry.yml", "src/fanops/**",
                 "requirements/**", ".orchestration/**"):
        assert must in prohibited, f"the contract must explicitly prohibit {must}"


def test_no_contract_carries_the_print_budget_assignment():
    """ADR-0105 §11.3. `IMPL-007` reads that assignment form as a LIVE CLAIM — it turns the gate red."""
    rx = re.compile(r"_CLI_PRINT_COUNT\s*=\s*\d+")
    for p in sorted((_ROOT / "docs" / "contracts").glob("*.md")) + sorted(FIXTURES.glob("*.md")):
        assert not rx.search(p.read_text(encoding="utf-8")), f"{p.name} carries the assignment form"


# ── 8. the tool is read-only ────────────────────────────────────────────────────────────────
def test_no_verb_writes_into_the_repository():
    """A tool that can write the artifact it validates lets an agent satisfy the gate by editing
    the evidence. `tools/arch` writes on purpose (regen/docs/baseline); this package never does."""
    banned = ("write_text(", "write_bytes(", ".mkdir(", "open(", "shutil.", "os.replace")
    for f in sorted((_ROOT / "tools" / "contract").glob("*.py")):
        src = f.read_text(encoding="utf-8")
        code = "\n".join(line for line in src.split("\n") if not line.strip().startswith("#"))
        found = [b for b in banned if b in code]
        assert found == [], f"{f.name} contains a write primitive {found} — every verb is read-only"


# ── CC-2026-07-19 · shipped-CLI reachability for landed-lifecycle integrity ──────────────────
#
# `LIFECYCLE-REWRITTEN` was implemented, correct, and covered by `NC-C10b` — and the shipped command
# could not produce it. `main_blob` was fetched only `if head is not None`, so the DEFAULT invocation
# (the one contract success conditions name) read the working tree with the landed comparison
# switched OFF, while `--head HEAD` switched it on but read the committed blob, where the tampering
# is not. Neither supported invocation could see a rewritten landed row.
#
# A rule-level control cannot catch that BY CONSTRUCTION: it hands the rule its input directly, which
# is exactly the step that was broken. So every case here drives `__main__.main(argv)` — argparse,
# dispatch, the nine stages, the report and the exit-class mapping — against a real git repository.
# Only the repository LOCATION is redirected; no port, rule or diagnostic is faked.
_CLI_ADR = "docs/adr/0105-reusable-change-contract-architecture.md"
_CLI_CONTRACT = "docs/contracts/CC-2026-07-18-fixture-minimal.md"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


# ── the hermetic platform ───────────────────────────────────────────────────────────────────
#
# These CLI cases drive the PRODUCTION entry point, and the production entry point resolves the
# governed PR from the contract's own `binding` row and then READS THE PLATFORM. That is correct and
# is not relaxed here: a verifier that skipped the read for a landed contract would answer confidently
# about facts it never looked at. What must not happen is the read reaching the real GitHub — a unit
# test that succeeds only where a token exists is not hermetic, and one that silently answers `ST-7`
# everywhere else is not testing anything.
#
# So the platform is SERVED, deterministically, by a `gh` stand-in on a temporary PATH. It answers
# only the two closed endpoints `MergeFactsPort` can construct, from a fixture table, and it LOGS
# every call so a test can prove the read actually happened rather than infer it from a green result.
_FAKE_SLUG = "fixture-owner/fixture-repo"
_FAKE_CONTEXT = "unit (fast, no toolchain)"
_FAKE_WORKFLOW = ".github/workflows/ci.yml"
_FAKE_GH = '''"""A deterministic `gh` stand-in. Serves ONLY `gh api <path>` for paths in the fixture table."""
import json, os, sys

argv = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(argv) + "\\n")
if len(argv) < 2 or argv[0] != "api":
    sys.stderr.write("fake gh: only `gh api <path>` is served, got %r\\n" % (argv,))
    raise SystemExit(2)
if os.environ.get("FAKE_GH_BROKEN"):
    sys.stderr.write("gh: authentication required\\n")
    raise SystemExit(1)
with open(os.environ["FAKE_GH_TABLE"], encoding="utf-8") as fh:
    table = json.load(fh)
if argv[1] not in table:
    sys.stderr.write("fake gh: no fixture for %s\\n" % argv[1])
    raise SystemExit(1)
sys.stdout.write(json.dumps(table[argv[1]]))
'''


def _build_fake_gh(repo, *, table=None, base_sha=""):
    """Write the stand-in, its fixture table, and a PATH directory that has `git` but NOT `gh`.

    Two directories, because "unusable" and "missing" are different failures and both must land on
    `ST-7`. `bin-nogh` carries a `git` symlink so the missing-`gh` case isolates the tool under test
    instead of also removing the one every port needs.
    """
    bin_dir, nogh = repo / ".fakebin", repo / ".fakebin-nogh"
    bin_dir.mkdir(exist_ok=True)
    nogh.mkdir(exist_ok=True)
    (bin_dir / "fake_gh.py").write_text(_FAKE_GH, encoding="utf-8")
    # A `/bin/sh` wrapper, NOT a `#!{sys.executable}` shebang. This repository's virtualenv lives
    # under a path containing a space, and a shebang interpreter path cannot contain one — the
    # kernel splits on it, the exec fails with ENOENT, and the port reports "gh not found on PATH".
    # Every test would then pass its ST-7 assertion and fail its positive one, for a reason that has
    # nothing to do with the product. Quoting inside `sh` is what makes the path survive.
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bin_dir / "fake_gh.py"}" "$@"\n',
                  encoding="utf-8")
    gh.chmod(0o755)
    real_git = shutil.which("git")
    assert real_git, "the fixture needs a real `git` to place on the restricted PATH"
    for d in (bin_dir, nogh):
        link = d / "git"
        if not link.exists():
            link.symlink_to(real_git)
    # PRs 1, 2 and 99 — every number the cases below put in a `binding` row. `_pr_of` reads the LAST
    # one, so case B's appended `pr=2` and case C's tampered `pr=99` each re-target the read; serving
    # all three keeps a case's result about the thing it mutated rather than about a fixture gap.
    unmerged = [{"head": {"sha": ""}, "base": {"sha": base_sha}, "merge_commit_sha": "",
                 "merged_at": "", "merged": False}]
    (repo / ".fake-gh-table.json").write_text(json.dumps(table if table is not None else {
        f"repos/{_FAKE_SLUG}/pulls/{n}": unmerged for n in (1, 2, 99)
    }), encoding="utf-8")
    return bin_dir, nogh


def _serve_platform(monkeypatch, repo, *, broken=False, missing=False):
    """Point the ports at the fixture repository and the fake platform for ONE test."""
    from tools.contract import adapters
    # ONLY the slug resolver. `adapters.REPO` also anchors every other path this tool resolves, so
    # repointing it at the fixture makes unrelated ports fail on files that live in the real tree —
    # a broad monkeypatch that breaks more than it isolates.
    monkeypatch.setattr(adapters, "_repo_slug", lambda: _FAKE_SLUG)
    bin_dir, nogh = repo / ".fakebin", repo / ".fakebin-nogh"
    monkeypatch.setenv("FAKE_GH_TABLE", str(repo / ".fake-gh-table.json"))
    monkeypatch.setenv("FAKE_GH_LOG", str(repo / ".fake-gh.log"))
    (repo / ".fake-gh.log").write_text("", encoding="utf-8")
    if broken:
        monkeypatch.setenv("FAKE_GH_BROKEN", "1")
    # `str(...)` ONLY — the real PATH is deliberately not appended, so a fixture gap surfaces as a
    # missing tool here instead of silently reaching the developer's authenticated `gh`.
    monkeypatch.setenv("PATH", str(nogh if missing else bin_dir))


@pytest.fixture(scope="module")
def cli_repo(tmp_path_factory):
    """A real git repository with a LANDED contract on `origin/main`, carrying TWO lifecycle rows.

    Two, not one, and that is load-bearing: with a single row `reversed()` is the identity and case E
    would assert nothing while passing. `_life` refuses to run against fewer, so the fixture cannot
    quietly regress into a vacuous one.

    `origin/main` is made with `update-ref`, not a clone — the rule under test is git-local, and a
    fixture that needed a remote would smuggle network dependence into the proof of that claim.
    """
    repo = tmp_path_factory.mktemp("cli_repo")
    (repo / "docs" / "adr").mkdir(parents=True)
    (repo / "docs" / "contracts").mkdir(parents=True)
    (repo / ".agents").mkdir()
    (repo / ".github").mkdir()
    (repo / ".agents" / "lanes.json").write_text("{}", encoding="utf-8")
    (repo / _CLI_ADR).write_text("# ADR-0105 (fixture)\n", encoding="utf-8")
    # The required set is read from the registry AT THE CONTRACT'S OWN BASE, so the fixture must
    # carry one at the commit it names — the same pinning the product does, not a stub beside it.
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / _FAKE_WORKFLOW).write_text(
        f"jobs:\n  unit:\n    name: {_FAKE_CONTEXT}\n", encoding="utf-8")
    # The registry must MAP each required context to the workflow and job that produce it — a bare
    # name is not provenance, so the verifier refuses a registry that declares one without the other.
    (repo / ".github" / "ci-control-registry.yml").write_text(
        f"current_required_contexts:\n  - {_FAKE_CONTEXT}\n"
        f"controls:\n"
        f"  - id: CI-UNIT\n"
        f"    name: {_FAKE_CONTEXT}\n"
        f"    workflow: {_FAKE_WORKFLOW}\n"
        f"    job: unit\n", encoding="utf-8")
    (repo / "impact.json").write_text(json.dumps(
        {"classification": "COMPATIBLE_CHANGE", "architecture": {}, "implementation": {},
         "touched_src": [], "changed_files": []}), encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    # A FIXED remote, so `_repo_slug()` derives a slug from THIS repository and the fake `gh` is
    # keyed on a path that can never coincide with the real one.
    _git(repo, "remote", "add", "origin", f"https://github.com/{_FAKE_SLUG}.git")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold")

    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()
    adr_blob = subprocess.run(["git", "rev-parse", f"HEAD:{_CLI_ADR}"], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()
    body = (FIXTURES / "valid_minimal.md").read_text(encoding="utf-8").replace(
        "| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | fixture-not-resolved |",
        f"| ADR-0105 | {_CLI_ADR} | {adr_blob} |").replace("base_sha=ce132f6", f"base_sha={base_sha}")
    (repo / _CLI_CONTRACT).write_text(body + "| 2026-07-18T09:30:00Z | binding | pr=1 |\n",
                                      encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "land the contract")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _build_fake_gh(repo, base_sha=base_sha)
    return repo


def _cli(monkeypatch, capsys, repo, *extra):
    """Drive the PRODUCTION entry point. `--phase pre` because lifecycle integrity is Stage A and
    holds in every phase; at `head` the fixture would stop at `ST-3` (it carries no approval), which
    would mask the very diagnostic these cases exist to observe."""
    from tools.contract import __main__ as cli
    from tools.contract.adapters import RepoPort
    orig = cli.Ports
    monkeypatch.setattr(cli, "REPO", repo)
    monkeypatch.setattr(cli, "Ports", lambda **kw: orig(repo=RepoPort(repo), **kw))
    _serve_platform(monkeypatch, repo)
    code = cli.main(["verify", _CLI_CONTRACT, "--base", "origin/main", "--phase", "pre", "--json",
                     "--impact-json", str(repo / "impact.json"), *extra])
    return code, json.loads(capsys.readouterr().out)


def _codes(payload):
    return {d["code"] for d in payload["diagnostics"] if d["kind"] != "ok"}


def _life(repo, mutate):
    """Rewrite ONLY the lifecycle DATA ROWS of the working copy, preserving every other byte.

    Non-row lines are carried through untouched so the identity mutation round-trips exactly; an
    earlier version filtered blank lines and thereby tampered with the file it claimed to leave
    alone, turning the clean case red for a reason that had nothing to do with the product.
    """
    p = repo / _CLI_CONTRACT
    decl, _, life = p.read_bytes().partition(parse.BOUNDARY)
    lines = life.decode().splitlines(keepends=True)
    rows = [x for x in lines if x.startswith("| 20")]
    assert len(rows) >= 2, f"the fixture must land >= 2 rows or C/D/E are vacuous (got {len(rows)})"
    other = [x for x in lines if not x.startswith("| 20")]
    p.write_bytes(decl + parse.BOUNDARY + ("".join(other) + "".join(mutate(rows))).encode())
    return parse.parse(p.read_bytes()).digest


def test_cli_A_valid_landed_lifecycle_passes(monkeypatch, capsys, cli_repo):
    """A. The positive control. Without it, every case below could pass by breaking the tool."""
    code, out = _cli(monkeypatch, capsys, cli_repo)
    assert (code, out["decision"], out["rule"]) == (0, "continue", "OK"), out["diagnostics"]


def test_cli_A2_the_ordinary_no_pr_invocation_actually_reads_the_platform(monkeypatch, capsys,
                                                                          cli_repo):
    """A2. `test_cli_A` passing is not proof the read happened — it is also what a SKIPPED read looks
    like, which is the exact defect the governed-PR correction fixed. So assert the call.

    The command carries no `--pr`. If the governed PR stops being resolved from `binding`, the log is
    empty and this goes red while every other CLI case stays green.
    """
    code, out = _cli(monkeypatch, capsys, cli_repo)
    called = (cli_repo / ".fake-gh.log").read_text(encoding="utf-8").strip().splitlines()
    assert (code, out["rule"]) == (0, "OK"), out["diagnostics"]
    assert any(f"repos/{_FAKE_SLUG}/pulls/1" in line for line in called), (
        f"the ordinary no---pr command must resolve the governed PR from `binding` and READ it; "
        f"gh was called with {called}")


@pytest.mark.parametrize("mode", ["unusable", "missing"])
def test_cli_A3_an_unreadable_platform_is_ST_7_never_OK(monkeypatch, capsys, cli_repo, mode):
    """A3. The negative half, and the one that must never be traded away for a green suite.

    Fail-closed: with no usable `gh` the merge facts are UNAVAILABLE, and unavailable is neither
    authorized nor a finding. `ST-7` is the only honest answer — not `OK` (which would be a verdict
    about facts never read), not a crash, and not a fabricated negative like `merged_unverified`.
    """
    from tools.contract import __main__ as cli
    from tools.contract.adapters import RepoPort
    orig = cli.Ports
    monkeypatch.setattr(cli, "REPO", cli_repo)
    monkeypatch.setattr(cli, "Ports", lambda **kw: orig(repo=RepoPort(cli_repo), **kw))
    _serve_platform(monkeypatch, cli_repo, broken=(mode == "unusable"), missing=(mode == "missing"))
    code = cli.main(["verify", _CLI_CONTRACT, "--base", "origin/main", "--phase", "pre", "--json",
                     "--impact-json", str(cli_repo / "impact.json")])
    out = json.loads(capsys.readouterr().out)
    assert out["rule"] == "ST-7", f"{mode}: expected ST-7, got {out['decision']}/{out['rule']}"
    assert code != 0, f"{mode}: an unreadable platform must not exit 0"
    assert "merged_unverified" not in json.dumps(out), (
        f"{mode}: unavailability must not be reported as a disagreement")


@pytest.mark.parametrize("pages,expect", [
    ("two-good", 3),
    ("short", None),
])
def test_the_check_run_read_aggregates_every_page(monkeypatch, tmp_path, cli_repo, pages, expect):
    """A REAL two-page fixture. `--paginate` alone emits one JSON document PER PAGE, so the previous
    single `json.loads` either threw on page two or silently read only page one — and `total_count`
    was then compared against that same short list, so a paginated answer looked complete.

    Two cases, because either alone is passable by a defect: the good one proves pages are actually
    joined, and the short one proves the aggregate is checked rather than trusted.
    """
    from tools.contract import adapters
    sha = "a" * 40
    good = [{"total_count": 3, "check_runs": [_page_run("1"), _page_run("2")]},
            {"total_count": 3, "check_runs": [_page_run("3")]}]
    short = [{"total_count": 3, "check_runs": [_page_run("1"), _page_run("2")]}]
    _build_fake_gh(cli_repo, table={f"repos/{_FAKE_SLUG}/commits/{sha}/check-runs":
                                    good if pages == "two-good" else short})
    _serve_platform(monkeypatch, cli_repo)
    port = adapters.MergeFactsPort(slug=_FAKE_SLUG)
    if expect is None:
        with pytest.raises(adapters.PortError) as exc:
            port.check_runs(sha)
        assert "incomplete" in str(exc.value), str(exc.value)
    else:
        got = port.check_runs(sha)
        assert [r["id"] for r in got] == ["1", "2", "3"], got
        assert len(got) == expect


def _page_run(rid):
    return {"id": rid, "name": _FAKE_CONTEXT, "conclusion": "success", "status": "completed",
            "started_at": "2026-07-19T23:00:00Z", "completed_at": "2026-07-19T23:05:00Z",
            "app": {"id": 15368, "slug": "github-actions"}}


def test_cli_B_monotone_append_passes(monkeypatch, capsys, cli_repo):
    """B. An APPEND is routine record-keeping, not a rewrite (§3.6). A check that could not tell the
    two apart would make the lifecycle unwritable, so this is the case that bounds the fix."""
    before = parse.parse((cli_repo / _CLI_CONTRACT).read_bytes()).digest
    after = _life(cli_repo, lambda r: r + ["| 2026-07-18T10:00:00Z | binding | pr=2 |\n"])
    try:
        assert after == before, "an append must never move `D` (ADR-0105 §3)"
        code, out = _cli(monkeypatch, capsys, cli_repo)
        assert (code, out["rule"]) == (0, "OK"), out["diagnostics"]
    finally:
        _git(cli_repo, "checkout", "--", _CLI_CONTRACT)


@pytest.mark.parametrize("case,mutate", [
    ("C-rewritten", lambda r: r[:-1] + [r[-1].replace("pr=1", "pr=99")]),
    ("D-deleted", lambda r: r[:-1]),
    ("E-reordered", lambda r: list(reversed(r))),
])
def test_cli_CDE_tampered_landed_lifecycle_is_caught(monkeypatch, capsys, cli_repo, case, mutate):
    """C/D/E. Rewrite, delete, reorder — none of which move `D`, because the lifecycle sits OUTSIDE
    the digest by design. Before this contract all three returned `continue`/`OK` with exit 0."""
    before = parse.parse((cli_repo / _CLI_CONTRACT).read_bytes()).digest
    after = _life(cli_repo, mutate)
    try:
        assert after == before, f"{case}: the tamper must leave `D` untouched or it proves nothing"
        code, out = _cli(monkeypatch, capsys, cli_repo)
        assert code != 0, f"{case}: tampering must not exit 0 — {out['decision']}/{out['rule']}"
        assert "LIFECYCLE-REWRITTEN" in _codes(out), f"{case}: got {sorted(_codes(out))}"
        assert out["rule"] == "A5", f"{case}: expected the lifecycle-integrity row, got {out['rule']}"
    finally:
        _git(cli_repo, "checkout", "--", _CLI_CONTRACT)


def test_cli_F_declaration_tampering_is_still_caught(monkeypatch, capsys, cli_repo):
    """F. The pre-existing defence must survive. A declaration edit moves `D`, so it was already
    caught by the digest; now the shipped CLI also names its cause instead of only its symptom."""
    p = cli_repo / _CLI_CONTRACT
    p.write_text(p.read_text(encoding="utf-8").replace("### objective", "### objective\n\nTAMPER.",
                                                       1), encoding="utf-8")
    try:
        code, out = _cli(monkeypatch, capsys, cli_repo)
        assert code != 0, out
        assert "DECL-DIVERGED" in _codes(out), sorted(_codes(out))
    finally:
        _git(cli_repo, "checkout", "--", _CLI_CONTRACT)


def test_cli_G_explicit_head_still_evaluates_the_committed_blob(monkeypatch, capsys, cli_repo):
    """G. `--head` semantics are UNCHANGED. It evaluates the blob at that ref, so a working-tree-only
    tamper is correctly invisible to it — ADR-0105 §11.1: a gate reads the blob.

    This is the case that keeps the fix honest. It would have been easy to turn everything green by
    quietly pointing `--head` at the working tree, and that would have broken the artifact contract
    while every other assertion here still passed.
    """
    _life(cli_repo, lambda r: r[:-1])
    try:
        code, out = _cli(monkeypatch, capsys, cli_repo, "--head", "HEAD")
        assert (code, out["rule"]) == (0, "OK"), out["diagnostics"]
    finally:
        _git(cli_repo, "checkout", "--", _CLI_CONTRACT)


def test_cli_H_unresolvable_origin_main_fails_closed(monkeypatch, capsys, cli_repo):
    """H. `origin/main` gone means we cannot know whether the contract landed, so we cannot know
    whether its history was rewritten. That is UNVERIFIABLE, and ADR-0105 §10 does not read
    unverifiable as satisfied. This forbids the exact failure that caused the original defect: an
    absent input quietly standing in for a passed check."""
    _git(cli_repo, "update-ref", "-d", "refs/remotes/origin/main")
    try:
        code, out = _cli(monkeypatch, capsys, cli_repo)
        assert code != 0, f"an unreadable landed copy must not exit 0 — {out['decision']}"
        assert "UNVERIFIABLE" in _codes(out), sorted(_codes(out))
        assert out["rule"] == "ST-7", out["rule"]
    finally:
        _git(cli_repo, "update-ref", "refs/remotes/origin/main", "HEAD")
