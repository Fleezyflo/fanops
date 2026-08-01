"""The control-registry schema, and the generator that emits it.

`.github/ci-control-registry.schema.json` is a GENERATED artifact — a pure function of this module.
Hand-editing it is caught by test_schema_is_not_stale in the required unit lane, the same
byte-compare discipline tools/arch applies to its derived artifacts.

Regenerate: python -m tools.ci regen

The shape is declared ONCE, here. A field is added by naming it in _CONTROL_FIELDS; the schema, its
`required` list and its closed-field set all follow, so they cannot disagree the way hand-kept
copies do.
"""
from __future__ import annotations

import json

from .common import SCHEMA

REGEN_CMD = "python -m tools.ci regen"

CLASSIFICATIONS = ("required", "advisory", "scheduled")

# field -> (required?, JSON Schema fragment). The ONE declaration of a control's shape.
_CONTROL_FIELDS: dict[str, tuple[bool, dict]] = {
    "id": (True, {
        "type": "string",
        "pattern": "^[A-Z][A-Z0-9-]+$",
        "description": "STABLE identity — never a mutable job display name. Renaming a job MUST NOT change this id.",
    }),
    "classification": (True, {
        "type": "string",
        "enum": list(CLASSIFICATIONS),
        "description": "required = in live branch protection, and blocks; advisory = runs on PR, reports, does not block (DC-7 additionally requires `continue-on-error: true` on the job); scheduled = cron/dispatch only.",
    }),
    "workflow": (True, {
        "type": "string",
        "pattern": r"^\.github/workflows/[a-z0-9-]+\.yml$",
        "description": "Path to the implementing workflow file.",
    }),
    "job": (True, {
        "type": "string",
        "minLength": 1,
        "description": "Workflow job id — the YAML key under `jobs:`, stable within the file. NOT the display name.",
    }),
    "branch_protection_context": (False, {
        "type": "string",
        "minLength": 1,
        "description": "The exact check-run context string, i.e. the job's `name:`. MUST be present when classification == 'required' — the anti-silent-detach mechanism DC-1 reads. Optional otherwise, and worth setting on an advisory job so DC-4 can catch prose that miscalls it.",
    }),
}

_TOP_LEVEL: dict[str, tuple[bool, dict]] = {
    "version": (True, {"type": "integer", "minimum": 1}),
    "status": (True, {
        "type": "string",
        "enum": ["proposed", "accepted", "superseded"],
        "description": "Lifecycle of the registry document itself.",
    }),
    "required_contexts": (True, {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "uniqueItems": True,
        "minItems": 1,
        "description": "The required branch-protection contexts. DC-3 requires LIVE GitHub protection to equal this exactly — change both in the same PR. DC-1 requires each entry to match a real workflow job name, and each must be the branch_protection_context of a control classified 'required'.",
    }),
    "required_security_settings": (True, {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "uniqueItems": True,
        "minItems": 1,
        "description": "Repository security settings that MUST be enabled in live GitHub, named exactly as the keys of the repo object's `security_and_analysis`. DC-9 reads each one and reports any that is not 'enabled'. This is the only declaration of a setting that lives nowhere in the tree — no static check can see it, so an operator switching one off is otherwise silent.",
    }),
    "controls": (True, {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"$ref": "#/definitions/control"},
        "description": "One row per workflow job. DC-2 enforces the bijection in both directions, so this list is exhaustive by construction: adding a job without a row reddens the required unit lane. The set of `workflow` values is ALSO the declaration DC-8 reconciles against live workflow enablement — a declared workflow disabled in GitHub is declared CI that cannot run.",
    }),
}


def _closed(fields: dict[str, tuple[bool, dict]], **extra) -> dict:
    """A closed object: every declared field, and nothing else."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [k for k, (req, _) in fields.items() if req],
        "properties": {k: frag for k, (_, frag) in fields.items()},
        **extra,
    }


def build() -> dict:
    control = _closed(_CONTROL_FIELDS, allOf=[{
        "$comment": "A required control MUST declare its branch-protection context. DC-1 compares that string against real workflow job names, so an unmirrored rename is caught instead of silently detaching the gate.",
        "if": {"properties": {"classification": {"const": "required"}}, "required": ["classification"]},
        "then": {"required": ["branch_protection_context"]},
    }])
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://fanops/ci-control-registry.schema.json",
        "$comment": f"GENERATED — do not hand-edit. Regenerate with `{REGEN_CMD}`.",
        "title": "FanOps CI Control Registry",
        "description": "Validation schema for .github/ci-control-registry.yml, enforced by tools/ci/registry.py on every `tools.ci static` and every run of tests/test_ci_registry_validator.py in the required unit lane. A validation failure names the exact path and constraint.",
        **_closed(_TOP_LEVEL),
        "definitions": {"control": control},
    }


def render() -> str:
    """Deterministic bytes: sorted keys, fixed indent, trailing newline — the contract
    tools/arch/common.dumps uses, so a byte-compare asks a real question about the source."""
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write() -> bool:
    """Write the artifact. True when the bytes changed."""
    fresh = render()
    if SCHEMA.exists() and SCHEMA.read_text(encoding="utf-8") == fresh:
        return False
    SCHEMA.write_text(fresh, encoding="utf-8")
    return True
