"""Load the registry (YAML) and validate it against `.github/ci-control-registry.schema.json`.

The schema is the sole source of shape truth — field set, id pattern, enums,
required-implies-context, unique rows. Nothing here restates it.

DO NOT make the jsonschema import optional. It is at module scope so an absent validator is an
ImportError that stops the lane. A guarded import here means validation silently does not happen.

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
