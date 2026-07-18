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

import json
import re
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
    gates = model.Gates(content_approval="satisfied", exact_head_approval="satisfied")
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
    from tools.contract import lifecycle
    d = parse.parse(selftest.build())
    st = lifecycle.state(d, d.events, model.Gates(), merged=True, ci_green=False,
                         proposal_bound=False, pr_open=False, mandatory_ok=True)
    assert st == "merged"


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


def test_the_bootstrap_contract_declares_this_exact_patch():
    """AC-28 / ADR-0105 §5.3. The declared surfaces must be the surfaces, with nothing extra."""
    declared = {r["path"] for r in parse.parse(BOOTSTRAP.read_bytes()).value("expected_surfaces")}
    r = subprocess.run(["git", "diff", "--name-only", "origin/main...HEAD"], cwd=_ROOT,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        pytest.skip("origin/main is not resolvable in this checkout")
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

    # THE BASE IS THE ONE THE CONTRACT RECORDS, NOT `origin/main`. A contract governs the change
    # from its own `created.base_sha`; `origin/main` only coincided with that while the change was
    # un-landed. Once it merged, `origin/main...HEAD` became a one-file MONOTONE lifecycle append —
    # which §3.6 deliberately keeps OUTSIDE `T3` — so the derived trait set was empty, the declared
    # one was `{governance}`, and the verifier correctly answered `CL-2`. Second time this test has
    # assumed the world would hold still (see the timestamp note below); reading the base off the
    # artifact makes the assertion true in every phase of the contract's life instead of one.
    base_ref = next((e.get("base_sha") for e in parsed.events
                     if e.kind == "created" and e.get("base_sha")), "origin/main")
    if real.resolve(base_ref) is None:
        pytest.skip(f"the contract's recorded base {base_ref[:12]} is not in this checkout")

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

    class _Approved:
        def __init__(self, i): self.i = i
        def blob(self, r, q):
            b = self.i.blob(r, q)
            return approved if (q == p and b is not None and r != "origin/main") else b
        def blob_sha(self, r, q): return self.i.blob_sha(r, q)
        def diff_names(self, b, h): return self.i.diff_names(b, h)
        def contains(self, r, q): return self.i.contains(r, q)
        def resolve(self, r): return self.i.resolve(r)
        def is_ancestor(self, a, b): return self.i.is_ancestor(a, b)

    ports = Ports(repo=_Approved(real))
    got = {}
    for phase in ("pre-implementation", "at-head", "merge-gate"):
        dec, _ = run_pipeline(ports, p, base=base_ref, head="HEAD", pr=None, phase=phase)
        got[phase] = (dec.outcome, dec.rule)

    assert got["pre-implementation"] == ("continue", "OK"), got
    assert got["at-head"] == ("continue", "OK"), (
        f"AC-21: with approval granted the contract must reach `continue` at its own head; got "
        f"{got['at-head']}. If this is `ST-8`, GS-2 has regressed to the over-broad form.")
    # The merge gate is a SEPARATE grant and must not fall out of the content approval. With no
    # `merge_approved` on record, NEITHER §4.1a route has evidence, so `ST-4` is the honest verdict —
    # the amendment made the gate reachable, not automatic.
    if not any(e.kind == "merge_approved" for e in parse.parse(approved).events):
        assert got["merge-gate"][1] == "ST-4", (
            f"with no merge approval recorded the gate must still stop; got {got['merge-gate']}")


# ── ADR-0105 §4.1a — parent-binding and the two evidence routes ─────────────────────────────
#
# The original exact-head gate was UNSATISFIABLE here: it admitted only a non-author `APPROVED` PR
# review, `Fleezyflo` is the sole account with push access AND the author of every PR, and GitHub
# refuses self-approval. The same self-reference (a record cannot name the commit computed over it)
# also made `head_proposed`, and therefore the `implemented` state, unreachable in EVERY repository.
# These tests hold the amended gate to both halves of its claim: reachable where no second principal
# can exist, and untouched everywhere else.
_P = "docs/contracts/CC-2026-07-18-change-contract-compiler.md"
_PARENT, _HEAD = "a" * 40, "b" * 40
_APPROVAL = f"| 2026-07-19T10:00:00Z | merge_approved | parent_sha={_PARENT}; operator=solo |\n"


class _Repo:
    """Exactly enough git to answer the four §4.1a checks, so each one can be failed in isolation."""

    def __init__(self, parent_raw, head_raw, changed=(_P,), ancestor=True):
        self.parent_raw, self.head_raw = parent_raw, head_raw
        self.changed, self.ancestor = list(changed), ancestor

    def blob(self, ref, path): return self.parent_raw if ref == _PARENT else self.head_raw

    def diff_names(self, base, head): return sorted(self.changed)

    def is_ancestor(self, a, b): return self.ancestor


def _appended(parent: bytes, row: str = _APPROVAL) -> bytes:
    return parent + row.encode()


def _gate(parent, head, *, principals, reviews=(), changed=(_P,), ancestor=True):
    from tools.contract import lifecycle
    d = parse.parse(head)
    return lifecycle.gates(d, d.events, head_sha=_HEAD, pr=1, reviews=list(reviews),
                           main_has_contract=False, repo=_Repo(parent, head, changed, ancestor),
                           path=_P, raw=head, principals=principals)


def test_single_principal_repositories_can_reach_the_exact_head_gate():
    """DELIVERABLE 9. The gate the original model could never satisfy here binds — and discloses how.

    `satisfied` alone would not be enough. An approval that carries no second principal's judgement
    and does not SAY SO is the bypass this amendment exists not to be.
    """
    parent = selftest.build()
    g = _gate(parent, _appended(parent), principals=["solo"])
    assert g.exact_head_approval == "satisfied", g.detail
    assert g.exact_head_evidence == "unwitnessed"
    assert any("UNWITNESSED" in x for x in g.detail), g.detail


def test_multi_principal_repositories_keep_the_original_guarantee_exactly():
    """DELIVERABLE 8. Two principals ⇒ the in-file route is UNREACHABLE, however well-formed it is.

    This is the whole anti-bypass argument in one assertion: a team cannot opt out of peer review by
    writing a line, because admissibility is read from the platform and nothing in the tree can
    reach it.
    """
    parent = selftest.build()
    g = _gate(parent, _appended(parent), principals=["alice", "bob"])
    assert g.exact_head_approval != "satisfied", g.detail
    assert g.exact_head_evidence == ""
    assert any("INADMISSIBLE" in x for x in g.detail), g.detail


def test_a_witnessed_review_still_satisfies_the_gate_and_outranks_the_in_file_route():
    parent = selftest.build()
    head = _appended(parent)
    for principals in (["alice", "bob"], ["solo"]):
        g = _gate(parent, head, principals=principals, reviews=[(_HEAD, "APPROVED")])
        assert (g.exact_head_approval, g.exact_head_evidence) == ("satisfied", "witnessed"), (
            f"a real review must win outright for principals={principals}: {g.detail}")


def test_an_unreadable_principal_set_leaves_the_gate_unknown_not_satisfied():
    """Fail closed. `gh` being down must never be the reason a merge becomes authorized."""
    parent = selftest.build()
    g = _gate(parent, _appended(parent), principals=None)
    assert g.exact_head_approval == "unknown", g.detail


@pytest.mark.parametrize("mutate,why", [
    (dict(changed=(_P, "src/fanops/publish.py")), "code rode in behind the approval"),
    (dict(ancestor=False), "the approved commit is not an ancestor of the head"),
])
def test_parent_binding_rejects_a_head_that_moved_for_any_other_reason(mutate, why):
    parent = selftest.build()
    g = _gate(parent, _appended(parent), principals=["solo"], **mutate)
    assert g.exact_head_approval != "satisfied", f"{why}: {g.detail}"


def test_parent_binding_rejects_a_declaration_edited_inside_the_one_permitted_path():
    """The check the path-level test CANNOT do. Without it the delta proof has a hole exactly the
    size of the contract file, which is the one file the approval permits to move."""
    parent = selftest.build()
    head = _appended(selftest.build(decl_mutate=lambda d: d.replace("Prove the", "Proved the", 1)))
    g = _gate(parent, head, principals=["solo"])
    assert g.exact_head_approval != "satisfied", g.detail
    assert any("declaration changed" in x for x in g.detail), g.detail


def test_parent_binding_rejects_a_rewritten_lifecycle():
    parent = selftest.build(extra="| 2026-07-18T11:00:00Z | binding | pr=1 |\n")
    g = _gate(parent, _appended(selftest.build()), principals=["solo"])
    assert g.exact_head_approval != "satisfied", g.detail


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
    (repo / ".agents" / "lanes.json").write_text("{}", encoding="utf-8")
    (repo / _CLI_ADR).write_text("# ADR-0105 (fixture)\n", encoding="utf-8")
    (repo / "impact.json").write_text(json.dumps(
        {"classification": "COMPATIBLE_CHANGE", "architecture": {}, "implementation": {},
         "touched_src": [], "changed_files": []}), encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold")

    adr_blob = subprocess.run(["git", "rev-parse", f"HEAD:{_CLI_ADR}"], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()
    body = (FIXTURES / "valid_minimal.md").read_text(encoding="utf-8").replace(
        "| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | fixture-not-resolved |",
        f"| ADR-0105 | {_CLI_ADR} | {adr_blob} |")
    (repo / _CLI_CONTRACT).write_text(body + "| 2026-07-18T09:30:00Z | binding | pr=1 |\n",
                                      encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "land the contract")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
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
