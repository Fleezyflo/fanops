"""Load the registry (YAML) and shape-check it. ONE validator, no optional dependency.

There used to be two, and the bigger one was switched off. A 156-line draft-07 JSON schema sat
beside these checks enforced by NOTHING: `jsonschema` was declared in neither pyproject.toml nor
requirements/, and the loader returned [] when it was absent, so in CI the schema was inert and this
hand-roll was the only thing running. The fail-open is precisely why nobody noticed.

The fix is not to install the schema — it is to stop keeping two. The registry is ~130 lines and
each control carries a handful of fields; a second declarative copy of that shape, plus a
hash-pinned dependency in a `--require-hashes` lane, costs more than it catches. Everything the
schema uniquely asserted now lives here, in the language the rest of the checks are already written
in: `additionalProperties: false` -> _ALLOWED_CONTROL_FIELDS, the id `pattern` -> _ID_RE, the enums
-> _CLASSES/_STATUSES, `required` -> _REQUIRED_CONTROL_FIELDS, and `required implies
branch_protection_context` -> the last check in shape_findings. Duplicate control id, which draft-07
cannot express at all, was already only here.

PyYAML is present in the CI unit lane (requirements/ci-unit.txt) and is the only dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .common import REGISTRY, Finding

_REQUIRED_CONTROL_FIELDS = ("id", "classification", "workflow", "job")
# The CLOSED set — `additionalProperties: false`, which the schema asserted and CI never ran. A field
# not named here is a typo or a leftover, never an extension. Widening it must be a deliberate edit;
# that is the point, because an unread field is exactly what accumulates silently otherwise.
#
# ENTRY CONDITION — and it is NOT "a check reads it". A registry may carry documentation a parser
# never touches: who owns a control, why deleting it would hurt. Those earn their place without code.
# The bar is: NO UNVERIFIED CLAIM, NO UNSYNCED DUPLICATE, NO POINTER TO SOMETHING DELETED. A field
# that asserts something nothing confirms is decoration, and decoration in a governance file is the
# failure this module exists to catch.
#
# The 16-field shape failed that bar, not an arithmetic one:
#   timeout_minutes, concurrency  copies of values DC-6 reads from the WORKFLOW, with nothing syncing
#                                 the two — drift-bait that reads as authority.
#   failure_evidence              claimed every control had a proving negative control. NOTHING checked
#                                 the claim. The real mechanism is selftest.py::CONTROLS plus
#                                 test_every_blocking_condition_has_a_negative_control.
#   adr                           mandatory on all 27, naming documents deleted with the prose layer.
#   step, command, name           unverified copies of ci.yml / the job's own `name:`.
#   invariant                     read only by DC-5, which went with the duplicate_groups it policed.
#   status                        one non-`active` value, marking a de-duplication this file's own
#                                 duplicate_groups block declared DEAD. A column with one value.
# deletion_consequence was the one worth keeping, so it MOVED — to comments on the ci.yml steps it
# describes, where it sits next to the thing it explains and cannot drift from it.
_ALLOWED_CONTROL_FIELDS = frozenset(_REQUIRED_CONTROL_FIELDS) | {"branch_protection_context"}
_CLASSES = {"required", "advisory", "scheduled"}
# STABLE identity — never a mutable job display name.
_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]+$")


def load_registry(path: Path = REGISTRY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
        unknown = sorted(set(c) - _ALLOWED_CONTROL_FIELDS)
        if unknown:
            out.append(Finding("SCHEMA", cid, f"unknown field(s): {unknown} — typo, or a field left "
                                              f"behind by a strip; no check reads it", True))
        if not _ID_RE.match(str(cid)):
            out.append(Finding("SCHEMA", cid, f"id {cid!r} is not a stable upper-case identifier "
                                              f"(^[A-Z][A-Z0-9-]+$)", True))
        if cid in seen:
            out.append(Finding("SCHEMA", cid, "duplicate control id", True))
        seen.add(cid)
        if c.get("classification") not in _CLASSES:
            out.append(Finding("SCHEMA", cid, f"classification {c.get('classification')!r} invalid", True))
        if c.get("classification") == "required" and not c.get("branch_protection_context"):
            out.append(Finding("SCHEMA", cid, "required top-level control has no branch_protection_context", True))
    return out
