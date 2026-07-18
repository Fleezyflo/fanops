"""R14 — the verdict. TWO STAGES, ONE ORDERED TABLE, FIRST MATCH WINS, TOTAL AND PURE.

This module imports `model` and nothing else. No I/O, no clock, no network, no `tools.arch`, no
`tools.ci` — not as a style preference but because `AC-3` (the golden table reproduces
byte-identically) and `AC-4` (exactly one of six outcomes, never raises) are only worth asserting if
the function genuinely has no hidden inputs. Adding an import here would not break a test; it would
quietly make two passing tests stop meaning what they say.

REFUSAL IS A FIRST-CLASS SUCCESSFUL OUTCOME (ADR-0105 §10). `refuse` is a decision this function
returns, never an exception it raises and never an error the CLI reports. A contract terminating in
`refused` with a recorded reason has done its job.

DECLARED CONDITIONS. Four rows depend on facts no tool can compute — that two authorities at the
same precedence conflict, that the task requires exceeding a `LAW-*`, that the right change lies
outside `allowed_scope`, that a success condition cannot be made falsifiable. Rather than invent a
field per rule, all four reuse `stop_conditions` (§3.1 #18, "task-specific additions") under ONE
convention: an entry beginning `<RULE-ID>:` declares that condition holds. One convention, no new
field, and the 19-slot model is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .model import (CLARIFICATION, CONTINUE, ESCALATE, EXPANDED, REFUSE, STOP, Decision,
                    DecisionInput)

PRE, HEAD, MERGE = "pre-implementation", "at-head", "merge-gate"
_ALL = (PRE, HEAD, MERGE)


@dataclass(frozen=True)
class Rule:
    id: str
    outcome: str
    phases: tuple[str, ...]
    predicate: Callable[[DecisionInput], bool]
    why: str
    actor: str


def _codes(di: DecisionInput) -> set[str]:
    return {d.code for d in di.diagnostics}


def _declared(di: DecisionInput, rule_id: str) -> bool:
    """A `stop_conditions` entry beginning `<RULE-ID>:` — the declared-condition convention."""
    for entry in (di.declaration.value("stop_conditions") or []):
        if isinstance(entry, str) and entry.strip().startswith(f"{rule_id}:"):
            return True
    return False


def _live(di: DecisionInput) -> bool:
    return "live" in di.derived.traits


def _execution_gate_recorded(di: DecisionInput) -> bool:
    """ADR-0105 §1 T4 / §10: a live action needs a SEPARATE execution gate an operator must give.

    Recorded as `execution_gate=` on an `approved` event, so it is an operator act bound into the
    append-only history — not a declaration field the agent writing the contract could fill in for
    itself. That distinction is the whole content of the word "separate".
    """
    return any(e.kind == "approved" and e.get("execution_gate") for e in di.declaration.events)


# ── Stage A · admissibility ─────────────────────────────────────────────────────────────────
#
# A table cannot evaluate a contract that did not parse, and `unauthorized` is meaningless before
# `expected_surfaces` exists. Running Stage B on an inadmissible contract would produce a confident
# verdict about a document nobody has successfully read.
_PARSE_FAIL = {"NO-BOUNDARY", "MULTI-BOUNDARY", "UNSUP-CRLF", "NO-FRONTMATTER",
               "UNCLOSED-FRONTMATTER", "UNSUP-MULTIDOC", "UNSUP-TAB", "UNSUP-MERGE", "UNSUP-ANCHOR",
               "UNSUP-ALIAS", "UNSUP-TAG", "UNSUP-BLOCK-SCALAR", "UNSUP-FLOW-MAP", "UNSUP-COMMENT",
               "UNSUP-NESTED", "DUP-KEY", "BAD-KEY", "NO-COLON", "ORPHAN-ITEM", "BAD-COLUMNS",
               "BAD-ROW", "NO-TABLE", "BAD-EVENT-COLUMNS", "BAD-EVENT-ROW", "WRONG-LOCATION"}
_LIFECYCLE_FAIL = {"EVENT-KIND", "EVENT-TIME", "EVENT-ORDER", "EVENT-AFTER-TERMINAL",
                   "ACCEPT-INCOMPLETE", "DECL-DIVERGED", "LIFECYCLE-REWRITTEN"}

STAGE_A: tuple[Rule, ...] = (
    Rule("A1", CLARIFICATION, _ALL, lambda di: bool(_codes(di) & _PARSE_FAIL),
         "the contract did not parse", "operator"),
    Rule("A2", CLARIFICATION, _ALL, lambda di: "UNKNOWN-KEY" in _codes(di)
         or "UNKNOWN-SECTION" in _codes(di),
         "the contract carries a field outside the closed set", "operator"),
    Rule("A3", CLARIFICATION, _ALL, lambda di: bool(_codes(di) & {"FIELD-MISSING", "FIELD-EMPTY"}),
         "an unconditionally-mandatory field is absent or empty", "operator"),
    Rule("A4", CLARIFICATION, _ALL, lambda di: bool(_codes(di) & {"ID-FORMAT", "ID-FILENAME"}),
         "`id` is malformed or does not match its filename", "operator"),
    Rule("A5", STOP, _ALL, lambda di: bool(_codes(di) & _LIFECYCLE_FAIL),
         "the lifecycle record is invalid, reordered, or the landed declaration was edited — "
         "§3.6 governance-sensitive, not a formatting mistake", "operator"),
)

# ── Stage B · severity-ordered, first match wins ────────────────────────────────────────────
#
# `RF-2` precedes `ST-1` because ADR-0105 §10 splits exactly that row by `risk_tier`. Rules 1–2 are
# the INTENDED SOLE CONSUMERS of `risk_tier`; `NC-C12` adds a second read elsewhere and must go red.
#
# `EA-1` and `ST-1` are the same fact at two different times: before the file is written it is a
# request for authorization, after it is a scope breach. The phase column is what keeps them apart,
# so an agent cannot write first and reclassify afterwards.
STAGE_B: tuple[Rule, ...] = (
    Rule("RF-1", REFUSE, _ALL, lambda di: _live(di) and not _execution_gate_recorded(di),
         "a `live` change has no separate execution gate", "operator"),
    Rule("RF-2", REFUSE, (HEAD, MERGE),
         lambda di: di.derived.risk_tier == "live" and bool(di.derived.unauthorized),
         "a `live` change touched a file outside its declared scope", "operator"),
    Rule("RF-3", REFUSE, _ALL,
         lambda di: any(e.kind == "refused" for e in di.declaration.events),
         "the contract records a terminal `refused` event", "—"),
    Rule("RF-4", REFUSE, (PRE,), lambda di: "UNFALSIFIABLE" in _codes(di) and _declared(di, "RF-4"),
         "`success_condition` is not falsifiable and is declared unfixable", "—"),

    Rule("ES-1", ESCALATE, _ALL, lambda di: _declared(di, "ES-1"),
         "two same-precedence authorities conflict — the agent must not choose", "operator"),
    Rule("ES-2", ESCALATE, _ALL, lambda di: _declared(di, "ES-2"),
         "the task requires exceeding a `LAW-*`; the path is amendment (C18.1), not a contract "
         "field", "operator"),
    Rule("ES-3", ESCALATE, _ALL, lambda di: "I2" in _codes(di),
         "conflicting evidence at the same precedence", "operator"),

    Rule("ST-1", STOP, (HEAD, MERGE),
         lambda di: bool(di.derived.unauthorized) and di.derived.risk_tier != "live",
         "the diff contains an `unauthorized` file", "operator"),
    # A cited authority whose FILE IS GONE is the strongest form of "changed", not a lesser one, so
    # `AUTH-MISSING-FILE` lands here rather than in a softer row. Without it a deleted authority
    # produced `continue`, which `NC-C14` caught.
    Rule("ST-2", STOP, _ALL,
         lambda di: bool(_codes(di) & {"AUTH-BLOB-MOVED", "AUTH-MISSING-FILE"}),
         "a cited authority changed after approval, or its file no longer exists", "operator"),
    Rule("ST-3", STOP, (HEAD, MERGE), lambda di: di.gates.content_approval != "satisfied",
         "no `approved` event names the current declaration digest `D`", "operator"),
    Rule("ST-4", STOP, (MERGE,), lambda di: di.gates.exact_head_approval != "satisfied",
         "no pull-request review approves the exact current head", "operator"),
    Rule("ST-5", STOP, (HEAD, MERGE), lambda di: "GENERATED-NOT-REPRODUCIBLE" in _codes(di),
         "a generated consequence is not reproduced by regeneration", "agent"),
    Rule("ST-6", STOP, _ALL, lambda di: bool(_codes(di) & {"I1", "I4", "EV-SHAPE"}),
         "reused evidence is invalid under `I1` / `I4` / `I5`", "agent"),
    Rule("ST-7", STOP, _ALL, lambda di: bool(di.derived.unverifiable),
         "a required input was unavailable — unavailable is never authorized", "operator"),
    Rule("ST-8", STOP, (HEAD, MERGE), lambda di: bool(_codes(di) & {"GS-1", "GS-2"}),
         "a governance surface is not covered by the ADR-0105 §1 T3 list", "operator"),

    Rule("EA-1", EXPANDED, (PRE,), lambda di: _declared(di, "EA-1"),
         "the correct change lies outside `allowed_scope` — never widen unilaterally", "operator"),

    Rule("CL-1", CLARIFICATION, _ALL, lambda di: "TRAIT-CONDITIONAL" in _codes(di),
         "a trait-conditional mandatory field is absent", "operator"),
    Rule("CL-2", CLARIFICATION, _ALL,
         lambda di: di.declaration.traits != di.derived.traits and di.declaration.present("traits"),
         "the declared trait set differs from the derived one", "operator"),
    Rule("CL-3", CLARIFICATION, _ALL, lambda di: "UNFALSIFIABLE" in _codes(di),
         "`success_condition` names nothing observable", "operator"),
    # The approved design's failure model requires `F3b` — *"a cited id does not exist → S6 →
    # clarification"* — but its Stage-B table numbers no row for it, so the outcome it specifies had
    # nowhere to come from. This is that row and nothing more: same stage, same outcome, no new
    # concept. An id in the wrong namespace, or a control id in no registry row, is a question for
    # the author, not a halt.
    Rule("CL-4", CLARIFICATION, _ALL,
         lambda di: bool(_codes(di) & {"AUTH-UNKNOWN", "AUTH-NAMESPACE"}),
         "a cited authority id is unknown or is not a recognised namespace", "operator"),
)

RULES: tuple[Rule, ...] = STAGE_A + STAGE_B
RULE_IDS: tuple[str, ...] = tuple(r.id for r in RULES)


def decide(di: DecisionInput) -> Decision:
    """Exactly one of six outcomes. Total: the default row cannot be reached past, and cannot raise.

    A predicate that raises would turn a governance verdict into a stack trace, and a stack trace is
    not a decision. `_safe` converts that into `stop` naming the rule, which is the only honest
    answer available: the rule could not be evaluated, so continuation is not authorized.
    """
    for rule in RULES:
        if di.phase not in rule.phases:
            continue
        hit, err = _safe(rule, di)
        if err is not None:
            return Decision(STOP, rule.id, f"rule {rule.id} could not be evaluated: {err}",
                            "operator", di.phase, di.diagnostics)
        if hit:
            return Decision(rule.outcome, rule.id, rule.why, rule.actor, di.phase, di.diagnostics)
    return Decision(CONTINUE, "OK", "within declared scope; authority clear", "agent", di.phase,
                    di.diagnostics)


def _safe(rule: Rule, di: DecisionInput) -> tuple[bool, str | None]:
    try:
        return bool(rule.predicate(di)), None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
