"""R15 — human and structured rendering, and the exit-class mapping (operator decision D-8).

These functions COMPUTE strings and never write them, so a test can assert on output without
invoking the CLI. It is the same reason `tools/arch/render.py::expected()` is split from `cmd_docs`:
a renderer that prints is a renderer you can only test by capturing stdout, and output assertions
made through a capture are the first thing to rot.

THE EXIT CLASS ENCODES TRUST; THE DECISION TRAVELS IN THE PAYLOAD. Three classes, and exit 2 is
reserved for "no trustworthy decision was reached" and is produced by no decision at all. A caller
that mapped exit 2 to "advisory success" would convert a tool failure into a pass, so exit 2 emits
NO `decision` field — there is nothing to misread (`NC-C15`).
"""
from __future__ import annotations

import json

from .model import EXIT_UNTRUSTWORTHY, Decision

_ICON = {"continue": "✅", "clarification_required": "❓", "expanded_authorization_required": "🔓",
         "stop": "🛑", "escalate": "⬆️", "refuse": "⛔"}

_ORDER = {"MISSING": 0, "MALFORMED": 1, "UNSUPPORTED": 2, "UNKNOWN": 3, "SEMANTIC": 4}


def payload(decision: Decision, *, contract_id: str, digest: str, traits, risk_tier: str,
            gates, state: str) -> dict:
    return {
        "decision": decision.outcome,
        "exit_class": decision.exit_class,
        "rule": decision.rule,
        "phase": decision.phase,
        "why": decision.why,
        "next_actor": decision.next_actor,
        "contract_id": contract_id,
        "declaration_digest": digest,
        "state": state,
        "traits": sorted(traits),
        "risk_tier": risk_tier,
        "gates": {"content_approval": gates.content_approval,
                  "exact_head_approval": gates.exact_head_approval,
                  "acceptance": gates.acceptance},
        "diagnostics": [{"kind": d.kind, "code": d.code, "detail": d.detail, "at": d.located(),
                         "path": d.path, "got": d.got, "expected": d.expected,
                         "remediation": d.remediation, "evidence": list(d.evidence)}
                        for d in _sorted(decision.diagnostics)],
    }


def untrustworthy(reason: str, detail: str = "") -> dict:
    """Exit 2's payload. NOTE WHAT IS ABSENT: no `decision` key, at any nesting level."""
    return {"exit_class": EXIT_UNTRUSTWORTHY, "error": reason, "detail": detail,
            "note": "no trustworthy decision was reached; this is not an advisory pass and must "
                    "never be converted to `continue`"}


def as_json(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def _sorted(diags):
    """Deterministic: by class, then source position, then code. Never by dict order."""
    return sorted(diags, key=lambda d: (_ORDER.get(d.kind, 9), d.line, d.column, d.code, d.path))


def render(decision: Decision, *, contract_id: str, digest: str, traits, risk_tier: str, gates,
           state: str, derived=None) -> str:
    icon = _ICON.get(decision.outcome, "•")
    L = [f"## {icon} `{decision.outcome}`  —  rule `{decision.rule}`  ({decision.phase})", "",
         f"**{decision.why}**", "",
         f"- contract: `{contract_id or '<unparsed>'}`",
         f"- declaration digest `D`: `{digest}`",
         f"- derived state: `{state}`",
         f"- traits: {', '.join(f'`{t}`' for t in sorted(traits)) or '`contained` (empty set)'}",
         f"- risk_tier: `{risk_tier}` — selects the breach response ONLY, never the obligations",
         f"- gates: content `{gates.content_approval}` · exact-head `{gates.exact_head_approval}` "
         f"· acceptance `{gates.acceptance}`", ""]

    if decision.outcome == "refuse":
        L += ["> **Refusal is a first-class successful outcome** (ADR-0105 §10). A contract "
              "terminating in `refused` with a recorded reason has done its job.", ""]

    if derived is not None:
        if derived.unverifiable:
            L += ["### Inputs that could not be resolved", "",
                  "*Unavailable is never authorized — these are why no `continue` is possible.*", ""]
            L += [f"- {u}" for u in derived.unverifiable] + [""]
        fired = [t for t in derived.triggers if t.fired]
        L += ["### Triggers", ""]
        L += [f"- **{t.id}** — {t.reason}" for t in fired] or ["- none fired"]
        L.append("")
        if derived.obligations:
            L += ["### Obligations (the UNION over the trait set)", ""]
            L += [f"- `{oid}` — {what}" for oid, what in derived.obligations] + [""]
        if derived.unauthorized:
            L += ["### Unauthorized surfaces", ""]
            L += [f"- `{p}`" for p in derived.unauthorized] + [""]

    diags = _sorted(decision.diagnostics)
    if diags:
        L += ["### Diagnostics", ""]
        for d in diags[:40]:
            where = f" `{d.path}`" if d.path else (f" ({d.located()})" if d.line else "")
            L.append(f"- **{d.code}**{where} — {d.detail}")
            if d.expected and d.got:
                L.append(f"  - got `{d.got}` · expected `{d.expected}`")
            if d.remediation:
                L.append(f"  - fix: {d.remediation}")
        if len(diags) > 40:
            L.append(f"- … and {len(diags) - 40} more")
        L.append("")

    L += ["<sub>Generated by `python -m tools.contract`. Read-only: this tool writes nothing, adds "
          "no CI job, and enforces nothing — ADR-0105 §9 leaves enforcement to Phase 6.</sub>"]
    return "\n".join(L)


def render_template() -> str:
    """The declaration skeleton, printed to STDOUT so nothing is ever written into the repository.

    A tool that could write the artifact it validates would let an agent satisfy the gate by editing
    the evidence. Every verb here is read-only for that reason — deliberately unlike `tools/arch`,
    whose `regen`, `docs` and `baseline --accept` all write.
    """
    return """---
id: CC-YYYY-MM-DD-slug
traits: []
authorized_actions: [design]
incidental_allowlist: []
blast_radius: []
invariants: []
stop_conditions: []
supersedes: []
---

# CC-YYYY-MM-DD-slug

### objective

What this change is for, in prose.

### success_condition

What would be TRUE if it worked and FALSE if it did not — name something observable.

### rollback

How this is undone, and at what cost.

### authority

| id | source_file | blob_sha |
|---|---|---|
| ADR-0105 | docs/adr/0105-reusable-change-contract-architecture.md | <git rev-parse HEAD:path> |

### owners

| subsystem_id | why_touched |
|---|---|
| S01_foundation | … |

### allowed_scope

| glob | why | basis |
|---|---|---|
| src/fanops/example.py | … | declared |

### prohibited_scope

| glob | why |
|---|---|
| .github/workflows/** | no workflow change |

### expected_surfaces

| path | kind | why |
|---|---|---|
| src/fanops/example.py | MODIFIED | … |

### coupling

| what | must_move_with | why |
|---|---|---|

### reusable_evidence

| claim | proven_by | proven_at | binding |
|---|---|---|---|

### verification

| obligation_id | control_or_requirement | distinct_boundary |
|---|---|---|
| OB-ARCH-CI | python -m tools.arch ci | regeneration byte-compare |

## Lifecycle

| timestamp | event | values |
|---|---|---|
| YYYY-MM-DDTHH:MM:SSZ | created | id=CC-YYYY-MM-DD-slug; base_sha=<sha> |
"""
