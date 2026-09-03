"""ARCH-008 / IMPL-007 ratchet enforcement."""
from __future__ import annotations

import re

from ..common import ARCH, CONTRACT, DERIVED, GOVERNANCE, KB, REPO, load
from .rules import Finding, _f

_HISTORY = frozenset({"CYCLE6_CORRECTIONS.md", "IMPLEMENTATION_READINESS.md", "C6-S08.md", "C6-S09.md"})

# ── ARCH-008 side-effect ratchet (GB-6 shape: ceiling + allowlist vs derived census) ────────
#
# Ceiling keys match derived/side_effects.json → totals. Section keys are the per-module maps.
# The ceiling is DECLARED POLICY (like governance/unknowns.json `approved_ceiling`); the census
# stays in derived/. Do not put per-site counts in the declared file — those are AST-computable
# and belong only in derived/.
_SIDE_EFFECT_RATCHET_KEYS = (
    ("subprocess_sites", "subprocess"),
    ("network_sites_literal_requests", "network"),
    ("ledger_transaction_sites", "ledger_transaction"),
    ("mkdtemp_sites", "mkdtemp"),
    ("rmtree_sites", "rmtree"),
    ("env_write_sites", "env_writes"),
)


def _side_effect_ratchet(se: dict) -> list[Finding]:
    """ARCH-008: derived census totals may not exceed the declared ceiling; every module that
    carries a site must be listed. Raising the ceiling without listing a new module (or listing
    without raising) still fails — both halves of the human decision must be visible in the diff.
    """
    path = GOVERNANCE / "side_effect_ratchet.json"
    # No `.exists()` guard: GOV-001 short-circuits on a missing required artifact first.
    doc = load(path)
    ceilings = doc.get("ceilings") or {}
    approved = doc.get("approved_sites") or {}
    totals = se.get("totals") or {}
    out: list[Finding] = []

    missing_ceilings = [k for k, _ in _SIDE_EFFECT_RATCHET_KEYS if k not in ceilings]
    if missing_ceilings:
        out.append(_f("ARCH-008",
                      f"{len(missing_ceilings)} side-effect ceiling(s) missing from "
                      f"governance/side_effect_ratchet.json. A key without a ceiling is unenforced.",
                      [f"missing ceiling: {k}" for k in missing_ceilings]))
        return out

    for key, section in _SIDE_EFFECT_RATCHET_KEYS:
        ceiling = ceilings[key]
        if not isinstance(ceiling, int) or isinstance(ceiling, bool):
            out.append(_f("ARCH-008",
                          f"Ceiling for {key} must be a structured integer, not prose.",
                          [f"{key}: {ceiling!r}"]))
            continue
        actual = int(totals.get(key, 0))
        modules = set((se.get(section) or {}).keys())
        allowed_raw = approved.get(key, [])
        allowed: set[str] = set()
        for entry in allowed_raw:
            if isinstance(entry, str):
                allowed.add(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("module"), str):
                allowed.add(entry["module"])
        undeclared = sorted(modules - allowed)
        if actual <= ceiling and not undeclared:
            continue
        evidence = [f"ceiling: {ceiling}", f"census: {actual}"]
        if undeclared:
            evidence += [f"undeclared module: {m}" for m in undeclared]
        else:
            # Over ceiling inside already-listed modules — name every module so the PR can see where.
            evidence += [f"module: {m}" for m in sorted(modules)]
        out.append(_f("ARCH-008",
                      f"Side-effect census for {key} is {actual}, above the approved ceiling of "
                      f"{ceiling}." if actual > ceiling else
                      f"{len(undeclared)} undeclared {key} module(s) — list each new module in "
                      f"governance/side_effect_ratchet.json when raising the ceiling.",
                      evidence))
    return out


# ── ARCH-008 / ARCH-009: derived facts may not be restated in a DECLARED artifact ───────────
#
# (rule id, kb file, block, human label). The blocks a hand-authored artifact used to mirror a
# derived census in. There is deliberately NO table of which numbers to check: whatever number the
# block contains is the violation, so the rule's coverage is the block's contents and cannot be
# narrowed by editing the block. What this replaced compared seven hand-paired keys under an
# `if declared_key in block` guard — five of kb/dependencies.json's twelve totals were never paired
# at all (two of those five had silently rotted), and deleting a paired key deleted its enforcement
# while the gate stayed green. A rule that stops firing is indistinguishable from one that passes.
#
# ARCH-008 ALSO runs `_side_effect_ratchet` above. The restatement forbid below is the half that
# keeps a derived number out of kb/; the ratchet is the half that fails a new site.
_FORBIDDEN_RESTATEMENT_BLOCKS = (
    ("ARCH-009", "dependencies.json", "totals", "kb/dependencies.json `totals`"),
    ("ARCH-009", "subsystems.json", "totality", "kb/subsystems.json `totality`"),
    ("ARCH-008", "side_effects.json", "counts_AST_verified",
     "kb/side_effects.json `counts_AST_verified`"),
)

