"""The seven validation families. ALL RUN; NONE SHORT-CIRCUITS; diagnostics are data, not control flow.

Collecting everything before deciding is what makes one round-trip enough. A validator that stopped
at the first problem would make an author fix one field, re-run, find the next, and re-run again —
and a governance tool that is tedious to satisfy is a governance tool people route around.

The families are separated by WHO FIXES THE PROBLEM, not by what code is convenient:

    V-schema      the author, in the contract's own bytes
    V-semantic    the author, in what the contract SAYS
    V-authority   the author, or an operator re-confirming a moved authority
    V-lifecycle   nobody — a rewritten history is governance-sensitive (§3.6)
    V-scope       the author, by amending the declaration or reverting a file
    V-evidence    whoever proved the claim, by re-proving it
    V-dependency  nobody: the input was unavailable and no verdict was reached

V-dependency is separate on purpose. It prevents the worst failure a governance tool can have —
reporting `continue` because a check silently did not run. Everything else says "this is wrong"; only
V-dependency says "I could not tell", and those two must never arrive wearing the same face.
"""
from __future__ import annotations

import re

from .classify import GENERATED, UNAUTHORIZED, any_match
from .model import (ACTIONS, APPROVAL_FIELDS, EMPTY_ALLOWED_FIELDS, MALFORMED, MANDATORY_FIELDS,
                    MISSING, SCOPE_BASES, SEMANTIC, SURFACE_KINDS, TABLE_FIELDS,
                    TRAIT_CONDITIONAL_FIELDS, TRAITS, Diagnostic)

_ID = re.compile(r"^CC-\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+){0,5}$")
_PRINT_BUDGET = re.compile(r"_CLI_PRINT_COUNT\s*=\s*\d+")

# ADR-0105 §7: "Prohibited additions default to all." These are the classes; a path in one of them
# must carry an explicit justification, and one that is not declared at all is already `unauthorized`.
_PROHIBITED_ADDITION_CLASSES = (
    ("a dependency", ("requirements/**", "pyproject.toml")),
    ("a required check", (".github/ci-control-registry.yml", ".github/workflows/**")),
    ("a registry", (".reports/architecture/kb/**", ".reports/architecture/governance/**")),
)

# A `success_condition` must name at least ONE OBSERVABLE — something a third party could go and
# look at. This is deliberately a weak, mechanical predicate: falsifiability in general is a
# judgement, and a checker that pretended otherwise would reject good conditions and accept bad ones
# with equal confidence. What it DOES catch is the real failure mode — a condition phrased entirely
# as intent ("the system works better") with nothing anyone could check.
_OBSERVABLE = re.compile(r"`[^`]+`|\bexit\s+\d|\b\d+\b|\b(?:green|red|passes|fails|equals|byte-"
                         r"identical|reproduces)\b", re.I)


def v_schema(decl, filename_stem: str, in_contracts_dir: bool = True):
    out = list(decl.diagnostics)
    for name in MANDATORY_FIELDS:
        if not decl.present(name):
            out.append(Diagnostic(MISSING, "FIELD-MISSING", f"mandatory field `{name}` is absent",
                                  expected=name))
        elif not decl.value(name) and name not in EMPTY_ALLOWED_FIELDS:
            out.append(Diagnostic(MISSING, "FIELD-EMPTY", f"mandatory field `{name}` is empty",
                                  expected=f"a non-empty {name}"))
    cid = decl.id
    if cid and not _ID.match(cid):
        out.append(Diagnostic(MALFORMED, "ID-FORMAT", f"{cid!r} is not `CC-YYYY-MM-DD-<slug>` with a "
                                                      f"1–6 word lowercase kebab slug",
                              got=cid, expected="CC-2026-07-18-example-slug"))
    # ADR-0105 §6 scopes this precisely: *"the id is the filename stem IN `docs/contracts/`"* — the
    # filesystem there is the uniqueness check, which is why no registry is needed. A contract read
    # from anywhere else (a test fixture, a scratch copy) is data, not a directory entry competing
    # for an id, so applying the rule to it would reject valid documents for a reason the ADR does
    # not state.
    if cid and filename_stem and in_contracts_dir and cid != filename_stem:
        out.append(Diagnostic(MALFORMED, "ID-FILENAME",
                              f"`id` is {cid!r} but the filename stem is {filename_stem!r}",
                              got=cid, expected=filename_stem,
                              remediation="uniqueness IS filename uniqueness (ADR-0105 §6); no "
                                          "registry exists to reconcile a mismatch"))
    for name, allowed in (("traits", TRAITS), ("authorized_actions", ACTIONS)):
        for v in (decl.value(name) or []):
            if v not in allowed:
                out.append(Diagnostic(MALFORMED, "ENUM", f"`{name}` value {v!r} is not permitted",
                                      got=v, expected=", ".join(allowed)))
    for row in (decl.value("expected_surfaces") or []):
        if row.get("kind") not in SURFACE_KINDS:
            out.append(Diagnostic(MALFORMED, "ENUM", f"`expected_surfaces.kind` {row.get('kind')!r} "
                                                     f"is not permitted", got=str(row.get("kind")),
                                  expected=", ".join(SURFACE_KINDS), path=row.get("path", "")))
    for row in (decl.value("allowed_scope") or []):
        if row.get("basis") not in SCOPE_BASES:
            out.append(Diagnostic(MALFORMED, "ENUM", f"`allowed_scope.basis` {row.get('basis')!r} "
                                                     f"is not permitted", got=str(row.get("basis")),
                                  expected=", ".join(SCOPE_BASES)))
    out += _approval_shape(decl)
    return out


