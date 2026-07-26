"""Load the registry (YAML) and validate it against the JSON schema. ONE validator, and it RUNS.

The schema is the source of shape truth: `.github/ci-control-registry.schema.json`. It is declarative,
it is what an editor can validate the file against live while you type (point a YAML language server
at it), and it cannot have bugs in its own control flow the way hand-written checks can.

It was inert until 2026-07-26. `jsonschema` was declared in neither pyproject.toml nor requirements/,
and this module skipped validation when the import was missing — so in CI the schema had NEVER ONCE
executed and a 24-line hand-rolled subset was the only thing running. The repair is the obvious one
and it is deliberately not clever: declare the dependency, and import it AT MODULE SCOPE so a missing
validator is an ImportError that stops the lane, never a silent pass. An optional validator is not a
validator; it is a fail-open with a docstring.

NOTHING here is hand-written. Closed field set, id pattern, enums, required-implies-context, unique
rows — all of it is stated once, in the schema. `shape_findings` runs the validator and maps its
errors to Findings; that is the entire function.

Residual, named rather than hidden: `uniqueItems` compares WHOLE objects, so two controls sharing an
`id` while differing in any other field are not rejected here. The structural fix is to key `controls`
by id (an object, not an array), which makes the state unrepresentable instead of merely detected —
a larger change to every consumer in checks.py, not a validator tweak.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

from .common import REGISTRY, SCHEMA, Finding


def load_registry(path: Path = REGISTRY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_schema(path: Path = SCHEMA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def shape_findings(reg: dict) -> list[Finding]:
    """Validate the registry against the schema. That is the whole function.

    Every message carries the failing JSON path, so a finding points at the offending row and field
    rather than at the file.
    """
    validator = jsonschema.Draft7Validator(load_schema())
    return [Finding("SCHEMA", "/".join(map(str, e.path)) or "-", e.message, True)
            for e in sorted(validator.iter_errors(reg), key=lambda err: list(err.path))]
