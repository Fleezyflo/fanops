"""The Change-Contract compiler and verifier — ADR-0105 Phase 3.

The compiler derives the fields a contract can derive; the verifier checks a written contract
against a real diff at a real head and returns exactly one of six decisions. Nothing here is a CI
gate: `python -m tools.contract` is read-only, adds no workflow, and ADR-0105 §9 keeps enforcement a
Phase 6 decision.

DEPENDENCY DIRECTION — the one thing to preserve when editing this package.

    tools/contract  ->  tools/arch      (impact, verifymap, derived artifacts, drift, render)
    tools/contract  ->  tools/ci        (control ids, for ADR-0105 §9 V3)
    tools/arch      -/->  tools/contract
    tools/ci        -/->  tools/contract

`tools/ci/__init__.py:5` states the sibling invariant: *"Nothing here imports tools/arch and nothing
there imports this."* That invariant is why this is a THIRD package rather than a subpackage of
either sibling. The compiler needs `tools.arch`'s impact analysis AND `tools.ci`'s control ids;
placing it under `tools/arch/contract/` would have forced `tools/arch` to import `tools/ci` and
broken the invariant. A third package importing both preserves it — neither sibling gains an edge.

`NC-C28` asserts the two reverse edges do not exist. It is an AST scan, not a convention.
"""
