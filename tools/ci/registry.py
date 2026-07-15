"""Load the registry (YAML) and schema (JSON); a dependency-light shape check.

PyYAML is already present in the CI unit lane (requirements/ci-unit.txt). jsonschema is used when
available for full coverage, but the core shape checks always run so a missing jsonschema can never
produce a false pass.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

from .common import REGISTRY, SCHEMA, Finding

_REQUIRED_CONTROL_FIELDS = ("id", "name", "invariant", "owner", "classification", "trigger",
                            "justification", "deletion_consequence", "adr", "failure_evidence",
                            "status")
_CLASSES = {"required", "advisory", "scheduled", "local"}
_STATUSES = {"active", "transitional", "deprecated", "dormant"}


def load_registry(path: Path = REGISTRY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_schema(path: Path = SCHEMA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonschema_findings(reg: dict) -> list[Finding]:
    """Full schema validation when jsonschema is installed; [] when it is not (the core shape
    checks below always run, so absence can never be a false pass). find_spec avoids a swallow."""
    if importlib.util.find_spec("jsonschema") is None:
        return []
    import jsonschema
    validator = jsonschema.Draft7Validator(load_schema())
    return [Finding("SCHEMA", "/".join(map(str, e.path)) or "-", e.message, True)
            for e in sorted(validator.iter_errors(reg), key=lambda err: list(err.path))]


def shape_findings(reg: dict) -> list[Finding]:
    out: list[Finding] = []
    controls = reg.get("controls")
    if not isinstance(controls, list) or not controls:
        return [Finding("SCHEMA", "-", "registry has no controls list", True)]
    if not reg.get("intended_required_contexts"):
        out.append(Finding("SCHEMA", "-", "registry has no intended_required_contexts", True))
    seen: set[str] = set()
    for c in controls:
        cid = c.get("id", "<unnamed>")
        missing = [f for f in _REQUIRED_CONTROL_FIELDS if f not in c]
        if missing:
            out.append(Finding("SCHEMA", cid, f"missing required field(s): {missing}", True))
        if cid in seen:
            out.append(Finding("SCHEMA", cid, "duplicate control id", True))
        seen.add(cid)
        if c.get("classification") not in _CLASSES:
            out.append(Finding("SCHEMA", cid, f"classification {c.get('classification')!r} invalid", True))
        if c.get("status") not in _STATUSES:
            out.append(Finding("SCHEMA", cid, f"status {c.get('status')!r} invalid", True))
        if c.get("classification") == "required" and not c.get("parent") and not c.get("branch_protection_context"):
            out.append(Finding("SCHEMA", cid, "required top-level control has no branch_protection_context", True))
    out.extend(_jsonschema_findings(reg))
    return out