def _approval_shape(decl):
    """The two ways the ADR-0106 approval fields can be wrong. Both are `A5`, not formatting.

    ONE CONTRACT, ONE APPROVAL ROUTE. `lifecycle.gates` selects the route from `boundary_count`, so
    a lifecycle-bearing contract carrying `approved_digest` has an approval record that is never
    read. An inert authorization record is worse than an absent one: it looks like approval in the
    diff and decides nothing, which is exactly the shape a reader would trust and a rule would miss.

    A DIGEST WITHOUT A TOKEN IS NOT AN OPERATOR ACT. The `approved` event it replaces required both
    (§4.2) — the digest says WHAT was approved and the token is the operator's own words saying it
    was. Either alone records half of an authorization, so the pair is required together or not at
    all; neither is required on its own, because a contract exists before it is approved.
    """
    out: list[Diagnostic] = []
    present = [f for f in APPROVAL_FIELDS if decl.value(f)]
    if decl.boundary_count and present:
        out.append(Diagnostic(MALFORMED, "APPROVAL-DUAL-ROUTE",
                              f"this contract carries a `## Lifecycle` section AND the "
                              f"declaration-only approval field(s) {', '.join(present)} — the "
                              f"lifecycle route is the one that is read, so these decide nothing",
                              got=", ".join(present), expected="one route, not two",
                              remediation="a lifecycle-bearing contract records approval as an "
                                          "`approved` event (ADR-0105 §4.2); a declaration-only one "
                                          "records it in front matter (ADR-0106) — never both"))
    digest_v, token_v = decl.value("approved_digest"), decl.value("approval_token")
    if bool(digest_v) != bool(token_v):
        got = "approved_digest" if digest_v else "approval_token"
        out.append(Diagnostic(MALFORMED, "APPROVAL-INCOMPLETE",
                              f"`{got}` is present without its pair — an approval names both WHAT "
                              f"was approved and the operator words that approved it",
                              got=got, expected="approved_digest and approval_token together",
                              remediation="record both, or neither until the operator has answered"))
    return out


def v_semantic(decl, generated_paths):
    out: list[Diagnostic] = []
    allowed = [r.get("glob", "") for r in (decl.value("allowed_scope") or [])]
    prohibited = [r.get("glob", "") for r in (decl.value("prohibited_scope") or [])]
    surfaces = [r.get("path", "") for r in (decl.value("expected_surfaces") or [])]

    for p in surfaces:
        if p and not any_match(p, allowed):
            out.append(Diagnostic(SEMANTIC, "SURFACE-OUTSIDE-SCOPE",
                                  f"`{p}` is an expected surface but no `allowed_scope` glob "
                                  f"admits it", path=p, expected="a covering glob"))
    for g in allowed:
        for q in prohibited:
            if g and g == q:
                out.append(Diagnostic(SEMANTIC, "SCOPE-CONTRADICTION",
                                      f"`{g}` is both allowed and prohibited", got=g))
    for g in allowed:
        if g in generated_paths or (g and any_match(g, generated_paths)):
            out.append(Diagnostic(SEMANTIC, "GENERATED-IN-SCOPE",
                                  f"`{g}` is a GENERATED artifact and may never be in "
                                  f"`allowed_scope` (ADR-0105 §7)", got=g,
                                  remediation="a generated file is a `generated-consequence`, "
                                              "produced by regeneration; naming it in scope invites "
                                              "the hand-edit `LAW-DOC-01` forbids"))
    for field, trait in TRAIT_CONDITIONAL_FIELDS.items():
        if trait in decl.traits and not decl.value(field):
            out.append(Diagnostic(MISSING, "TRAIT-CONDITIONAL",
                                  f"`{field}` is mandatory when the `{trait}` trait is set",
                                  expected=field))

    sc = decl.value("success_condition") or ""
    if sc and not _OBSERVABLE.search(str(sc)):
        out.append(Diagnostic(SEMANTIC, "UNFALSIFIABLE",
                              "`success_condition` names nothing observable — no command, path, "
                              "control id, count or outcome anyone could go and check",
                              got=str(sc)[:80],
                              remediation="state what would be TRUE if it worked and FALSE if it "
                                          "did not, in terms someone else can evaluate"))

    for name in ("objective", "success_condition", "rollback"):
        body = str(decl.value(name) or "")
        if _PRINT_BUDGET.search(body):
            out.append(Diagnostic(SEMANTIC, "PRINT-BUDGET-COPY",
                                  f"`{name}` carries a `_CLI_PRINT_COUNT = <n>` assignment, which "
                                  f"`IMPL-007` reads as a LIVE CLAIM (ADR-0105 §11.3)",
                                  remediation="describe the budget without writing the assignment "
                                              "form; a stale copy turns the architecture gate red"))

    declared = {r.get("path", "") for r in (decl.value("expected_surfaces") or [])}
    for what, patterns in _PROHIBITED_ADDITION_CLASSES:
        for row in (decl.value("expected_surfaces") or []):
            p = row.get("path", "")
            if p in declared and any_match(p, patterns) and not row.get("why", "").strip():
                out.append(Diagnostic(SEMANTIC, "UNJUSTIFIED-ADDITION",
                                      f"`{p}` adds {what}, which ADR-0105 §7 prohibits by default "
                                      f"unless explicitly allowed — it carries no justification",
                                      path=p, expected="a non-empty `why`"))

    for name in TABLE_FIELDS:
        rows = decl.value(name)
        if rows is not None and not isinstance(rows, list):
            out.append(Diagnostic(MALFORMED, "TABLE-SHAPE", f"`{name}` did not parse as a table",
                                  got=type(rows).__name__))
    return out