# Keys whose CONTENT is a derived number narrated as prose, which the numeric test above cannot see.
# `updated_at_HEAD` was a single-line append-only changelog of graph deltas: every graph-touching PR
# appended a sentence to the same line, so it conflicted on every one of them and the resolution was
# semantic. `what_did_NOT_move` asserted a set of counts had held — and had already rotted uncaught
# ("lateral stays 51" against a derived 46). Git history is the changelog and cannot conflict with
# itself. An EXPLICITLY NAMED list, never a wildcard: a name added here only widens this rule, so
# unlike the pair table it replaced it has no failure mode where the rule quietly checks less.
_FORBIDDEN_DELTA_KEYS = frozenset({"updated_at_HEAD", "what_did_NOT_move"})


def _restated_facts(node: object, path: str) -> list[tuple[str, str]]:
    """Every place `node` restates a derived fact, as (dotted path, what it is)."""
    if isinstance(node, dict):
        out: list[tuple[str, str]] = []
        for k, v in node.items():
            here = f"{path}.{k}"
            if k in _FORBIDDEN_DELTA_KEYS:
                out.append((here, "a prose changelog of derived deltas"))
            else:
                out += _restated_facts(v, here)
        return out
    if isinstance(node, list):
        return [hit for i, v in enumerate(node) for hit in _restated_facts(v, f"{path}[{i}]")]
    if isinstance(node, bool):
        return []                                 # a JSON literal, not a census
    if isinstance(node, (int, float)):
        return [(path, f"a derived number ({node})")]
    return []


def _declared_numbers_forbidden(rule_id: str) -> list[Finding]:
    """ARCH-008 / ARCH-009: a DECLARED artifact may not restate a number the regen derives.

    The predicate is PRESENCE, not agreement. A comparison keeps the copy alive — it has to, or it
    has nothing to compare — and a live copy conflicts on every PR that moves the graph and rots
    between conflicts. Absence has neither failure mode, and it cannot be disarmed by deleting the
    field it guards, because the field's absence IS the passing state.

    No `.exists()` guard: GOV-001 evaluates first and short-circuits on a missing canonical
    artifact, so a skip here could only re-introduce the vacuous pass this whole rule set exists to
    make impossible.
    """
    out: list[Finding] = []
    for rid, fname, block, label in _FORBIDDEN_RESTATEMENT_BLOCKS:
        if rid != rule_id:
            continue
        bad = _restated_facts(load(KB / fname).get(block, {}), block)
        if bad:
            out.append(_f(rid,
                          f"{len(bad)} derived fact(s) restated in {label}. A number copied out of "
                          f"the code is wrong the moment the code moves, and it conflicts on every "
                          f"PR that moves it. The derived twin does neither.",
                          [f"{where}: {what}" for where, what in sorted(bad)]))
    return out


def _ratchet_drift(rat: dict) -> list[Finding]:
    out: list[Finding] = []
    ic = CONTRACT / "implementation_contract.json"
    if not ic.exists():
        return out
    gb6 = load(ic).get("GLOBAL_BOUNDARIES", {}).get("GB-6_ast_ratchet_budgets", {})
    declared_cli = rat["declared_by_the_ci_tests"]["print"].get("cli_print_count")

    # *** READ THIS BEFORE YOU "SIMPLIFY" IT. ***
    # The first version of this parser split the sentence on '=' and took the first token where
    # `.isdigit()` was true. In the real contract the number is written as a `_CLI_PRINT_COUNT`
    # assignment INSIDE BACKTICKS — so the token carried a trailing backtick, `.isdigit()` was False,
    # and the parser extracted NOTHING. The rule silently no-opped. It would NEVER have caught the
    # stale-copy drift that motivated this entire cycle; that was found by hand.
    #
    # A negative control (NC-15) is the only reason anybody knows. This is `AR-03` — "a check whose
    # name promises what its assertion does not deliver" — occurring INSIDE THE GOVERNANCE SYSTEM.
    # The root cause is that the number lives in PROSE. The regex is the patch; the fix is the
    # migration (store ratchet budgets as structured fields, never as a sentence).
    pr = str(gb6.get("print_ratchet", {}).get("mechanism_B", ""))
    m = re.search(r"_CLI_PRINT_COUNT\s*=\s*(\d+)", pr)
    contract_num = int(m.group(1)) if m else None
    if contract_num is None and pr:
        out.append(_f("IMPL-007",
                      "The contract's print-ratchet budget could not be PARSED at all. A budget "
                      "nobody can read is a budget nobody enforces — and a rule that silently "
                      "extracts nothing reports success. Store it as a structured field.",
                      [f"unparseable: {pr[:90]}"]))

    # *** EVERY LIVE COPY, not just the one this rule happened to know about. ***
    #
    # The rule originally read ONLY contract/implementation_contract.json. The number turned out to
    # exist in NINE places across the KB, holding FOUR DIFFERENT VALUES — and the two worst were
    # `contract/prompts/C6-S08.md` and `C6-S09.md`, the LIVE IMPLEMENTATION PROMPTS handed to whoever
    # builds those slices. They pinned a `_CLI_PRINT_COUNT` assignment to a now-stale value and told the
    # implementer NOT to change the count. An implementer obeying that prompt writes the wrong constant
    # and CI goes red for a reason unrelated to their change — the precise failure GB-6/IR-4 prevents.
    # A rule named "the ratchet budgets the contract COPIES must match the tests that ENFORCE them"
    # was reporting green throughout. Checking ONE copy of a duplicated number is not enforcement;
    # it is a rule scoped to the place its author happened to remember.
    #
    # THE ASSIGNMENT FORM IS A LIVE CLAIM. `_CLI_PRINT_COUNT = N` anywhere in a declared artifact
    # asserts a current fact and is held to the test. Prose *about* the past ("the contract once
    # pinned it at one four seven") is narrative and is not a claim — write history as prose.
    #
    # _HISTORY is excluded because a corrections record MUST keep saying what was true when it was
    # written; retroactively editing an erratum to match today destroys the only account of what
    # went wrong. It is a small, named list, not a wildcard, so it cannot become a loophole.
    #
    # G1: the scan was `.reports/architecture/`-ONLY, so the engine COULD NOT SEE ITSELF — a stale
    # `_CLI_PRINT_COUNT = <n>` assignment in tools/arch/'s own rationales, or in docs/, went unwatched
    # (the very thing the rule checks for, in the file that does the checking). Widened to tools/arch/
    # and docs/, and to .py, so every live copy is held to the test. `selftest.py` is EXCLUDED because
    # it INJECTS a deliberately-wrong assignment as the NC-15 fixture — scanning it would fire the rule
    # on the negative control's own payload. docs/ is scanned too: if any doc states a stale copy of
    # the budget, this rule catches it.
    _scan_exclude = _HISTORY | {"selftest.py"}
    _scanned: set = set()
    for root in (ARCH, REPO / "tools" / "arch", REPO / "docs"):
        if not root.exists():
            continue                                  # a fixture may copy only a subset of the roots
        for path in sorted(root.rglob("*")):
            if path in _scanned or path.suffix not in (".json", ".md", ".py") or not path.is_file():
                continue
            _scanned.add(path)
            if path.name in _scan_exclude or DERIVED in path.parents:
                continue
            try:
                blob = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for found in sorted({int(x) for x in re.findall(r"_CLI_PRINT_COUNT\s*=\s*(\d+)", blob)}):
                if declared_cli is None or found == declared_cli:
                    continue
                out.append(_f("IMPL-007",
                              "A copy of the cli.py print budget is STALE. It is pinned as a "
                              "load-bearing, exact-equality budget shared by three slices (GB-6 / IR-4) "
                              "— a wrong value makes the boundary unenforceable and would fail a slice "
                              "for a reason unrelated to its change.",
                              [f"{path.relative_to(REPO).as_posix()} says _CLI_PRINT_COUNT = {found}",
                               f"tests/test_internal_prints_routed.py says {declared_cli}",
                               f"measured in src/fanops/cli.py: {declared_cli}",
                               "the TEST is authoritative; the declared copy rotted"]))

    # the per-file swallow ceilings the contract restates
    ceilings = gb6.get("swallow_ratchet", {}).get("budget_ceiling__must_not_exceed", {})
    base = rat["declared_by_the_ci_tests"]["swallow"].get("baseline", {})
    bad = [(f, spec.get("baseline"), base.get(f))
           for f, spec in sorted(ceilings.items())
           if isinstance(spec, dict) and f in base and spec.get("baseline") != base[f]]
    if bad:
        out.append(_f("IMPL-007",
                      "The contract's swallow ceilings disagree with the test that enforces them.",
                      [f"{f}: contract {a}, test {b}" for f, a, b in bad]))
    return out