def v_authority(problems):
    out = []
    for code, cid, detail in problems:
        kind = MISSING if code in ("AUTH-MISSING-FILE", "AUTH-UNKNOWN") else MALFORMED
        out.append(Diagnostic(kind, code, detail, got=cid,
                              remediation="a moved authority blob FLAGS for re-confirmation and "
                                          "does not auto-void (ADR-0105 §4.4)"
                                          if code == "AUTH-BLOB-MOVED" else ""))
    return out


def v_scope(labelled, *, owners_declared, owners_derived, stale_artifacts):
    """§5.3 labels, the per-owner justification, and regeneration-reproducibility."""
    out: list[Diagnostic] = []
    for p, lab in labelled:
        if lab == UNAUTHORIZED:
            out.append(Diagnostic(SEMANTIC, "SCOPE-UNAUTHORIZED",
                                  f"`{p}` is in the diff but is neither declared, nor a generated "
                                  f"consequence, nor on the incidental allowlist", path=p,
                                  remediation="amend the declaration and re-approve, or revert the "
                                              "file — ADR-0105 §5.3"))
    generated_changed = [p for p, lab in labelled if lab == GENERATED]
    for art in stale_artifacts:
        if generated_changed:
            out.append(Diagnostic(SEMANTIC, "GENERATED-NOT-REPRODUCIBLE",
                                  f"`{art}` does not match regeneration from this tree — a "
                                  f"generated consequence is allowed WITHOUT re-approval only when "
                                  f"regeneration produced it", path=art,
                                  remediation="run the regeneration command; a hand-edited "
                                              "generated file is ADR-0102 §4"))
    missing_why = [r for r in owners_declared if not r.get("why_touched", "").strip()]
    if len(owners_declared) > 1:
        for r in missing_why:
            out.append(Diagnostic(MISSING, "OWNER-UNJUSTIFIED",
                                  f"subsystem `{r.get('subsystem_id')}` is an additional owner with "
                                  f"no reason recorded — this is what stops the drive-by edit",
                                  expected="a non-empty `why_touched`"))
    declared_ids = {r.get("subsystem_id", "") for r in owners_declared}
    for sid in owners_derived:
        if sid not in declared_ids:
            out.append(Diagnostic(MISSING, "OWNER-UNDECLARED",
                                  f"the diff touches subsystem `{sid}`, which `owners` does not "
                                  f"name", got=sid, expected=sid))
    for sid in declared_ids:
        if sid and not re.match(r"^S\d{2}_[a-z_]+$", sid):
            out.append(Diagnostic(MALFORMED, "OWNER-FORM",
                                  f"`{sid}` is not a full-form subsystem id — slice ids (`S01`) and "
                                  f"subsystem ids (`S01_foundation`) are unrelated taxonomies "
                                  f"sharing a prefix (ADR-0105 §7)", got=sid,
                                  expected="S04_registry"))
    return out


def v_evidence(problems):
    out = []
    for code, claim, detail in problems:
        out.append(Diagnostic(SEMANTIC, code, detail, got=claim,
                              remediation="conflicting evidence at the same precedence ESCALATES; "
                                          "the agent must not choose (ADR-0105 §2)"
                                          if code == "I2" else ""))
    return out


def v_dependency(unverifiable):
    """The family that exists so 'I could not tell' can never be read as 'nothing was wrong'."""
    return [Diagnostic(MISSING, "UNVERIFIABLE", u,
                       remediation="an unavailable input is never authorization; resolve the input "
                                   "or record why the obligation does not apply")
            for u in unverifiable]
